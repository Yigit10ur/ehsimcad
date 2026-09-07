"""The conversion queue.

Polls the database for queued versions, converts them and writes the result
back. There is no broker: at MVP volume a table plus `FOR UPDATE SKIP LOCKED`
is a correct queue, and one fewer moving part to run and debug.

    python -m app.worker
"""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.pipeline import UnsupportedFormatError, convert
from app.storage import download, sibling_key, upload

logger = logging.getLogger("worker")

# Claiming and releasing in one statement keeps two workers from taking the
# same row: the subquery locks it, and SKIP LOCKED sends the other worker to
# the next one instead of making it wait.
CLAIM_SQL = """
UPDATE model_versions
SET status = 'processing', claimed_at = now()
WHERE id = (
    SELECT id FROM model_versions
    WHERE status = 'queued'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, model_id, version_no, source_key, source_filename, source_format,
          source_length_mm
"""

# A worker that dies mid-job leaves its row claimed forever. Nothing else would
# ever notice, so the queue reclaims anything that has been processing for
# longer than a conversion could plausibly take.
REQUEUE_STALE_SQL = """
UPDATE model_versions
SET status = 'queued', claimed_at = NULL
WHERE status = 'processing'
  AND claimed_at < now() - make_interval(secs => %s)
RETURNING id
"""

SUCCEED_SQL = """
UPDATE model_versions
SET status = 'ready',
    glb_key = %s,
    metadata_key = %s,
    stats = %s,
    error_message = NULL,
    claimed_at = NULL
WHERE id = %s
"""

RENAME_SQL = """
UPDATE models
SET name = %s
WHERE id = %s
"""

FAIL_SQL = """
UPDATE model_versions
SET status = 'failed', error_message = %s, claimed_at = NULL
WHERE id = %s
"""


def _words(text: str) -> set[str]:
    """The alphanumeric words of a name, for comparing two spellings of it."""
    return {
        word.casefold()
        for word in re.split(r"[^0-9A-Za-z\u00c0-\u024f]+", text)
        if len(word) > 1
    }


def better_name(declared: str | None, filename: str | None) -> str | None:
    """The CAD file's own name for the model, when it is plainly the same name.

    A file name is whatever the operating system was holding. One upload here
    arrived as `BK-09 BO^LUKSUZ ALUM0NYUM SERVO KAPL0N`: something upstream had
    truncated each Turkish character to the low byte of its code point long
    before we saw the file. The name inside the file was intact and correctly
    encoded -- `BK-09 BOŞLUKSUZ SERVO KAPLİN` -- because the modeller wrote it
    there through a Unicode escape the standard defines for exactly this.

    So the point is to repair a damaged file name, not to overrule the person
    who chose it. The two have to be recognisably the same name, which is
    tested by sharing a word: the pair above share `bk`, `09` and `servo`.

    That test also disposes of a name no one chose. A STEP file with no product
    name of its own gets one from whatever wrote it -- OCCT's own writer leaves
    `Open CASCADE STEP translator 7.9 1` -- and a translator's version string
    has no words in common with anything a person would name a file.
    """
    if not declared or not filename:
        return None

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return declared if _words(declared) & _words(stem) else None


def connect() -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set; see .env.example")

    conn = psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)

    # psycopg promotes a repeated query to a prepared statement on its own,
    # which breaks against a transaction pooler: the pooler hands the next
    # statement to a different backend session, where that prepared name
    # either does not exist or already belongs to something else. The web
    # application disables them for the same reason.
    conn.prepare_threshold = None

    return conn


def requeue_stale(conn: psycopg.Connection) -> int:
    with conn.cursor() as cursor:
        cursor.execute(REQUEUE_STALE_SQL, (settings.stale_job_seconds,))
        rows = cursor.fetchall()
    if rows:
        logger.warning("requeued %d abandoned job(s)", len(rows))
    return len(rows)


def claim(conn: psycopg.Connection) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(CLAIM_SQL)
        return cursor.fetchone()


