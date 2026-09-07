"""What the worker writes down.

A worker's output is not always read by the people who are allowed the file it
is working on. This one runs as a CI job on a public repository, where the log
is readable by anyone, and it will run on a company's own server, where it is
not. The same code writes both, so it has to be safe in the more exposed of
them.

Part names are the customer's. They are in the database, where the people who
may see them already look; they have no business in a log.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app import worker

SECRET_NAME = "BK-09 BOSLUKSUZ SERVO KAPLIN"
SECRET_FILE = "BK-09 BOSLUKSUZ SERVO KAPLIN.STEP"


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.statements.append((sql, params))


class _Connection:
    def __init__(self) -> None:
        self.last = _Cursor()

    def cursor(self) -> _Cursor:
        self.last = _Cursor()
        return self.last


@pytest.fixture
def converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything outside the worker, stubbed: no file, no bucket, no OCCT."""
    metadata = SimpleNamespace(
        parts={"n1_1": object()},
        units="mm",
        declared_name=SECRET_NAME,
    )
    result = SimpleNamespace(triangle_count=1234, deflection=0.1, metadata=metadata)

    monkeypatch.setattr(worker, "download", lambda key, to: to)
    monkeypatch.setattr(worker, "upload", lambda path, key, kind: None)

    def convert(source, out, length_mm=None):
        CONVERTED_WITH.append(length_mm)
        return result

    CONVERTED_WITH.clear()
    monkeypatch.setattr(worker, "convert", convert)


# What the stubbed converter was asked for, one entry per call.
CONVERTED_WITH: list[float | None] = []


def _job(**extra: Any) -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "model_id": "22222222-2222-2222-2222-222222222222",
        "version_no": 1,
        "source_key": "proj/model/version/source.step",
        "source_filename": SECRET_FILE,
        "source_format": "step",
        "source_length_mm": None,
        **extra,
    }


def test_renaming_a_model_does_not_write_its_name_down(
    converted: None, caplog: pytest.LogCaptureFixture
) -> None:
    connection = _Connection()

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.process(connection, _job())

    written = "\n".join(record.getMessage() for record in caplog.records)

    # It happened -- the rename is in the database.
    assert "renamed model" in written
    # And it is not in the log.
    assert SECRET_NAME not in written
    assert SECRET_FILE not in written


def test_the_name_still_reaches_the_database(converted: None) -> None:
    # The point is to keep it out of the log, not to stop renaming.
    connection = _Connection()
    worker.process(connection, _job())

    renamed = [
        params for sql, params in connection.last.statements if "UPDATE models" in sql
    ]
    assert renamed and renamed[0][0] == SECRET_NAME


def test_the_log_still_says_which_job_it_was(
    converted: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Redacting is easy; redacting into uselessness is easier.

    The identifiers have to stay, or a failure in production cannot be traced
    back to the upload that caused it.
    """
    connection = _Connection()

    with caplog.at_level(logging.INFO, logger="worker"):
        worker.process(connection, _job())

    written = "\n".join(record.getMessage() for record in caplog.records)
    assert _job()["id"] in written
    assert _job()["model_id"] in written
    assert "1234 triangles" in written


def test_the_length_on_the_row_reaches_the_reader(converted):
    """The only way a printed sheet gets a size.

    It is asked for at the upload, written on the version row, and read back
    here. Dropped anywhere along that path the model still converts, still
    opens and is quietly the wrong size -- so this is the end of the thread,
    checked.
    """
    conn = _Connection()
    worker.process(conn, _job(source_length_mm=90.0))

    assert CONVERTED_WITH == [90.0]


def test_a_row_with_no_length_asks_for_none(converted):
    conn = _Connection()
    worker.process(conn, _job())

    assert CONVERTED_WITH == [None]


def test_the_queue_hands_back_exactly_what_the_worker_reads():
    """The claim query and the code that reads its result, held together.

    A column left out of `RETURNING` is not an error anywhere: the key is
    simply absent, `job.get` answers None, and the conversion goes ahead
    without whatever it was. That is how a length asked for at the upload
    disappears silently.
    """
    returning = worker.CLAIM_SQL.split("RETURNING", 1)[1]
    columns = {name.strip() for name in returning.replace("\n", " ").split(",")}

    assert columns == set(_job())
