"""Telling the drawings on a sheet apart from each other.

A sheet is not a drawing. It carries a longitudinal view, an end view, a
section, a detail, and a title block that is none of them -- and until they are
told apart, every rule that has to pick the part out is picking it out of one
heap.

The axis is what makes it matter. There is more than one centre line on a
sheet, and taking the longest of them for the whole sheet is the failure this
file exists to stop: an axis got wrong does not make an obviously broken model,
it makes a convincing one with every radius wrong, which somebody then measures.

Everything here runs without OpenCascade, like the rest of the reading.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _box(msp, x0: float, y0: float, x1: float, y1: float, layer: str = "0") -> None:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(corners, corners[1:], strict=False):
        msp.add_line(a, b, dxfattribs={"layer": layer})


def _centre(msp, x0: float, y: float, x1: float) -> None:
    msp.add_line((x0, y), (x1, y), dxfattribs={"layer": "CENTER"})


def _views(path: Path) -> list[drawing.View]:
    outline, candidates, _units, _ignored = drawing.read_curves(path)
    return drawing.split_views(outline, candidates)


def _area(doc, tmp_path: Path) -> float:
    return drawing.section_area(drawing.read_profile(_saved(doc, tmp_path)).curves)


# --- the sheet comes apart ------------------------------------------------


def test_a_title_block_is_a_group_of_its_own(tmp_path):
    """The case the sheet always has, and the one the old rules worked around.

    A title block is more line than a small part. Nothing about it says "not
    the part" except where it sits, which is why where things sit has to be
    read before anything else is decided.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    _box(msp, -20, -60, 70, -40, layer="TITLE")

    views = _views(_saved(doc, tmp_path))

    assert len(views) == 2
    with_axis = [view for view in views if view.has_axis]
    assert len(with_axis) == 1
    assert with_axis[0].box == pytest.approx((0.0, -10.0, 50.0, 10.0))


def test_a_centre_line_does_not_reach_across_the_title_block(tmp_path):
    """What keeps the title block out of the running, and why it is length.

    The centre line is drawn a little past the part at each end and no
    further. A title block is wider than the part it labels, so the line that
    reaches across the part does not reach across the block -- and a block
    with no axis is a block that can never be revolved.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    _box(msp, -20, -60, 70, -40, layer="TITLE")

    title = next(view for view in _views(_saved(doc, tmp_path)) if view.box[1] < -30)
    assert title.axis_candidates == []


def test_a_stray_line_is_not_a_view(tmp_path):
    """A line on its own encloses nothing, so there is no reading to lose.

    Worth excluding rather than merely harmless: a line has no width, so it
    can lie nearer a centre line than the part does, and a rule that asks
    which view a centre line belongs to would answer with the stray.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    msp.add_line((-4, 40), (60, 40))

    views = _views(_saved(doc, tmp_path))

    assert len(views) == 1
    assert views[0].box == pytest.approx((0.0, -10.0, 50.0, 10.0))


# --- the axis follows the view --------------------------------------------


def test_each_view_is_revolved_about_its_own_centre_line(tmp_path):
    """The reason the sheet is divided at all.

    Two views, each with a centre line, and the longer line belongs to the
    smaller view. Read as one sheet, the longest centre line wins and is
    applied to everything, so the small outline beside it comes back as the
    part and the real one is revolved about nothing at all.

    Read as views, each outline is measured against the line drawn for it.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -6, 20, 6)
    _centre(msp, -4, 0, 24)
    # Off on its own, with a longer centre line of its own: a detail, or the
    # next part on a sheet holding two.
    _box(msp, 0, 61, 10, 63)
    _centre(msp, -30, 60, 60)

    path = _saved(doc, tmp_path)
    profile = drawing.read_profile(path)

    # 20 long and 6 in radius: the part, not the 10 by 2 beside it.
    assert drawing.section_area(profile.curves) == pytest.approx(120.0)
    assert profile.axis.point[1] == pytest.approx(0.0)


def test_a_centre_line_stays_with_the_part_it_stands_clear_of(tmp_path):
    """A bored part is nowhere near its own axis, and the axis is still its own.

    The bore is the distance between them and the bore is a feature, not a gap
    in the layout: on a thin-walled tube it is most of the drawing. So the
    centre line is matched to the part by running its length, not by lying
    near it -- a rule about distance would fail exactly where the part is
    hardest to read.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, 18, 40, 20)
    _centre(msp, -5, 0, 45)

    assert _area(doc, tmp_path) == pytest.approx(80.0)


