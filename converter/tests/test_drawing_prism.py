"""A part that is not turned, read from two views of it.

A turned part carries its own axis on the sheet, so one view describes it. A
part of constant section carries nothing of the sort: the depth it runs is
simply not in the view that shows its shape, it is in the view drawn above or
beside that one. So this reading needs the sheet divided into views and the
views paired -- which is the whole reason those came first.

Two things had to be got right before any of it could be built, and both were
wrong in a way nobody would have noticed:

A plate drawing has no centre line for the part and a pair of them crossing at
every bolt hole. Read as a turned part, that plate was revolved about one of
its own holes -- silently, into a solid that measures exactly and is nothing
like the part.

And a plate with no centre lines at all was turned down with a message telling
the draughtsman to draw one, which is advice for a drawing of a different kind
of part.

The reading tests run without OpenCascade. The ones that measure a solid need
it and say so.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing, occt

FIXTURES = Path(__file__).parent / "fixtures"

# A 100 x 60 plate with a 20 x 20 notch out of one corner, 8 thick.
PLATE = [(0, 0), (100, 0), (100, 40), (80, 40), (80, 60), (0, 60), (0, 0)]
PLATE_SECTION = 100 * 60 - 20 * 20
PLATE_THICKNESS = 8


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _chain(msp, points) -> None:
    for a, b in zip(points, points[1:], strict=False):
        msp.add_line(a, b)


def _rect(msp, x0: float, y0: float, x1: float, y1: float) -> None:
    _chain(msp, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])


def _plate(bolt_holes: bool = False, thickness_beside: bool = False):
    """The plate, and the view that gives its thickness."""
    doc = _document()
    msp = doc.modelspace()
    _chain(msp, PLATE)
    if thickness_beside:
        # Edge-on to the right, sharing the plate's height.
        _rect(msp, 130, 0, 130 + PLATE_THICKNESS, 60)
    else:
        # Edge-on below, sharing the plate's width.
        _rect(msp, 0, -20, 100, -20 + PLATE_THICKNESS)
    if bolt_holes:
        # What every plate drawing carries and no turned part does: centre
        # lines that mark a hole and stop there.
        msp.add_line((30, 20), (50, 20), dxfattribs={"layer": "CENTER"})
        msp.add_line((40, 10), (40, 30), dxfattribs={"layer": "CENTER"})
    return doc


# --- the reading ----------------------------------------------------------


def test_a_plate_is_read_as_a_part_of_constant_section(tmp_path):
    read = drawing.read_part(_saved(_plate(), tmp_path))

    assert isinstance(read, drawing.Prism)
    assert read.depth == pytest.approx(PLATE_THICKNESS)
    assert drawing.section_area(read.curves) == pytest.approx(PLATE_SECTION)


def test_a_bolt_holes_centre_line_does_not_make_a_plate_turned(tmp_path):
    """The one that was silently wrong.

    A plate is covered in centre lines that stop inside it, one pair crossing
    at every hole. Any of them, taken for the axis of the part, revolves the
    plate about a hole -- and what comes back is a solid with exact faces and
    an exact volume that is not the part in any respect.

    A centre line has to reach across the view to be its axis. A hole's does
    not reach across anything.
    """
    read = drawing.read_part(_saved(_plate(bolt_holes=True), tmp_path))

    assert isinstance(read, drawing.Prism)
    assert read.depth == pytest.approx(PLATE_THICKNESS)
    assert drawing.section_area(read.curves) == pytest.approx(PLATE_SECTION)


def test_the_depth_comes_from_whichever_side_the_second_view_is_on(tmp_path):
    """Above or beside: the layout says which way the thickness is measured."""
    read = drawing.read_part(_saved(_plate(thickness_beside=True), tmp_path))

    assert isinstance(read, drawing.Prism)
    assert read.depth == pytest.approx(PLATE_THICKNESS)
    assert any("beside it" in note for note in read.assumptions)


def test_the_section_is_the_view_with_more_to_it(tmp_path):
    """Edge-on, a part is a rectangle whatever shape it really is.

    So corners are what tell the two views apart, and the notch is what gives
    the plate more of them than the band below it.
    """
    read = drawing.read_part(_saved(_plate(), tmp_path))

    assert len(read.curves) == 6
    assert any("6 edges" in note for note in read.assumptions)


def test_it_says_the_outline_was_taken_to_run_straight_through(tmp_path):
    """The claim the whole reading rests on, on the model where it can be read."""
    read = drawing.read_part(_saved(_plate(), tmp_path))

    said = " ".join(read.assumptions)
    assert "constant section" in said
    assert "8 mm deep" in said


# --- what it will not read ------------------------------------------------


def test_a_part_drawn_in_three_views_is_refused(tmp_path):
    """Three views usually means the section is not constant.

    Reading it as though it were would give a solid that measures exactly and
    is the wrong shape, which is the failure worth refusing over.
    """
    doc = _document()
    msp = doc.modelspace()
    _chain(msp, PLATE)
    _rect(msp, 0, -20, 100, -12)
    _rect(msp, 130, 0, 138, 60)

    with pytest.raises(drawing.DrawingError, match="more than two views"):
        drawing.read_part(_saved(doc, tmp_path))


def test_an_outline_beside_the_part_rather_than_inside_it_is_refused(tmp_path):
    """Inside is a hole. Beside is something nobody here can name.

    Cutting it out and leaving it in are both guesses, and the difference
    between them is material the part either has or does not.
    """
    doc = _document()
    msp = doc.modelspace()
    _chain(msp, PLATE)
    _rect(msp, 0, -20, 100, -12)
    # In the empty square the notch leaves: inside the box around the part,
    # and not inside the part. A hole cut there would be a hole in nothing.
    _rect(msp, 85, 45, 95, 55)

    with pytest.raises(drawing.DrawingError, match="beside the part"):
        drawing.read_part(_saved(doc, tmp_path))


def test_one_view_and_no_centre_line_still_asks_for_a_centre_line(tmp_path):
    """Nothing lines up, so there is no second reading to offer.

    The turned reading keeps the refusal here, and it is the right one: a lone
    outline with no axis and no second view is a drawing that is missing
    something, and the message says what.
    """
    doc = _document()
    msp = doc.modelspace()
    _chain(msp, PLATE)

    with pytest.raises(drawing.DrawingError, match="no centre line found"):
        drawing.read_part(_saved(doc, tmp_path))


def test_a_turned_part_is_still_read_as_turned(tmp_path):
    """The fork must not move anything that already worked."""
    for name in ("stepped_shaft.dxf", "plain_shaft.dxf"):
        read = drawing.read_part(FIXTURES / name)
        assert isinstance(read, drawing.Profile)
        assert read.axis.direction == pytest.approx((1.0, 0.0))


# --- the solid ------------------------------------------------------------


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_the_plate_measures_what_the_drawing_describes(tmp_path):
    """Written down rather than recorded from a run: 5600 mm2, 8 deep."""
    import json

    out = tmp_path / "plate.glb"
    drawing.convert(_saved(_plate(bolt_holes=True), tmp_path), out)
    meta = json.loads(out.with_suffix(".json").read_text())

    assert meta["parts"]["n1"]["volume_mm3"] == pytest.approx(
        PLATE_SECTION * PLATE_THICKNESS
    )
    assert meta["geometry_source"] == "derived"
    assert meta["derived"]["method"] == "dxf-extrude"
    # An extrusion has a direction, not an axis. Reporting one would invite a
    # reader to measure a radius about it.
    assert meta["derived"]["axis_point"] is None
    assert meta["derived"]["axis_direction"] is None


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_the_plate_comes_back_as_a_brep_with_real_faces(tmp_path):
    """Six faces for the box and two more for the notch, all snappable."""
    import json

    out = tmp_path / "plate.glb"
    drawing.convert(_saved(_plate(), tmp_path), out)
    meta = json.loads(out.with_suffix(".json").read_text())

    assert len(meta["snap"]["n1"]["faces"]) == 8
    assert len(meta["face_groups"]["n1"]) == len(meta["snap"]["n1"]["faces"])
