"""A drawing converted the whole way, to the .glb and metadata the viewer reads.

Skipped without OCCT, like the other geometry tests. What is checked here is
that a part recovered from a drawing is a first class B-rep -- exact volume,
real faces, face groups that tile the triangles -- and that it is labelled as
derived so nothing downstream can take it for a modelled part.
"""

from __future__ import annotations

import json
from math import pi
from pathlib import Path

import pytest

from app.cad import drawing, occt

pytestmark = pytest.mark.skipif(
    not occt.available(), reason="OCCT bindings not installed"
)

FIXTURES = Path(__file__).parent / "fixtures"

# Both fixtures are drawn from figures chosen so the answer can be written down
# rather than recorded from a run.
STEPPED_VOLUME = pi * (10**2 * 30 + 15**2 * 40 + 8**2 * 20) - pi * 4**2 * 90
PLAIN_VOLUME = pi * 10**2 * 50


@pytest.fixture(scope="module")
def stepped(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("out") / "stepped.glb"
    drawing.convert(FIXTURES / "stepped_shaft.dxf", out)
    return json.loads(out.with_suffix(".json").read_text())


def test_the_volume_is_the_one_the_drawing_describes(stepped):
    assert stepped["parts"]["n1"]["volume_mm3"] == pytest.approx(STEPPED_VOLUME)


def test_a_plain_shaft_closed_by_the_axis_is_a_full_cylinder(tmp_path):
    out = tmp_path / "plain.glb"
    result = drawing.convert(FIXTURES / "plain_shaft.dxf", out)
    assert result.metadata.parts["n1"].volume_mm3 == pytest.approx(PLAIN_VOLUME)
    kinds = sorted(f.kind for f in result.metadata.snap["n1"].faces)
    assert kinds == ["cylinder", "plane", "plane"]


def test_the_faces_are_the_turned_features(stepped):
    kinds = sorted(f["kind"] for f in stepped["snap"]["n1"]["faces"])
    # Three turned diameters and the bore; two end faces and two shoulders.
    assert kinds == ["cylinder"] * 4 + ["plane"] * 4

    radii = sorted(
        round(f["radius"], 6)
        for f in stepped["snap"]["n1"]["faces"]
        if f["kind"] == "cylinder"
    )
    assert radii == [4.0, 8.0, 10.0, 15.0]


def test_it_is_labelled_derived_and_says_what_it_assumed(stepped):
    assert stepped["geometry_source"] == "derived"

    derived = stepped["derived"]
    assert derived["method"] == "dxf-revolve"
    assert derived["axis_direction"] == pytest.approx([1.0, 0.0, 0.0])
    assert derived["axis_point"][1] == pytest.approx(0.0)

    said = " ".join(derived["assumptions"])
    assert "solid of revolution" in said
    assert "centre line" in said
    assert "bored" in said

    # What was on the sheet and is not in the part, kept apart from what the
    # reading decided. They answer different questions and are shown as two
    # lists, so a tally of skipped dimensions does not read as an assumption.
    assert set(derived["ignored"]) == {
        "1 DIMENSION",
        "1 TEXT",
        "1 other closed outline on the sheet",
    }
    assert not any("DIMENSION" in note for note in derived["assumptions"])


def test_the_face_groups_tile_the_triangles(stepped):
    """The invariant the viewer's face picking rests on, checked here too."""
    groups = stepped["face_groups"]["n1"]
    assert len(groups) == len(stepped["snap"]["n1"]["faces"])
    assert groups[0][0] == 0
    for (_, end), (start, _) in zip(groups, groups[1:], strict=False):
        assert start == end


def test_a_derived_model_can_be_measured_like_any_other(stepped):
    snap = stepped["snap"]["n1"]
    assert snap["vertices"]
    assert snap["edges"]
    circles = [e for e in snap["edges"] if e["kind"] == "circle"]
    # Every change of diameter and every end of the bore is a circular edge,
    # carrying the radius from the B-rep rather than fitted to triangles.
    assert {round(e["radius"], 6) for e in circles} == {4.0, 8.0, 10.0, 15.0}


def test_a_step_file_is_still_read_as_brep(tmp_path):
    """The reader split must not have changed what an ordinary model reports."""
    out = tmp_path / "assembly.glb"
    result = occt.convert(FIXTURES / "assembly.step", out)
    assert result.metadata.geometry_source == "brep"
    assert result.metadata.derived is None
