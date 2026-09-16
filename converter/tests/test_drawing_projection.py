"""Which drawings on a sheet are the same part seen from different directions.

Once a sheet comes apart into views, something has to say what the pieces are
to each other. Orthographic projection already says it: a view drawn above or
below another shares its width, a view beside it shares its height, because
both show the same dimension of the same part at the same scale.

What that buys is the one judgement size cannot make. A detail drawn at 5:1
encloses far more than the part it magnifies, so a rule that picks the biggest
outline picks the detail -- and a detail lines up with nothing, while a second
view lines up exactly.

Pure 2D and no OpenCascade, like the rest of the reading.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing


def _view(x0: float, y0: float, x1: float, y1: float) -> drawing.View:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return drawing.View(
        [drawing.Curve(a, b) for a, b in zip(corners, corners[1:], strict=False)], []
    )


def _groups(views: list[drawing.View], gap: float = 1.0) -> list[int]:
    """The sizes of the projection groups, largest first."""
    return sorted(
        (len(group) for group in drawing.in_projection(views, gap)), reverse=True
    )


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _box(msp, x0: float, y0: float, x1: float, y1: float) -> None:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(corners, corners[1:], strict=False):
        msp.add_line(a, b)


def _centre(msp, x0: float, y: float, x1: float) -> None:
    msp.add_line((x0, y), (x1, y), dxfattribs={"layer": "CENTER"})


# --- what lines up --------------------------------------------------------


def test_a_view_above_another_shares_its_width():
    assert _groups([_view(0, 0, 40, 10), _view(0, 60, 40, 80)]) == [2]


def test_a_view_beside_another_shares_its_height():
    assert _groups([_view(0, 0, 10, 40), _view(60, 0, 80, 40)]) == [2]


def test_a_detail_drawn_to_another_scale_lines_up_with_nothing():
    """The whole reason this exists.

    Same part, drawn twice the size. It sits above the view it magnifies and
    overlaps its width, which is as far as a test about overlapping gets --
    and it is not a projection of anything.
    """
    assert _groups([_view(0, 0, 20, 6), _view(0, 60, 40, 72)]) == [1, 1]


def test_corner_to_corner_is_not_a_projection():
    """Sharing neither a row nor a column, so the standard says nothing."""
    assert _groups([_view(0, 0, 20, 10), _view(60, 60, 80, 70)]) == [1, 1]


def test_a_view_drawn_inside_another_is_not_a_projection():
    """Overlapping both ways is not lining up either way."""
    assert _groups([_view(0, 0, 100, 100), _view(20, 20, 40, 40)]) == [1, 1]


def test_three_views_in_a_row_are_one_part():
    """Projection is passed along: front to top, top to the next, all one."""
    views = [_view(0, 0, 40, 10), _view(0, 60, 40, 80), _view(0, 120, 40, 130)]
    assert _groups(views) == [3]


def test_lining_up_is_judged_at_the_sheet_s_own_tolerance():
    """Hand-placed views are never exact, and a millimetre is not a scale change."""
    assert _groups([_view(0, 0, 40, 10), _view(0.4, 60, 40.4, 80)], gap=1.0) == [2]
    assert _groups([_view(0, 0, 40, 10), _view(0.4, 60, 40.4, 80)], gap=0.1) == [1, 1]


# --- what it changes about the reading ------------------------------------


def test_two_views_of_the_part_beat_a_bigger_lone_outline(tmp_path):
    """Size alone reads the detail. Lining up reads the part.

    The part is small and its magnified detail is not, so the outline that
    encloses the most section is the wrong one -- by a factor of six. What
    settles it is that the part is drawn twice and the two drawings line up.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -3, 20, 3)
    _centre(msp, -3, 0, 23)
    # A second section of the same part, directly beneath and the same width.
    _box(msp, 0, 27, 20, 33)
    _centre(msp, -3, 30, 23)
    # A detail at 5:1, off to one side and lining up with neither.
    _box(msp, 40, 55, 90, 70)
    _centre(msp, 35, 62.5, 95)

    profile = drawing.read_profile(_saved(doc, tmp_path))

    assert drawing.section_area(profile.curves) == pytest.approx(60.0)
    assert any("views of the part line up" in note for note in profile.assumptions)


def test_a_sheet_that_offers_two_unrelated_readings_says_so(tmp_path):
    """Nothing in the geometry chooses, so the choice is put on the model.

    A part and its 5:1 detail, and no third view to break the tie. This reads
    the wrong one -- there is nothing here that could know better, short of
    the label the drawing puts on a detail. What it must not do is read it
    quietly.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -3, 20, 3)
    _centre(msp, -3, 0, 23)
    _box(msp, 0, 55, 50, 70)
    _centre(msp, -5, 62.5, 55)

    profile = drawing.read_profile(_saved(doc, tmp_path))

    said = " ".join(profile.assumptions)
    assert "another reading of this sheet was possible" in said
    assert "does not line up with this one" in said


def test_a_sheet_with_one_reading_claims_nothing_about_others(tmp_path):
    """No rival, no note. An assumption nobody made is noise on the model."""
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    # A title block, which is not a reading of anything: it has no centre line.
    _box(msp, -20, -60, 70, -40)

    profile = drawing.read_profile(_saved(doc, tmp_path))

    said = " ".join(profile.assumptions)
    assert "reading of this sheet was possible" not in said
    assert "line up" not in said
