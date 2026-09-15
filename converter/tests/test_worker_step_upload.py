"""The estimated STEP file, from the converter's output to the row that names it.

The file itself is written by `occt.export_step`, and what it says about itself
is tested next to that. What is tested here is the wiring in between: that a
derived conversion's STEP is uploaded, that the key is recorded on the version,
and -- the half that is easy to get wrong quietly -- that a conversion which
produced no STEP records no key rather than a stale or empty one.

Everything outside the worker is stubbed. No bucket, no database, no OCCT.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import worker

SOURCE_KEY = "proj/model/version/source.dxf"


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
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Records what was sent to storage, as (filename, key, content type)."""
    recorded: list[tuple[str, str, str]] = []

    monkeypatch.setattr(worker, "download", lambda key, to: to)
    monkeypatch.setattr(
        worker,
        "upload",
        lambda path, key, kind: recorded.append((Path(path).name, key, kind)),
    )
    return recorded


def stub_convert(monkeypatch: pytest.MonkeyPatch, step_path: str | None) -> None:
    metadata = SimpleNamespace(parts={"n1": object()}, units="mm", declared_name=None)
    result = SimpleNamespace(
        triangle_count=10,
        deflection=0.1,
        metadata=metadata,
        step_path=step_path,
    )
    monkeypatch.setattr(worker, "convert", lambda source, out, length_mm=None: result)


def _job() -> dict[str, Any]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "model_id": "22222222-2222-2222-2222-222222222222",
        # Not the first version, so the rename path stays out of the way.
        "version_no": 2,
        "source_key": SOURCE_KEY,
        "source_filename": "shaft.dxf",
        "source_format": "dxf",
        "source_length_mm": None,
    }


def succeeded(connection: _Connection) -> tuple[Any, ...]:
    for sql, params in connection.last.statements:
        if "status = 'ready'" in sql:
            return params
    raise AssertionError("the version was never marked ready")


def test_a_derived_conversion_uploads_its_step_beside_the_glb(
    monkeypatch: pytest.MonkeyPatch, uploads: list[tuple[str, str, str]]
) -> None:
    stub_convert(monkeypatch, "/tmp/whatever/estimated.step")

    worker.process(_Connection(), _job())

    keys = [key for _, key, _ in uploads]
    assert "proj/model/version/estimated.step" in keys

    # Beside the others, under the version that produced it -- not in a
    # directory of its own, and not overwriting a previous revision's.
    assert keys == [
        "proj/model/version/model.glb",
        "proj/model/version/metadata.json",
        "proj/model/version/estimated.step",
    ]


def test_the_key_is_recorded_on_the_version(
    monkeypatch: pytest.MonkeyPatch,
    uploads: list[tuple[str, str, str]],  # noqa: ARG001 - stubs storage away
) -> None:
    stub_convert(monkeypatch, "/tmp/whatever/estimated.step")
    connection = _Connection()

    worker.process(connection, _job())

    glb, metadata, step, _stats, version_id = succeeded(connection)
    assert glb == "proj/model/version/model.glb"
    assert metadata == "proj/model/version/metadata.json"
    assert step == "proj/model/version/estimated.step"
    assert version_id == "11111111-1111-1111-1111-111111111111"


def test_a_conversion_with_no_step_records_none(
    monkeypatch: pytest.MonkeyPatch, uploads: list[tuple[str, str, str]]
) -> None:
    # Which is every model read from a real solid, so this is the common case
    # rather than the edge one.
    stub_convert(monkeypatch, None)
    connection = _Connection()

    worker.process(connection, _job())

    assert succeeded(connection)[2] is None
    assert not any(key.endswith(".step") for _, key, _ in uploads)
