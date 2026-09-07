"""Reading a turned part out of a DXF drawing.

Everything here runs without OpenCascade. That is the point of splitting the
reader from the revolve: deciding which line is the axis and which outline is
the part is where this can be wrong in a way nobody notices, so it is the part
that has to be covered on every pull request rather than only in the image.

The two fixtures are drawn by `scripts/make_fixture.py` the way an office draws
them -- mirrored about the centre line, bore dashed, dimensions and a title
block on top -- so a reader that only works on a tidy list of profile edges
fails here rather than in front of somebody.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing

FIXTURES = Path(__file__).parent / "fixtures"
STEPPED = FIXTURES / "stepped_shaft.dxf"
PLAIN = FIXTURES / "plain_shaft.dxf"


def read(path: Path) -> drawing.Profile:
    return drawing.read_profile(path)


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def area_of(doc, tmp_path: Path) -> float:
    """Save the drawing, read it, and report the section it would revolve."""
    return drawing.section_area(read(_saved(doc, tmp_path)).curves)


# --- what the sheet holds that is not the part ----------------------------


def test_dimensions_and_notes_do_not_reach_the_outline():
    outline, axis, _units, ignored = drawing.read_curves(STEPPED)
    assert "1 DIMENSION" in ignored
    assert "1 TEXT" in ignored
    # 14 outline lines, 2 dashed bore lines, 4 of title block.
    assert len(outline) == 20
    assert len(axis) == 1


def test_the_title_block_is_not_mistaken_for_the_part():
    profile = read(STEPPED)
    xs = [p[0] for c in profile.curves for p in (c.start, c.end)]
    ys = [p[1] for c in profile.curves for p in (c.start, c.end)]
    assert min(xs) == pytest.approx(0.0)
    assert max(xs) == pytest.approx(90.0)
    # The title block sits at y = -40 to -60. Nothing from it is here.
    assert min(ys) == pytest.approx(4.0)
    assert max(ys) == pytest.approx(15.0)
    # Said, rather than silently dropped: a closed outline beside the part is
    # exactly what somebody would want to hear about if the answer looks wrong.
    assert "1 other closed outline on the sheet" in profile.ignored
    assert not any("outline on the sheet" in note for note in profile.assumptions)


def _tube_and_axis(msp) -> None:
    """A bored part, so its section stands 3 mm clear of the axis.

    Bored on purpose. A solid part's section runs to the axis, so nothing else
    on the sheet can be nearer and the rule that picks the outline beside the
    axis cannot be got wrong. These tests are about what happens when it can.
    """
    for a, b in [
        ((0, 3), (0, 6)),
        ((0, 6), (20, 6)),
        ((20, 6), (20, 3)),
        ((20, 3), (0, 3)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "CENTER"})


def _decoy(msp, layer: str) -> None:
    """A closed outline nearer the axis than the part, which must not win."""
    for a, b in [
        ((2, 0.5), (2, 1.5)),
        ((2, 1.5), (8, 1.5)),
        ((8, 1.5), (8, 0.5)),
        ((8, 0.5), (2, 0.5)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": layer})


def test_geometry_on_a_switched_off_layer_is_ignored(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    doc.layers.add("SCRATCH").off()
    _decoy(msp, "SCRATCH")

    profile = read(_saved(doc, tmp_path))
    assert drawing.section_area(profile.curves) == pytest.approx(60.0)
    assert not any("other closed outline" in note for note in profile.ignored)


def test_geometry_on_a_frozen_layer_is_ignored(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    doc.layers.add("SCRATCH").freeze()
    _decoy(msp, "SCRATCH")

    assert area_of(doc, tmp_path) == pytest.approx(60.0)


def test_the_defpoints_layer_is_not_geometry(tmp_path):
    """Where AutoCAD keeps the anchor points of dimensions.

    Ordinary lines on an ordinary layer, except that the layer is never
    plotted. Reading them as material would build a part out of the dimensions.
    """
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    # setup=True has already made it: it is a standard layer, not a choice.
    assert "Defpoints" in doc.layers
    _decoy(msp, "Defpoints")

    assert area_of(doc, tmp_path) == pytest.approx(60.0)


def test_a_decoy_that_is_visible_does_win(tmp_path):
    """The other half of the three tests above.

    Without this they would pass against a reader that discards every closed
    outline nearer the axis than the part, for any reason or none.
    """
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    _decoy(msp, "0")

    assert area_of(doc, tmp_path) == pytest.approx(6.0)


# --- the axis -------------------------------------------------------------


def test_the_axis_comes_from_the_centre_line():
    profile = read(STEPPED)
    assert profile.axis.found_by == "centre line"
    assert profile.axis.direction == pytest.approx((1.0, 0.0))
    assert profile.axis.point[1] == pytest.approx(0.0)


def test_a_bylayer_linetype_is_followed_to_the_layer(tmp_path):
    """The centre line in both fixtures says BYLAYER, as nearly all do.

    A reader that takes the entity's own linetype at face value finds no centre
    line at all, and this is the test that says so.
    """
    doc = ezdxf.readfile(STEPPED)
    centre = [e for e in doc.modelspace() if e.dxf.layer == "CENTER"]
    assert [e.dxf.linetype for e in centre] == ["BYLAYER"]
    assert drawing._linetype_of(centre[0], doc) == "CENTER"


def test_an_axis_named_only_by_its_layer_is_found(tmp_path):
    doc = _document()
    doc.layers.add("EKSEN")  # continuous, so only the name says what it is
    msp = doc.modelspace()
    for a, b in [
        ((0, -5), (0, 5)),
        ((0, 5), (20, 5)),
        ((20, 5), (20, -5)),
        ((20, -5), (0, -5)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "EKSEN"})
    assert read(_saved(doc, tmp_path)).axis.found_by == "centre line"


def test_a_drawing_with_no_centre_line_is_refused(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, -5), (0, 5)),
        ((0, 5), (20, 5)),
        ((20, 5), (20, -5)),
        ((20, -5), (0, -5)),
    ]:
        msp.add_line(a, b)  # no centre line drawn

    with pytest.raises(drawing.DrawingError, match="no centre line"):
        read(_saved(doc, tmp_path))


# --- the profile ----------------------------------------------------------


def test_a_bored_part_is_closed_by_its_bore():
    profile = read(STEPPED)
    assert len(profile.curves) == 8
    # 6 x 30 + 11 x 40 + 4 x 20, the section of a shaft bored 4 through.
    assert drawing.section_area(profile.curves) == pytest.approx(700.0)
    assert any("bored" in note for note in profile.assumptions)


def test_a_solid_part_is_closed_by_the_axis():
    profile = read(PLAIN)
    assert drawing.section_area(profile.curves) == pytest.approx(500.0)
    # The end faces are drawn straight across the centre line: both are cut at
    # it, and the axis itself closes the section.
    on_axis = [
        c for c in profile.curves if abs(c.start[1]) < 1e-9 and abs(c.end[1]) < 1e-9
    ]
    assert len(on_axis) == 1
    assert on_axis[0].length == pytest.approx(50.0)
    assert not any("bored" in note for note in profile.assumptions)


def test_a_gap_in_the_outline_is_refused(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, -5), (0, 5)),
        ((0, 5), (20, 5)),
        ((20, 5), (20, 1)),  # stops short: 1 mm of the end face missing
        ((20, -5), (0, -5)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "CENTER"})

    with pytest.raises(drawing.DrawingError, match="closed outline"):
        read(_saved(doc, tmp_path))


def _box_and_axis(msp) -> None:
    for a, b in [
        ((0, -5), (0, 5)),
        ((0, 5), (20, 5)),
        ((20, 5), (20, -5)),
        ((20, -5), (0, -5)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "CENTER"})


def test_a_line_touching_the_outline_mid_span_is_dropped(tmp_path):
    """It joins nothing and closes nothing, so it is not part of any outline."""
    doc = _document()
    msp = doc.modelspace()
    _box_and_axis(msp)
    msp.add_line((10, 5), (10, 12))

    profile = read(_saved(doc, tmp_path))
    assert drawing.section_area(profile.curves) == pytest.approx(100.0)
    assert max(p[1] for c in profile.curves for p in (c.start, c.end)) == pytest.approx(
        5.0
    )


def test_a_line_meeting_the_outline_at_a_corner_is_refused(tmp_path):
    """Three ends at one point, and no way to say which two are the outline.

    Refusing is the answer rather than picking: the two wrong ones make a
    closed loop just as readily as the two right ones, and the result would be
    a solid nobody could tell was wrong.
    """
    doc = _document()
    msp = doc.modelspace()
    _box_and_axis(msp)
    msp.add_line((20, 5), (20, 12))

    with pytest.raises(drawing.DrawingError, match="more than two lines meet"):
        read(_saved(doc, tmp_path))


def test_an_arc_across_the_centre_line_is_refused(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    msp.add_line((0, -5), (0, 5))
    msp.add_line((0, 5), (20, 5))
    msp.add_arc((20, 0), radius=5, start_angle=-90, end_angle=90)
    msp.add_line((20, -5), (0, -5))
    msp.add_line((-4, 0), (26, 0), dxfattribs={"layer": "CENTER"})

    with pytest.raises(drawing.DrawingError, match="arc crosses the centre line"):
        read(_saved(doc, tmp_path))


def test_an_arc_on_one_side_becomes_an_arc(tmp_path):
    """A fillet stays a fillet, rather than being flattened into chords."""
    doc = _document()
    msp = doc.modelspace()
    msp.add_line((0, 0), (0, 10))
    msp.add_line((0, 10), (15, 10))
    msp.add_arc((15, 5), radius=5, start_angle=0, end_angle=90)
    msp.add_line((20, 5), (20, 0))
    msp.add_line((20, 0), (0, 0))
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "CENTER"})

    profile = read(_saved(doc, tmp_path))
    arcs = [c for c in profile.curves if c.is_arc]
    assert len(arcs) == 1
    assert arcs[0].length == pytest.approx(5 * math.pi / 2)


# --- units ----------------------------------------------------------------


def test_a_drawing_in_inches_arrives_in_millimetres(tmp_path):
    doc = _document()
    doc.header["$INSUNITS"] = 1  # inches
    msp = doc.modelspace()
    for a, b in [
        ((0, -1), (0, 1)),
        ((0, 1), (2, 1)),
        ((2, 1), (2, -1)),
        ((2, -1), (0, -1)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-1, 0), (3, 0), dxfattribs={"layer": "CENTER"})

    profile = read(_saved(doc, tmp_path))
    assert profile.units == "inches"
    # A 2 x 1 inch half section is 50.8 x 25.4 mm.
    assert drawing.section_area(profile.curves) == pytest.approx(50.8 * 25.4)


def test_a_file_that_is_not_dxf_is_refused(tmp_path):
    path = tmp_path / "not.dxf"
    path.write_text("this is not a drawing")
    with pytest.raises(drawing.DrawingError, match="could not be read as DXF"):
        read(path)


def test_a_half_view_is_read_from_the_side_it_was_drawn_on(tmp_path):
    """Symmetric parts are often drawn as half a section and the axis.

    Both fixtures are drawn in full, so on their own they would let a reader
    that always takes one particular side pass. This one is drawn below the
    centre line only, and there is nothing above it to find.
    """
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, 0), (0, -8)),
        ((0, -8), (25, -8)),
        ((25, -8), (25, 0)),
        ((25, 0), (0, 0)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (29, 0), dxfattribs={"layer": "CENTER"})

    profile = read(_saved(doc, tmp_path))
    assert drawing.section_area(profile.curves) == pytest.approx(200.0)
    assert any("below the centre line" in note for note in profile.assumptions)


# The same rounded corner, drawn as a polyline traversed each way round. A
# bulge belongs to the segment leaving its vertex and its sign is the direction
# of travel, so reversing the outline moves every bulge and flips it.
CORNER_CLOCKWISE = [
    (0, 0, 0.0),
    (0, 10, 0.0),
    (15, 10, -0.41421356),
    (20, 5, 0.0),
    (20, 0, 0.0),
]
CORNER_ANTICLOCKWISE = [
    (20, 0, 0.0),
    (20, 5, 0.41421356),
    (15, 10, 0.0),
    (0, 10, 0.0),
    (0, 0, 0.0),
]


@pytest.mark.parametrize(
    ("points", "corner"),
    [(CORNER_CLOCKWISE, "clockwise"), (CORNER_ANTICLOCKWISE, "anticlockwise")],
)
def test_a_polyline_corner_is_the_arc_it_draws(tmp_path, points, corner):
    """A rounded corner in a polyline is a bulge, and its sign is its direction.

    Both signs are here because the library hands every arc back anticlockwise
    regardless of which way the polyline runs through it, so a reader that
    trusts the two ends it is given draws one of these the long way round --
    270 degrees where the drawing says 90. The outline still closes and still
    revolves. It is simply a different part.
    """
    doc = _document()
    msp = doc.modelspace()
    msp.add_lwpolyline(points, format="xyb")
    msp.add_line((-4, 0), (24, 0), dxfattribs={"layer": "CENTER"})

    profile = read(_saved(doc, tmp_path))
    arcs = [c for c in profile.curves if c.is_arc]
    assert len(arcs) == 1, corner
    assert arcs[0].length == pytest.approx(5 * math.pi / 2), corner
    # 20 x 10, less the square corner the fillet takes out of it.
    assert drawing.section_area(profile.curves) == pytest.approx(
        200 - (25 - 25 * math.pi / 4), rel=1e-3
    ), corner


def test_the_longest_centre_line_is_the_axis(tmp_path):
    """Drawings carry centre marks as well as centre lines.

    A hole's centre mark is drawn the same way as the part's axis and is
    shorter, which is all there is to tell them apart.
    """
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    msp.add_line((30, 18), (30, 22), dxfattribs={"layer": "CENTER"})

    profile = read(_saved(doc, tmp_path))
    assert profile.axis.direction == pytest.approx((1.0, 0.0))
    assert profile.axis.point[1] == pytest.approx(0.0)


def test_a_section_drawn_with_its_axis_edge_reads_the_same(tmp_path):
    """Half sections are often drawn as a closed loop, axis edge included."""
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, 0), (0, 8)),
        ((0, 8), (25, 8)),
        ((25, 8), (25, 0)),
        ((25, 0), (0, 0)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (29, 0), dxfattribs={"layer": "CENTER"})

    assert area_of(doc, tmp_path) == pytest.approx(200.0)


def test_an_axis_also_drawn_as_a_plain_line_does_not_break_the_outline(tmp_path):
    """The same line twice: once as the centre line, once as ordinary geometry.

    It runs past both ends of the part, so kept as outline it would leave a
    chain that closes nothing.
    """
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    msp.add_line((-4, 0), (24, 0))

    assert area_of(doc, tmp_path) == pytest.approx(60.0)


def test_a_line_drawn_twice_does_not_become_the_part(tmp_path):
    """Duplicated geometry is the most ordinary thing wrong with a DXF.

    Two copies of one line close into a loop enclosing nothing, and it sits on
    the axis -- so by every other rule here it is a better answer than the
    part.
    """
    doc = _document()
    msp = doc.modelspace()
    _tube_and_axis(msp)
    msp.add_line((10, 0), (10, 5))
    msp.add_line((10, 0), (10, 5))

    assert area_of(doc, tmp_path) == pytest.approx(60.0)


def test_an_axis_edge_and_a_drawn_axis_meeting_at_a_corner(tmp_path):
    """Both are on the axis, and they share the corner the section starts at.

    Kept as outline, three ends meet there and the drawing is refused. Dropping
    what lies along the axis is what stops a redundant line from making the
    part unreadable.
    """
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, 0), (0, 8)),
        ((0, 8), (25, 8)),
        ((25, 8), (25, 0)),
        ((25, 0), (0, 0)),
    ]:
        msp.add_line(a, b)
    msp.add_line((0, 0), (29, 0))  # the axis again, as ordinary geometry
    msp.add_line((-4, 0), (29, 0), dxfattribs={"layer": "CENTER"})

    assert area_of(doc, tmp_path) == pytest.approx(200.0)


def test_a_hairline_gap_still_closes(tmp_path):
    """What a CAD application leaves behind, as against what a mistake leaves.

    A ten thousandth of a millimetre is rounding; the millimetre gap in
    `test_a_gap_in_the_outline_is_refused` is a missing edge. The tolerance has
    to be between them, and it is set from the size of the drawing rather than
    fixed -- so these two tests together are what pin it down.
    """
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, 0), (0, 8)),
        ((0, 8), (25, 8)),
        ((25, 8), (25, 0.0001)),
        ((25, 0), (0, 0)),
    ]:
        msp.add_line(a, b)
    msp.add_line((-4, 0), (29, 0), dxfattribs={"layer": "CENTER"})

    assert area_of(doc, tmp_path) == pytest.approx(200.0, rel=1e-4)
