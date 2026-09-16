"""Edges that were drawn and became nothing.

An outline is assembled end to end. An edge that stops partway along another
joins nothing at either end, closes into no loop, and is left out -- and for a
long time was left out in silence.

That silence is what these are about. A bored shaft drawn as an outside view
has its bore shown with two hidden lines meeting the end faces in the middle,
which is how such a view is drawn and not a mistake. The outline closes without
them, and what comes back is a solid cylinder: not an obviously broken model,
a convincing one weighing a fifth more than the part. Nothing said so.

It still comes back. Which edge mattered is not something the geometry says --
the same leftover is a keyway shown with hidden lines, deliberately not cut --
so the reading is not refused. What changed is that the sheet now says an edge
was drawn and not used, which is enough to weigh the section against the
drawing and catch it.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest

from app.cad import drawing, occt

FIXTURES = Path(__file__).parent / "fixtures"

# A 100 long, 20 across shaft with an 8 bore through it.
BORED_VOLUME = math.pi * (10**2 - 4**2) * 100
SOLID_VOLUME = math.pi * 10**2 * 100


def _document():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("CENTER", linetype="CENTER")
    doc.layers.add("HIDDEN", linetype="HIDDEN")
    return doc


def _saved(doc, tmp_path: Path, name: str = "case.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _rect(msp, x0: float, y0: float, x1: float, y1: float) -> None:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for a, b in zip(corners, corners[1:], strict=False):
        msp.add_line(a, b)


def _outside_view():
    """The bore meeting the end faces partway, as an outside view draws it."""
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, -10, 100, 10)
    msp.add_line((0, 4), (100, 4), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((0, -4), (100, -4), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((-5, 0), (105, 0), dxfattribs={"layer": "CENTER"})
    return doc


def _section_view():
    """The same part with the end faces split where the bore meets them."""
    doc = _document()
    msp = doc.modelspace()
    for a, b in [
        ((0, 4), (0, 10)),
        ((0, 10), (100, 10)),
        ((100, 10), (100, 4)),
        ((0, -4), (0, -10)),
        ((0, -10), (100, -10)),
        ((100, -10), (100, -4)),
    ]:
        msp.add_line(a, b)
    msp.add_line((0, 4), (100, 4), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((0, -4), (100, -4), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((-5, 0), (105, 0), dxfattribs={"layer": "CENTER"})
    return doc


def _left_out(read) -> str:
    # "closes" for one and "close" for several, so matched on neither.
    return " ".join(note for note in read.ignored if "into no outline" in note)


# --- what is said ---------------------------------------------------------


def test_an_edge_that_joins_nothing_is_counted(tmp_path):
    read = drawing.read_part(_saved(_outside_view(), tmp_path))

    assert _left_out(read) == "1 edge that closes into no outline, left out"


def test_the_same_part_drawn_end_to_end_says_nothing_of_the_kind(tmp_path):
    """The other half of the test above, and the one that keeps it honest.

    A note on every drawing would be no note at all.
    """
    read = drawing.read_part(_saved(_section_view(), tmp_path))

    assert _left_out(read) == ""
    assert any("bored" in note for note in read.assumptions)


def test_several_are_counted_and_said_as_several(tmp_path):
    doc = _outside_view()
    msp = doc.modelspace()
    # A groove drawn on the face and joined to nothing: two more edges, above
    # the centre line so they land on the side being read.
    msp.add_line((40, 6), (60, 6), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((40, 8), (60, 8), dxfattribs={"layer": "HIDDEN"})

    read = drawing.read_part(_saved(doc, tmp_path))

    assert _left_out(read) == "3 edges that close into no outline, left out"


def test_it_is_said_among_what_was_ignored_not_among_the_assumptions(tmp_path):
    # An assumption is something the reading did. This is something it did not
    # do, which is the other list.
    read = drawing.read_part(_saved(_outside_view(), tmp_path))

    assert not any("into no outline" in note for note in read.assumptions)


# --- what is not said -----------------------------------------------------


@pytest.mark.parametrize("name", ["plain_shaft.dxf", "stepped_shaft.dxf"])
def test_an_ordinary_sheet_gains_no_note(name):
    """Including one whose bore is drawn, joined, and read correctly."""
    read = drawing.read_part(FIXTURES / name)

    assert _left_out(read) == ""


def test_the_half_that_was_not_revolved_is_not_missing(tmp_path):
    """A drawing draws both sides of the centre line.

    One of them is revolved and the other is its reflection. Counting the
    unused half as edges left out would put a note on every drawing ever made.
    """
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, -10, 100, 10)
    msp.add_line((-5, 0), (105, 0), dxfattribs={"layer": "CENTER"})

    read = drawing.read_part(_saved(doc, tmp_path))

    assert _left_out(read) == ""


# --- the same, in the other reading ---------------------------------------


def test_a_flat_part_counts_them_too(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, 0, 100, 60)
    msp.add_circle((50, 30), 6)
    # A hidden edge of something that stops partway through the plate.
    msp.add_line((20, 0), (20, 60), dxfattribs={"layer": "HIDDEN"})
    _rect(msp, 0, -20, 100, -12)

    read = drawing.read_part(_saved(doc, tmp_path))

    assert isinstance(read, drawing.Prism)
    assert _left_out(read) == "1 edge that closes into no outline, left out"


def test_a_flat_part_with_nothing_loose_gains_no_note(tmp_path):
    doc = _document()
    msp = doc.modelspace()
    _rect(msp, 0, 0, 100, 60)
    msp.add_circle((50, 30), 6)
    _rect(msp, 0, -20, 100, -12)

    read = drawing.read_part(_saved(doc, tmp_path))

    assert _left_out(read) == ""


# --- what it still gets wrong ---------------------------------------------


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
def test_the_bore_is_still_lost_and_now_says_so(tmp_path):
    """The limit this note exists to make visible, written down as it is.

    The solid is the one the outline describes, and the outline is missing the
    bore: 31415.927 where the part is 26389.378, a fifth heavy. Reading it as
    bored would mean deciding that this loose edge is a bore and the keyway's
    is not, which the drawing does not say.

    What the sheet does now is show its working: four edges enclosing 1000 mm2
    of section, and one edge drawn that closed into nothing.
    """
    out = tmp_path / "shaft.glb"
    result = drawing.convert(_saved(_outside_view(), tmp_path), out)

    volume = result.metadata.parts["n1"].volume_mm3
    assert volume == pytest.approx(SOLID_VOLUME)
    assert volume > BORED_VOLUME

    said = " ".join(result.metadata.derived.ignored)
    assert "1 edge that closes into no outline, left out" in said
    assert any(
        "1000.0 mm2 of section" in note for note in result.metadata.derived.assumptions
    )
