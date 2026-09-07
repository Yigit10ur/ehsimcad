"""Reading a turned part out of a printed sheet.

Runs without OpenCascade, like the DXF reader's tests and for the same reason:
what can be wrong here without anybody noticing is which line was taken for the
axis and which marks were taken for the part.

Two kinds of input on purpose. The checked-in fixtures are real prints of the
DXF fixtures, so the whole chain is exercised on files nobody hand-tuned; the
PDFs written here are a few lines each, so a single case can be stated exactly
rather than arranged for.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.cad import drawing, pdf

FIXTURES = Path(__file__).parent / "fixtures"
PRINTED = FIXTURES / "stepped_shaft_printed.pdf"
PLOTTED = FIXTURES / "stepped_shaft_plotted.pdf"
FILLETED = FIXTURES / "filleted_shaft_plotted.pdf"


def minimal_pdf(content: str, width: int = 300, height: int = 200) -> bytes:
    """The smallest PDF that holds a content stream.

    Written out rather than drawn with a library so that a test can put one
    exact thing on the page -- a stroke with this dash pattern, a filled shape
    and nothing else -- which is not something a plotting library will agree to
    do.
    """
    stream = content.encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {width} {height}]"
        f"/Contents 4 0 R>>".encode(),
        b"<</Length "
        + str(len(stream)).encode()
        + b">>stream\n"
        + stream
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{start}\n%%EOF\n"
    ).encode()
    return bytes(out)


def sheet(content: str, tmp_path: Path, name: str = "case.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(minimal_pdf(content))
    return path


# A box 240 wide and 80 tall, with a dash-dot centre line down the middle of it.
BOX = """20 60 m 20 140 l S
20 140 m 260 140 l S
260 140 m 260 60 l S
260 60 m 20 60 l S
"""
AXIS = "[8 2 1 2] 0 d\n10 100 m 270 100 l S\n"


# --- finding the axis -----------------------------------------------------


def test_a_dash_pattern_on_a_stroke_is_a_centre_line(tmp_path):
    profile = pdf.read_profile(sheet(BOX + AXIS, tmp_path), length_mm=240)

    assert profile.axis.found_by == "centre line"
    assert profile.axis.direction == pytest.approx((1.0, 0.0))
    # Half the box, 240 by 40, taken from the middle line outwards.
    assert drawing.section_area(profile.curves) == pytest.approx(9600.0)


def test_a_dashed_line_is_not_a_centre_line(tmp_path):
    """One mark length repeating is an edge behind material, not an axis."""
    dashed = "[4 2] 0 d\n10 100 m 270 100 l S\n"

    with pytest.raises(drawing.DrawingError, match="no centre line"):
        pdf.read_profile(sheet(BOX + dashed, tmp_path))


def test_a_solid_line_is_not_a_centre_line(tmp_path):
    with pytest.raises(drawing.DrawingError, match="no centre line"):
        pdf.read_profile(sheet(BOX + "10 100 m 270 100 l S\n", tmp_path))


@pytest.mark.parametrize(
    ("pattern", "is_axis"),
    [
        (([5.6, 1.4, 0.7, 1.4], 0), True),  # long, gap, short, gap
        (([8, 2, 1, 2], 0), True),
        (([2.8, 1.4], 0), False),  # one length repeating: hidden
        (([3, 1, 3, 1], 0), False),  # written twice over, still one length
        (None, False),
        (([], 0), False),
    ],
)
def test_which_dash_patterns_mean_an_axis(pattern, is_axis):
    assert pdf._is_dash_dot(pattern) is is_axis


def test_a_centre_line_drawn_as_separate_strokes_is_put_back_together():
    """What a renderer does instead of setting a dash pattern.

    The printed fixture is the DXF through a renderer that breaks every dashed
    line into short strokes before it reaches the page, so the pattern is in
    the geometry rather than in the graphics state. Both are ordinary, and
    which one a file uses says nothing about the drawing.
    """
    outline, axes, _units, _ignored = pdf.read_curves(PRINTED)

    assert len(axes) == 1
    assert axes[0].length > 300  # one line, not the 42 pieces it arrived as
    assert all(not c.is_arc for c in axes)
    assert len(outline) == 23


def test_two_collinear_edges_are_not_joined_into_one_line(tmp_path):
    """A dashed line is many short strokes. Two edges that line up are two.

    Drawn here as the two ends of a part at the same height, which is as
    ordinary as a drawing gets -- and would become one line, with the material
    between them, if lining up were enough.
    """
    content = (
        "20 60 m 20 140 l S\n"
        "20 140 m 80 140 l S\n"
        "80 140 m 80 60 l S\n"
        "80 60 m 20 60 l S\n"
        "200 60 m 200 140 l S\n"
        "200 140 m 260 140 l S\n"
        "260 140 m 260 60 l S\n"
        "260 60 m 200 60 l S\n"
    ) + AXIS

    profile = pdf.read_profile(sheet(content, tmp_path), length_mm=60)
    # One of the two boxes, 60 by 40, and not a single 240 long one.
    assert drawing.section_area(profile.curves) == pytest.approx(2400.0)


# --- what is on the page but not in the part ------------------------------


def test_filled_shapes_are_not_geometry(tmp_path):
    """Every letter and every arrowhead is filled; every line is stroked.

    The filled triangle here sits nearer the axis than the part and encloses
    more than nothing, so it would be taken for the profile if being filled did
    not rule it out.
    """
    arrow = "120 100 m 140 108 l 140 92 l f\n"

    profile = pdf.read_profile(sheet(BOX + arrow + AXIS, tmp_path), length_mm=240)

    assert drawing.section_area(profile.curves) == pytest.approx(9600.0)
    assert "1 filled shape" in profile.ignored


def test_a_page_with_nothing_drawn_on_it_says_so(tmp_path):
    with pytest.raises(drawing.DrawingError, match="scanned image"):
        pdf.read_profile(sheet("BT /F1 12 Tf 10 10 Td ET\n", tmp_path))


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path):
    path = tmp_path / "not.pdf"
    path.write_text("this is not a drawing")

    with pytest.raises(drawing.DrawingError, match="could not be read as PDF"):
        pdf.read_profile(path)


# --- size -----------------------------------------------------------------


def test_a_known_length_fixes_the_scale():
    """The same part as the DXF fixture, and the same section.

    Two readers with nothing in common but the answer: one reads entity types
    out of a DXF, the other reads stroked paths out of a print of it.
    """
    for source in (PRINTED, PLOTTED):
        profile = pdf.read_profile(source, length_mm=90)
        assert drawing.section_area(profile.curves) == pytest.approx(700.0), source
        assert "90 mm along its axis" in " ".join(profile.assumptions)


def test_without_a_length_the_sheet_is_taken_as_printed_full_size():
    profile = pdf.read_profile(PRINTED)
    said = " ".join(profile.assumptions)

    assert "printed full size" in said
    # And it says what that makes the part, which is how somebody who knows the
    # part spots that the sheet was plotted to a scale.
    assert "103.9 mm long" in said


def test_a_length_that_is_not_a_length_is_refused():
    with pytest.raises(drawing.DrawingError, match="more than zero"):
        pdf.read_profile(PRINTED, length_mm=0)


# --- curves ---------------------------------------------------------------


def test_a_fillet_arrives_as_one_arc_of_the_right_radius():
    """The hardest thing a PDF does to a drawing.

    A PDF cannot draw a circle: an arc leaves the CAD application as an arc and
    arrives as two or three cubics. Fitted back, they are a fillet; left as
    they came, they are a row of chords that closes and revolves just as well
    and is not the part. Joined back into one, they are one face to click on
    rather than two.
    """
    profile = pdf.read_profile(FILLETED, length_mm=50)
    arcs = [c for c in profile.curves if c.is_arc]

    assert len(arcs) == 1
    assert math.dist(arcs[0].centre, arcs[0].start) == pytest.approx(5.0, abs=1e-3)
    assert arcs[0].length == pytest.approx(5 * math.pi / 2, rel=1e-3)
    _assert_ends_are_on_the_circle(arcs[0])


def _assert_ends_are_on_the_circle(arc) -> None:
    """An arc whose ends are not on its circle is not an arc OCCT will build.

    A circle fitted to a curve passes near its ends, not through them, and near
    is a failure here rather than a rounding error: the edge is refused and the
    whole drawing with it.
    """
    start = math.dist(arc.centre, arc.start)
    end = math.dist(arc.centre, arc.end)
    assert start == pytest.approx(end, rel=1e-12)


def test_an_arc_drawn_as_a_single_cubic_keeps_its_ends(tmp_path):
    """The case the merging step would otherwise cover up.

    A quarter circle is one cubic as often as two, and a lone one never goes
    through the join that rebuilds the circle through its own endpoints. So the
    circle it is given has to be right to begin with.
    """
    # A quarter of radius 40 about (60, 100), by the usual control distance.
    content = (
        "20 100 m 20 140 l S\n"
        "20 140 m 60 140 l S\n"
        "60 140 m 82.092 140 100 122.092 100 100 c S\n"
    ) + AXIS

    profile = pdf.read_profile(sheet(content, tmp_path), length_mm=80)
    arcs = [c for c in profile.curves if c.is_arc]

    assert len(arcs) == 1
    assert math.dist(arcs[0].centre, arcs[0].start) == pytest.approx(40.0, rel=1e-3)
    _assert_ends_are_on_the_circle(arcs[0])
    # A 40 by 40 corner and the quarter disc beyond it.
    assert drawing.section_area(profile.curves) == pytest.approx(
        1600 + math.pi * 40**2 / 4, rel=1e-3
    )


def test_a_straight_run_is_not_read_as_an_enormous_arc(tmp_path):
    """A line fits a circle of huge radius perfectly well.

    Drawn as a cubic whose control points lie along it, which is what a CAD
    application emits for a straight segment of a polyline.
    """
    content = (
        "20 60 m 20 140 l S\n"
        "20 140 m 100 140 180 140 260 140 c S\n"
        "260 140 m 260 60 l S\n"
        "260 60 m 20 60 l S\n"
    ) + AXIS

    profile = pdf.read_profile(sheet(content, tmp_path), length_mm=240)
    assert not any(c.is_arc for c in profile.curves)
    assert drawing.section_area(profile.curves) == pytest.approx(9600.0)


def _dashes(y: int, x0: int, x1: int, mark: int, gap: int) -> str:
    """A line drawn as a row of short strokes, all the same length."""
    out, x = [], x0
    while x + mark <= x1:
        out.append(f"{x} {y} m {x + mark} {y} l S")
        x += mark + gap
    return "\n".join(out) + "\n"


def test_a_line_broken_into_even_marks_is_not_the_axis(tmp_path):
    """The other half of the dash-dot rule, for the renderer's way of dashing.

    A bore drawn hidden reaches the page as a row of even strokes; the axis
    reaches it as a row of alternating ones. Both are rows of strokes, and the
    lengths are the whole of the difference.
    """
    # Longer than the centre line, so that if even marks counted as an axis
    # this one would be taken for it -- the longest wins.
    content = BOX + _dashes(120, 5, 295, 6, 3) + AXIS

    profile = pdf.read_profile(sheet(content, tmp_path), length_mm=240)

    assert profile.axis.point[1] == pytest.approx(100.0)
    assert drawing.section_area(profile.curves) == pytest.approx(9600.0)


def test_edges_that_line_up_across_the_sheet_stay_separate(tmp_path):
    """Four small views in a row, their top edges on one line.

    Four is enough to look like a dashed line, and the gaps are what say it is
    not. Joined, none of the four would close any more.
    """
    boxes = "".join(
        f"{x} 150 m {x} 180 l S\n"
        f"{x} 180 m {x + 20} 180 l S\n"
        f"{x + 20} 180 m {x + 20} 150 l S\n"
        f"{x + 20} 150 m {x} 150 l S\n"
        for x in (30, 90, 150, 210)
    )

    profile = pdf.read_profile(sheet(BOX + boxes + AXIS, tmp_path), length_mm=240)

    assert drawing.section_area(profile.curves) == pytest.approx(9600.0)
    assert "4 other closed outlines on the sheet" in profile.ignored


def test_a_cubic_that_is_not_a_circle_is_not_made_into_one(tmp_path):
    """An S bend fits a circle after a fashion, and is not one.

    The check that matters is not whether a circle can be fitted -- one always
    can -- but whether every point of the curve is on it.
    """
    # Lopsided on purpose: a curve symmetric about its own middle fits no
    # circle at all, so it would be turned away by the fit failing rather than
    # by the check that is under test here.
    content = (
        "20 60 m 20 140 l S\n"
        "20 140 m 40 200 200 180 260 140 c S\n"
        "260 140 m 260 60 l S\n"
        "260 60 m 20 60 l S\n"
    ) + AXIS

    profile = pdf.read_profile(sheet(content, tmp_path), length_mm=240)

    assert not any(c.is_arc for c in profile.curves)
    # Straight runs along the curve, rather than one arc pretending to be it.
    assert len(profile.curves) > 5


def test_a_sheet_taken_as_full_size_is_measured_in_millimetres():
    """72 points to the inch, which is the only size a PDF knows.

    Asserted as a length rather than as a sentence: the note saying the part
    came out 103.9 mm long is worth nothing if the geometry is still in points.
    """
    profile = pdf.read_profile(PRINTED)
    along = [p[0] for c in profile.curves for p in (c.start, c.end)]

    assert max(along) - min(along) == pytest.approx(103.9, abs=0.1)
