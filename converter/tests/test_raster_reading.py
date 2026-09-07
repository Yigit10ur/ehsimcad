"""Reading a turned part out of a picture of a drawing.

Runs without OpenCascade. What can be wrong here without anything looking wrong
is which marks were taken for the part and which line was taken for the axis --
and a picture gives less to go on than either of the other two readers, so the
cases below are the ones where being wrong is quiet.

The checked-in fixtures are a real render of the same shaft the DXF and PDF
fixtures draw, once as PNG and once as JPEG. The sheets drawn here are a few
lines each, so a single case can be stated exactly: this line weight, this dash
pattern, nothing else on the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cad import drawing, raster

FIXTURES = Path(__file__).parent / "fixtures"
SCAN = FIXTURES / "stepped_shaft_scan.png"
SCAN_JPEG = FIXTURES / "stepped_shaft_scan.jpg"

# The shaft the fixtures draw: 10 for 30, 15 for 40, 8 for 20, bored 4 through.
SECTION = 30 * 10 + 40 * 15 + 20 * 8 - 90 * 4


def sheet(
    tmp_path: Path,
    *,
    outline: list[tuple[float, float, float, float]],
    axis: tuple[float, float] | None = (20, 780),
    hidden: list[tuple[float, float, float]] | None = None,
    extras: list[tuple[float, float, float, float, int]] | None = None,
    name: str = "sheet.png",
) -> Path:
    """A drawing, drawn exactly.

    Outlines go on heavy, everything else light, because that difference is
    what the reader has to see -- and a plotting library will not agree to put
    one exact thing on a page.
    """
    from PIL import Image, ImageDraw

    image = Image.new("L", (1000, 600), color=255)
    draw = ImageDraw.Draw(image)

    for x1, y1, x2, y2 in outline:
        draw.line((x1, y1, x2, y2), fill=0, width=6)

    if axis is not None:
        x, row = axis
        while x < 980:
            draw.line((x, row, x + 24, row), fill=0, width=3)
            x += 30
            draw.line((x, row, x + 3, row), fill=0, width=3)
            x += 9

    for x1, x2, row in hidden or []:
        x = x1
        while x < x2:
            draw.line((x, row, min(x + 12, x2), row), fill=0, width=3)
            x += 18

    for x1, y1, x2, y2, width in extras or []:
        draw.line((x1, y1, x2, y2), fill=0, width=width)

    path = tmp_path / name
    image.save(path)
    return path


# A plain cylinder: 800 across, 200 either side of the axis at row 300.
PLAIN = [
    (100, 100, 900, 100),
    (100, 500, 900, 500),
    (100, 100, 100, 500),
    (900, 100, 900, 500),
]


def _plain(tmp_path: Path, **kwargs) -> Path:
    kwargs.setdefault("axis", (20, 300))
    return sheet(tmp_path, outline=PLAIN, **kwargs)


# --- the shaft, from a real render ----------------------------------------


@pytest.mark.parametrize("source", [SCAN, SCAN_JPEG], ids=["png", "jpeg"])
def test_the_shaft_is_read_off_the_picture(source):
    profile = raster.read_profile(source, length_mm=90)

    assert drawing.section_area(profile.curves) == pytest.approx(SECTION, rel=0.01)
    assert len(profile.curves) == 8


def test_the_png_and_the_jpeg_are_read_the_same():
    """Compression puts a halo round every line; the threshold has to see past it."""
    clean = raster.read_profile(SCAN, length_mm=90)
    compressed = raster.read_profile(SCAN_JPEG, length_mm=90)

    assert drawing.section_area(compressed.curves) == pytest.approx(
        drawing.section_area(clean.curves), rel=1e-3
    )


def test_the_steps_come_out_square():
    """A cylinder read a degree out of true is a cone.

    Every edge of a turned part is along the axis or across it unless the part
    is tapered, and this one is not. A degree of slope on the long edges would
    be four different diameters where the drawing has one.
    """
    profile = raster.read_profile(SCAN, length_mm=90)

    for curve in profile.curves:
        run = abs(curve.end[0] - curve.start[0])
        rise = abs(curve.end[1] - curve.start[1])
        assert min(run, rise) < 1e-6, f"{curve.start} -> {curve.end} is neither"


def test_the_bore_is_read_from_the_hidden_line():
    """Without it the shaft is solid, and solid is 50% more material."""
    profile = raster.read_profile(SCAN, length_mm=90)
    radii = sorted({round(p[1], 2) for c in profile.curves for p in (c.start, c.end)})

    assert radii[0] == pytest.approx(4.0, abs=0.1)


def test_the_dimensions_and_the_note_are_not_part_of_the_part():
    """They are drawn as heavily as an arrowhead, and are not the outline."""
    profile = raster.read_profile(SCAN, length_mm=90)

    assert any("heavy mark" in note for note in profile.ignored)
    # The note sits far out from the axis; if it had been read the part would
    # be several times its radius.
    assert max(p[1] for c in profile.curves for p in (c.start, c.end)) < 20


# --- the axis --------------------------------------------------------------


def test_a_chain_line_is_the_axis(tmp_path):
    profile = raster.read_profile(_plain(tmp_path), length_mm=80)

    assert profile.axis.found_by == "centre line"
    # 800 px over 80 mm, and 200 px of radius.
    assert drawing.section_area(profile.curves) == pytest.approx(80 * 20, rel=0.02)


def test_a_sheet_with_no_chain_line_is_refused(tmp_path):
    # With a dimension line on it, so that the sheet still has two weights and
    # it is the missing axis being refused rather than the missing contrast.
    path = _plain(tmp_path, axis=None, extras=[(100, 60, 900, 60, 3)])

    with pytest.raises(drawing.DrawingError, match="no centre line"):
        raster.read_profile(path, length_mm=80)


def test_an_evenly_dashed_line_is_not_the_axis(tmp_path):
    """A hidden edge repeats one length; an axis alternates two."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (1000, 600), color=255)
    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2 in PLAIN:
        draw.line((x1, y1, x2, y2), fill=0, width=6)
    x = 20
    while x < 980:  # evenly dashed, right across the part
        draw.line((x, 300, x + 12, 300), fill=0, width=3)
        x += 18
    path = tmp_path / "dashed.png"
    image.save(path)

    with pytest.raises(drawing.DrawingError, match="no centre line"):
        raster.read_profile(path, length_mm=80)


