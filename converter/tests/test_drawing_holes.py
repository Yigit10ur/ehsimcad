"""Circles, and the holes they cut.

A circle used to be thrown away at the door. The reason was sound and the
place was wrong: a full circle must never become the outline of a turned part,
because revolving one about a line through it builds a sphere and about a line
beside it a torus, and the drawing said neither. Refusing to read the entity at
all was how that was enforced, and the cost was that a plate's bolt holes were
not there to cut -- so a plate came back heavier than the part.

Now the circle is read and the rule is kept where it can see what it is looking
at. Which means two things have to hold at once, and both are covered here: a
circle is still never a profile, and a circle inside an outline is a hole.

The reading runs without OpenCascade. What measures a solid needs it and says
so.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing, occt

# A 100 x 60 plate, 8 thick, with two 12 diameter bolt holes.
PLATE_SECTION = 100 * 60
BORE_RADIUS = 6
THICKNESS = 8
HOLED_VOLUME = (PLATE_SECTION - 2 * math.pi * BORE_RADIUS**2) * THICKNESS


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _rect(msp, x0: float, y0: float, x1: float, y1: float) -> None:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(corners, corners[1:], strict=False):
        msp.add_line(a, b)


def _holed_plate(*, holes: bool = True):
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, 0, 100, 60)
    if holes:
        msp.add_circle((25, 30), BORE_RADIUS)
        msp.add_circle((75, 30), BORE_RADIUS)
    _rect(msp, 0, -20, 100, -20 + THICKNESS)
    return doc


def _shaft(circle=None):
    """A plain 50 by 20 shaft in section, optionally with a circle on it."""
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, -10, 50, 10)
    msp.add_line((-8, 0), (58, 0), dxfattribs={"layer": "CENTER"})
    if circle is not None:
        msp.add_circle(*circle)
    return doc


# --- a circle is geometry -------------------------------------------------


def test_a_circle_reaches_the_outline_now(tmp_path):
    """Four arcs rather than one curve, and where they are split is the point.

    A centre line drawn through the middle of a circle meets these at their
    ends. It never crosses one partway, which is the one thing the clipping
    cannot do.
    """
    doc = _document()
    msp = doc.modelspace()
    msp.add_circle((10, 10), 5)

    outline, _axes, _units, ignored = drawing.read_curves(_saved(doc, tmp_path))

    assert len(outline) == 4
    assert all(curve.is_arc for curve in outline)
    assert not any("CIRCLE" in note for note in ignored)


# --- and still never a profile --------------------------------------------


@pytest.mark.parametrize(
    ("what", "circle"),
    [
        ("a cross hole on the axis", ((25, 0), 4)),
        ("a hole above it, crossing it", ((25, 5), 6)),
        ("a hole clear of it", ((25, 5), 3)),
    ],
)
def test_a_circle_is_never_the_outline_of_a_turned_part(tmp_path, what, circle):
    """The rule the old reader kept by refusing to read the entity at all.

    The middle case is the one that would bite: an arc crossing the centre
    line is one the clipping refuses outright, so a circle drawn over the axis
    would have turned down the whole drawing rather than the circle.
    """
    profile = drawing.read_profile(_saved(_shaft(circle), tmp_path))

    assert drawing.section_area(profile.curves) == pytest.approx(500.0)
    assert not any(curve.is_arc for curve in profile.curves)


def test_a_circle_drawn_as_two_halves_is_still_a_circle(tmp_path):
    """Totted up from the arcs rather than taken on trust from the entity.

    Plenty of drawings carry a circle as two arcs, or as a closed polyline of
    them. What makes it a circle is that it comes to a whole turn.
    """
    doc = _shaft()
    msp = doc.modelspace()
    msp.add_arc((25, 5), 6, start_angle=0, end_angle=180)
    msp.add_arc((25, 5), 6, start_angle=180, end_angle=360)

    profile = drawing.read_profile(_saved(doc, tmp_path))

    assert drawing.section_area(profile.curves) == pytest.approx(500.0)


# --- inside an outline, a circle is a hole --------------------------------


def test_bolt_holes_are_cut_out_of_a_plate(tmp_path):
    read = drawing.read_part(_saved(_holed_plate(), tmp_path))

    assert isinstance(read, drawing.Prism)
    assert len(read.holes) == 2
    assert drawing.section_area(read.curves) == pytest.approx(PLATE_SECTION)
    assert sum(drawing.section_area(hole) for hole in read.holes) == pytest.approx(
        2 * math.pi * BORE_RADIUS**2
    )


def test_a_slot_is_a_hole_like_any_other(tmp_path):
    """Nothing here is about circles. What is cut out is whatever closes."""
    doc = _holed_plate(holes=False)
    _rect(doc.modelspace(), 40, 25, 60, 35)

    read = drawing.read_part(_saved(doc, tmp_path))

    assert len(read.holes) == 1
    assert drawing.section_area(read.holes[0]) == pytest.approx(200.0)


def test_what_was_cut_out_is_said(tmp_path):
    """Material the part does not have is a thing to state, not to imply."""
    read = drawing.read_part(_saved(_holed_plate(), tmp_path))

    said = " ".join(read.assumptions)
    assert "2 outline(s) inside it, cut out as holes" in said
    assert f"{2 * math.pi * BORE_RADIUS**2:.1f} mm2 of section taken away" in said


def test_a_plate_with_no_holes_claims_none(tmp_path):
    read = drawing.read_part(_saved(_holed_plate(holes=False), tmp_path))

    assert read.holes == []
    assert not any("cut out as holes" in note for note in read.assumptions)


# --- the solid ------------------------------------------------------------


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_a_plate_weighs_what_it_weighs_with_its_holes_gone(tmp_path):
    """The figure written down rather than recorded from a run.

    100 by 60 less two bores of 6, all 8 deep. Before the holes were cut this
    came back at 48000 -- the plate as though it were solid, which is 1810 mm3
    of material the part does not have.
    """
    out = tmp_path / "plate.glb"
    drawing.convert(_saved(_holed_plate(), tmp_path), out)
    meta = json.loads(out.with_suffix(".json").read_text())

    assert meta["parts"]["n1"]["volume_mm3"] == pytest.approx(HOLED_VOLUME)


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_a_bore_is_one_face(tmp_path):
    """Read in quarters and built whole, and this is why.

    Every measurement in the viewer snaps to a face. A bore in four pieces is
    one somebody has to click four times and can never take a diameter from,
    so the quarters the reading needs are put back together for the build.
    """
    out = tmp_path / "plate.glb"
    drawing.convert(_saved(_holed_plate(), tmp_path), out)
    meta = json.loads(out.with_suffix(".json").read_text())

    # Six for the box and one for each bore.
    assert len(meta["snap"]["n1"]["faces"]) == 8
    assert len(meta["face_groups"]["n1"]) == len(meta["snap"]["n1"]["faces"])


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_a_bore_says_where_its_axis_is(tmp_path):
    """What a measurement between two holes is made of.

    A round face used to record which way its axis pointed and not where that
    axis was, which is enough to say a bore is vertical and not enough to say
    where the bore is -- so two of them could not be measured apart. The
    distance between two holes is the dimension a plate is made to, and it was
    the one thing the viewer could not answer.

    Here because this is where the bores come from: the holes are drilled at
    25 and 75 across a 60 wide plate, so the answer to read off them is 50.
    """
    out = tmp_path / "plate.glb"
    drawing.convert(_saved(_holed_plate(), tmp_path), out)
    meta = json.loads(out.with_suffix(".json").read_text())

    bores = [face for face in meta["snap"]["n1"]["faces"] if face["kind"] == "cylinder"]
    assert len(bores) == 2

    for bore in bores:
        assert bore["radius"] == pytest.approx(BORE_RADIUS)
        # Down the part, which is the way an extrusion runs.
        assert abs(bore["axis"][2]) == pytest.approx(1.0)

    across = sorted(bore["position"][0] for bore in bores)
    assert across == pytest.approx([25.0, 75.0])
    assert all(bore["position"][1] == pytest.approx(30.0) for bore in bores)