def test_the_halves_of_a_bored_part_are_one_view(tmp_path):
    """Whitespace cuts this drawing in two and the drawing is one view.

    A turned part is drawn on both sides of its centre line, and a bore wider
    than the gap between two views puts the halves further apart than the
    layout ever would. What puts them back together is that they are each
    other's reflection in the line between them, which two stacked views never
    are.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, 18, 40, 20)
    _box(msp, 0, -20, 40, -18)
    _centre(msp, -5, 0, 45)

    path = _saved(doc, tmp_path)
    views = _views(path)

    assert len(views) == 1
    profile = drawing.read_profile(path)
    assert drawing.section_area(profile.curves) == pytest.approx(80.0)
    # Its own reflection is not another outline left on the sheet.
    assert not any("other closed outline" in note for note in profile.ignored)


# --- what is said about the rest of the sheet -----------------------------


def test_one_view_is_not_announced_as_a_choice(tmp_path):
    """Nothing was chosen, so nothing is claimed."""
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)

    profile = drawing.read_profile(_saved(doc, tmp_path))

    assert not any("groups of geometry" in note for note in profile.assumptions)


def test_the_group_that_was_read_is_said_to_have_been_chosen(tmp_path):
    """An assumption rather than a note about the sheet.

    Which group holds the part is decided here, not read off the file, and
    everything decided here travels with the model.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    _box(msp, -20, -60, 70, -40, layer="TITLE")

    profile = drawing.read_profile(_saved(doc, tmp_path))

    said = " ".join(profile.assumptions)
    assert "one of 2 groups of geometry on the sheet" in said
    assert "the one this centre line runs along" in said
    assert "1 other closed outline on the sheet" in profile.ignored


def test_outlines_left_in_other_groups_are_counted(tmp_path):
    """Counted across the whole sheet, not only within the group that was read.

    Dividing the sheet must not make it quieter. What was left on it is the
    first thing worth knowing when the answer comes out the wrong shape.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -10, 50, 10)
    _centre(msp, -8, 0, 58)
    _box(msp, -20, -60, 70, -40, layer="TITLE")
    _box(msp, -20, 40, 70, 60, layer="NOTES")

    profile = drawing.read_profile(_saved(doc, tmp_path))

    assert "2 other closed outlines on the sheet" in profile.ignored


def test_lines_crossing_in_a_group_nobody_read_do_not_refuse_the_drawing(tmp_path):
    """A stray line in the title block is not a reason to refuse the part.

    Read as one sheet it was: the outline has to be a single loop, so anywhere
    three lines met the whole drawing was turned down. Once the sheet is
    divided, that rule applies where it means something -- to the view being
    revolved -- and the rest of the sheet only has to be counted.
    """
    doc = _document()
    msp = doc.modelspace()
    _box(msp, 0, -6, 20, 6)
    _centre(msp, -4, 0, 24)
    for a, b in [((0, 40), (10, 40)), ((10, 40), (10, 50)), ((10, 50), (0, 40))]:
        msp.add_line(a, b)
    msp.add_line((10, 40), (20, 45))

    profile = drawing.read_profile(_saved(doc, tmp_path))

    assert drawing.section_area(profile.curves) == pytest.approx(120.0)
    assert "1 other closed outline on the sheet" in profile.ignored


# --- the edges ------------------------------------------------------------


def test_a_sheet_with_nothing_on_it_has_no_views():
    assert drawing.split_views([], []) == []


def test_geometry_at_one_point_is_one_view():
    """No sheet, so no layout to read: whatever it is, it is not two views."""
    curves = [drawing.Curve((0.0, 0.0), (0.0, 0.0))]
    assert len(drawing.split_views(curves, [])) == 1