# --- what makes it readable at all ----------------------------------------


def test_a_sheet_drawn_in_one_weight_is_refused(tmp_path):
    """Then there is nothing to tell the part from what is written about it.

    Refused rather than guessed: a profile built from dimension lines is a
    confident wrong answer, and the wrong answers are the dangerous ones.
    """
    thin = [(x1, y1, x2, y2, 3) for x1, y1, x2, y2 in PLAIN]
    path = sheet(tmp_path, outline=[], axis=(20, 300), extras=thin)

    with pytest.raises(drawing.DrawingError, match="same weight"):
        raster.read_profile(path, length_mm=80)


def test_a_file_that_is_not_an_image_is_refused(tmp_path):
    path = tmp_path / "not.png"
    path.write_text("this is not a drawing")

    with pytest.raises(drawing.DrawingError, match="could not be read as an image"):
        raster.read_profile(path, length_mm=80)


# --- size ------------------------------------------------------------------


def test_a_picture_cannot_be_read_without_a_length(tmp_path):
    """The one refusal that is about the format rather than the drawing.

    A DXF states its units and a printed sheet states a paper size. A picture
    states nothing: the same image is a bolt or a bridge.
    """
    with pytest.raises(drawing.DrawingError, match="no units in it"):
        raster.read_profile(_plain(tmp_path))


def test_a_length_that_is_not_a_length_is_refused(tmp_path):
    with pytest.raises(drawing.DrawingError, match="more than zero"):
        raster.read_profile(_plain(tmp_path), length_mm=-5)


def test_the_length_sets_the_scale(tmp_path):
    """Twice as long is four times the section, and nothing else changes."""
    small = raster.read_profile(_plain(tmp_path), length_mm=80)
    large = raster.read_profile(_plain(tmp_path, name="big.png"), length_mm=160)

    assert drawing.section_area(large.curves) == pytest.approx(
        4 * drawing.section_area(small.curves), rel=1e-6
    )
