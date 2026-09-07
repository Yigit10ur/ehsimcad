"""A printed sheet converted the whole way.

Skipped without OCCT, like the other geometry tests. The point of these is that
a print of a drawing and the drawing itself produce the same solid: nothing in
the two readers is shared except the answer.
"""

from __future__ import annotations

import json
from math import pi
from pathlib import Path

import pytest

from app.cad import drawing, occt, pdf

pytestmark = pytest.mark.skipif(
    not occt.available(), reason="OCCT bindings not installed"
)

FIXTURES = Path(__file__).parent / "fixtures"

STEPPED_VOLUME = pi * (10**2 * 30 + 15**2 * 40 + 8**2 * 20) - pi * 4**2 * 90

# A cylinder of radius 10 over 50, with the last 5 rounded off at radius 5:
#   pi * 100 * 45 for the plain part, and for the rounded end
#   pi * integral of (5 + sqrt(25 - u^2))^2 du, u = 0..5
# which is pi * (125 + 10 * 25pi/4 + 125 - 125/3).
FILLETED_VOLUME = pi * (100 * 45 + 125 + 62.5 * pi + 125 - 125 / 3)


@pytest.mark.parametrize(
    "name", ["stepped_shaft_printed.pdf", "stepped_shaft_plotted.pdf"]
)
def test_a_print_of_the_drawing_gives_the_drawing_s_part(tmp_path, name):
    out = tmp_path / "printed.glb"
    result = pdf.convert(FIXTURES / name, out, length_mm=90)

    assert result.metadata.parts["n1"].volume_mm3 == pytest.approx(STEPPED_VOLUME)
    kinds = sorted(f.kind for f in result.metadata.snap["n1"].faces)
    assert kinds == ["cylinder"] * 4 + ["plane"] * 4


def test_the_print_and_the_drawing_agree(tmp_path):
    """The same part twice, read by two things with nothing in common.

    One reads entity types, layers and linetypes out of a DXF. The other reads
    stroked paths out of a print of that DXF and works out the rest. If either
    is wrong, this is where it shows.
    """
    from_drawing = drawing.convert(FIXTURES / "stepped_shaft.dxf", tmp_path / "d.glb")
    from_print = pdf.convert(
        FIXTURES / "stepped_shaft_printed.pdf", tmp_path / "p.glb", length_mm=90
    )

    assert from_print.metadata.parts["n1"].volume_mm3 == pytest.approx(
        from_drawing.metadata.parts["n1"].volume_mm3
    )
    assert sorted(f.kind for f in from_print.metadata.snap["n1"].faces) == sorted(
        f.kind for f in from_drawing.metadata.snap["n1"].faces
    )
    radii = sorted(
        round(f.radius, 3)
        for f in from_print.metadata.snap["n1"].faces
        if f.radius is not None
    )
    assert radii == [4.0, 8.0, 10.0, 15.0]


def test_a_fillet_becomes_one_curved_face(tmp_path):
    out = tmp_path / "filleted.glb"
    result = pdf.convert(FIXTURES / "filleted_shaft_plotted.pdf", out, length_mm=50)

    assert result.metadata.parts["n1"].volume_mm3 == pytest.approx(
        FILLETED_VOLUME, rel=1e-4
    )
    kinds = sorted(f.kind for f in result.metadata.snap["n1"].faces)
    # One cylinder, two flat ends, and the fillet -- which is a torus, and so
    # not one of the kinds the metadata names. One of it, not two.
    assert kinds == ["cylinder", "other", "plane", "plane"]


def test_it_is_labelled_derived_and_says_it_came_from_a_sheet(tmp_path):
    out = tmp_path / "printed.glb"
    pdf.convert(FIXTURES / "stepped_shaft_printed.pdf", out, length_mm=90)
    metadata = json.loads(out.with_suffix(".json").read_text())

    assert metadata["geometry_source"] == "derived"
    assert metadata["derived"]["method"] == "pdf-revolve"

    said = " ".join(metadata["derived"]["assumptions"])
    assert "90 mm along its axis" in said
    assert "solid of revolution" in said
    assert any("filled shape" in note for note in metadata["derived"]["ignored"])


def test_the_face_groups_tile_the_triangles(tmp_path):
    out = tmp_path / "printed.glb"
    pdf.convert(FIXTURES / "stepped_shaft_printed.pdf", out, length_mm=90)
    metadata = json.loads(out.with_suffix(".json").read_text())

    groups = metadata["face_groups"]["n1"]
    assert len(groups) == len(metadata["snap"]["n1"]["faces"])
    assert groups[0][0] == 0
    for (_, end), (start, _) in zip(groups, groups[1:], strict=False):
        assert start == end


def test_a_picture_of_the_drawing_gives_the_drawing_s_part(tmp_path):
    """The third reader, held to the same shaft as the other two.

    A picture is the least a drawing can arrive as, and it shows: this is right
    to within a percent rather than to the eighth decimal place. What it gets
    exactly is the shape -- the same eight edges, the same four cylinders and
    four flat faces -- and that is the part of the answer somebody looks at.
    """
    from app.cad import raster

    out = tmp_path / "scan.glb"
    result = raster.convert(FIXTURES / "stepped_shaft_scan.jpg", out, length_mm=90)

    volume = result.metadata.parts["n1"].volume_mm3
    assert volume == pytest.approx(STEPPED_VOLUME, rel=0.02)

    kinds = sorted(f.kind for f in result.metadata.snap["n1"].faces)
    assert kinds == ["cylinder"] * 4 + ["plane"] * 4

    assert result.metadata.geometry_source == "derived"
    assert result.metadata.derived.method == "raster-revolve"
    said = " ".join(result.metadata.derived.assumptions)
    assert "read from a picture" in said
    assert "heavier lines" in said