def process(conn: psycopg.Connection, job: dict[str, Any]) -> None:
    version_id = job["id"]
    source_key = job["source_key"]
    logger.info("converting %s (%s)", version_id, source_key)

    with tempfile.TemporaryDirectory(prefix="cad-") as workspace:
        root = Path(workspace)
        source = download(source_key, root / Path(source_key).name)
        out_glb = root / "model.glb"

        # Only a drawing has one, and only when whoever uploaded it knew. The
        # readers that cannot use it ignore it.
        result = convert(source, out_glb, length_mm=job.get("source_length_mm"))

        glb_key = sibling_key(source_key, "model.glb")
        metadata_key = sibling_key(source_key, "metadata.json")

        upload(out_glb, glb_key, "model/gltf-binary")
        upload(out_glb.with_suffix(".json"), metadata_key, "application/json")

        stats = {
            "triangleCount": result.triangle_count,
            "deflection": result.deflection,
            "partCount": len(result.metadata.parts),
            "units": result.metadata.units,
        }

    # Only on the first version: renaming a model on a later revision would
    # rename something people have been referring to by its old name.
    name = (
        better_name(result.metadata.declared_name, job["source_filename"])
        if job["version_no"] == 1
        else None
    )

    with conn.cursor() as cursor:
        cursor.execute(
            SUCCEED_SQL, (glb_key, metadata_key, json.dumps(stats), version_id)
        )

        if name:
            cursor.execute(RENAME_SQL, (name[:200], job["model_id"]))
            # The name itself is not logged. A worker may be running somewhere
            # whose output is readable by people who are not allowed the
            # contents of the file -- a CI job on a public repository is
            # exactly that -- and a part name is the customer's, not ours. It
            # is in the database, where the people who may see it already look.
            logger.info("renamed model %s from the file", job["model_id"])

    logger.info("done %s: %d triangles", version_id, result.triangle_count)


def fail(conn: psycopg.Connection, version_id: str, message: str) -> None:
    logger.error("failed %s: %s", version_id, message)
    with conn.cursor() as cursor:
        cursor.execute(FAIL_SQL, (message[:2000], version_id))


def run(drain: bool = False) -> None:
    """Poll the queue forever, or -- with `drain` -- until it is empty.

    Draining is how the converter runs without a server to run it on. A CI job
    is started when something is uploaded, converts everything that has
    accumulated and exits, so nothing is billed or maintained between uploads.
    The queue does not care where its workers live: two of them claiming rows
    through `FOR UPDATE SKIP LOCKED` behave the same whether they are two
    containers or two CI runs that happened to overlap.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        logger.info("stopping after the current job")
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # Refuse to start rather than accept jobs and fail every one of them. A
    # container that is up but cannot import OCCT looks healthy from outside
    # and quietly turns the queue into a list of failures.
    from app.cad import occt

    if not occt.available():
        raise SystemExit(
            "OCCT is not importable in this environment. "
            'Install it with: pip install -e ".[cad]", or use the Docker image.'
        )

    conn = connect()
    converted = 0

    if drain:
        logger.info("draining the queue")
    else:
        logger.info("worker started, polling every %.1fs", settings.poll_interval)

    while not stopping:
        try:
            requeue_stale(conn)
            job = claim(conn)
        except psycopg.Error:
            logger.exception("database error while claiming; retrying")
            if drain:
                raise
            time.sleep(settings.poll_interval)
            continue

        if job is None:
            if drain:
                break
            time.sleep(settings.poll_interval)
            continue

        converted += 1

        try:
            process(conn, job)
        except UnsupportedFormatError as error:
            fail(conn, job["id"], str(error))
        except Exception as error:  # noqa: BLE001 - the job must not take the worker down
            fail(conn, job["id"], f"{type(error).__name__}: {error}")

    if drain:
        logger.info("queue empty after %d job(s)", converted)

    conn.close()


if __name__ == "__main__":
    # `--drain` converts what is waiting and exits, for a runner that is
    # started per upload rather than left running.
    run(drain="--drain" in sys.argv[1:])
