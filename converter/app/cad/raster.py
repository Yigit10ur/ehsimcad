"""Reading a turned part out of a picture of a drawing.

The third reader, and the one with the least to work with. A DXF says what each
mark is; a PDF says how each was drawn; an image says only that some pixels are
dark. Everything the other two are told has to be measured here.

Two things make it possible at all, and both come from the drawing standard
rather than from the file:

**Line weight.** An outline is drawn about twice the weight of a dimension, an
extension line or a note. Measured rather than assumed -- the stroke widths on
a sheet fall into two clear groups and the split is taken between them -- this
separates the part from the annotation as cleanly as entity types do in a DXF.

**Dash pattern.** A centre line alternates long and short marks; a hidden edge
repeats one length. The same rule the PDF reader uses, applied to runs of
pixels instead of runs of path segments.

And one thing makes it tractable: a turned part is a radius at each position
along its axis. There is no need to trace outlines or find corners -- the
profile is read off column by column, which is also why a groove is fine and an
undercut is not.

Nothing here recovers a size. An image carries no units at all, so the length
of the part has to be given; without it there is nothing to convert.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.cad.drawing import Curve, DrawingError, Point, Profile, profile_from

# Where a stroke stops being an outline and starts being an annotation. Halfway
# between the two weights actually measured on the sheet, so it needs no idea
# of resolution: what matters is that a drawing has two weights, not what they
# are in pixels.
WEIGHT_SPLIT = 0.5

# A centre line alternates two mark lengths; a hidden edge repeats one. Measured
# as the spread of the marks about their mean, exactly as in the PDF reader.
DASH_DOT_SPREAD = 0.3

# Below this a run of marks is not a line, it is a coincidence.
MIN_MARKS = 6

# How far apart two marks can be and still be one broken line, as a multiple of
# the marks themselves. Every convention makes the gap the smaller half.
MAX_GAP = 1.0


def available() -> bool:
    """Whether images can be read in this environment."""
    try:
        import PIL.Image  # noqa: F401
        import scipy.ndimage  # noqa: F401
    except ImportError:
        return False
    return True


def _threshold(grey: np.ndarray) -> int:
    """Otsu: the split that leaves the two sides as unlike each other as it can.

    A fixed threshold works on a clean export and fails on everything else,
    which is the wrong way round -- a clean export is the case that needs no
    help.
    """
    counts = np.bincount(grey.ravel(), minlength=256).astype(float)
    total = counts.sum()
    weight = np.cumsum(counts)
    moment = np.cumsum(counts * np.arange(256))

    usable = (weight > 0) & (total - weight > 0)
    variance = np.zeros(256)
    variance[usable] = ((moment[-1] * weight[usable] / total - moment[usable]) ** 2) / (
        weight[usable] * (total - weight[usable])
    )

    return int(np.argmax(variance))


def _ink(source: Path) -> np.ndarray:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(source) as handle:
            grey = np.asarray(handle.convert("L"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as error:
        raise DrawingError(
            f"this file could not be read as an image: {error}"
        ) from error

    if grey.size == 0:
        raise DrawingError("the image is empty")

    # At or below: the running totals above count every level up to and
    # including the threshold, so that is the class it names. One level either
    # way is nothing on a photograph and is everything on a drawing made of two
    # colours, where the whole of the ink sits on one level.
    return grey <= _threshold(grey)


def _stroke_widths(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How wide the stroke is at each of its centre pixels.

    The distance to the nearest white pixel is half the stroke width, and it is
    only that at the middle of the stroke -- so the middle is what is kept: the
    pixels where the distance stops growing.
    """
    from scipy import ndimage

    distance = ndimage.distance_transform_edt(ink)
    middle = ink & (distance >= ndimage.maximum_filter(distance, size=3) - 1e-9)
    return distance, middle


def _weight_split(widths: np.ndarray) -> float:
    """Where the outline stops and the annotation starts.

    A drawing is drawn in two weights, an outline about twice a dimension line,
    so the widths on a sheet fall into two groups. The split is taken between
    them by the same method as the threshold -- which means it is read off this
    drawing rather than assumed from a resolution nobody stated.

    Raises when there is only one weight to be found, because then there is no
    way to tell the part from what is written about it, and a profile built
    from dimension lines would be a confident wrong answer.
    """
    step = 0.25
    bins = np.arange(0.5, widths.max() + 2 * step, step)
    counts, edges = np.histogram(widths, bins=bins)

    total = counts.sum()
    if total == 0:
        raise DrawingError("nothing on this sheet is drawn as a line")

    centres = (edges[:-1] + edges[1:]) / 2
    weight = np.cumsum(counts)
    moment = np.cumsum(counts * centres)

    usable = (weight > 0) & (total - weight > 0)
    if not usable.any():
        raise DrawingError(_ONE_WEIGHT)

    variance = np.zeros(len(counts))
    variance[usable] = ((moment[-1] * weight[usable] / total - moment[usable]) ** 2) / (
        weight[usable] * (total - weight[usable])
    )

    cut = edges[int(np.argmax(variance)) + 1]
    thin = widths[widths < cut]
    thick = widths[widths >= cut]

    # Two groups, or one group split down the middle. A weight is a population
    # and not an outlier -- a handful of pixels where two lines cross is a
    # junction, not a heavier line -- and a drawing puts the outline at about
    # twice the rest, so anything much closer than that is one weight wearing
    # two names.
    share = min(thin.size, thick.size) / widths.size
    if share < 0.05 or thick.mean() < thin.mean() * 1.4:
        raise DrawingError(_ONE_WEIGHT)

    return float(cut)


_ONE_WEIGHT = (
    "every line on this sheet is the same weight, so there is no way to tell "
    "the outline from the dimensions. A drawing exported with line weights, or "
    "at a higher resolution, would be readable."
)


def _split(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """The sheet in two: what the part is drawn with, and everything else."""
    from scipy import ndimage

    distance, middle = _stroke_widths(ink)
    cut = _weight_split(distance[middle])

    # Grown back from the middle, because the distance is only the stroke's own
    # width along its centre -- the edges of a thick stroke are as near the
    # white as the edges of a thin one.
    heavy = (
        ndimage.binary_dilation(
            distance >= cut, ndimage.generate_binary_structure(2, 2), iterations=2
        )
        & ink
    )

    return heavy, ink & ~heavy, cut


class DashedLine:
    """A horizontal line drawn broken, and what its pattern says it is."""

    def __init__(self, row: float, x0: int, x1: int, kind: str, marks: int):
        self.row = row
        self.x0 = x0
        self.x1 = x1
        self.kind = kind
        self.marks = marks

    @property
    def length(self) -> int:
        return self.x1 - self.x0


def _dashed_lines(thin: np.ndarray, stroke: float) -> list[DashedLine]:
    """Every broken horizontal line on the sheet, centre lines and hidden edges.

    Marks are gathered by the row they sit on and read as a pattern: one length
    repeating is an edge behind material, two lengths in turn is an axis. The
    dots of a dash-dot are a pixel or two across and are the whole of the
    difference, so nothing is filtered by how long it is -- only by how tall,
    which is what separates a mark from a letter.
    """
    from scipy import ndimage

    labels, _ = ndimage.label(thin, structure=np.ones((3, 3)))
    rows: dict[int, list[tuple[int, int]]] = {}
    centres: dict[int, list[float]] = {}

    tolerance = max(2, int(round(stroke)))

    for index, box in enumerate(ndimage.find_objects(labels), start=1):
        height = box[0].stop - box[0].start
        if height > 2.5 * stroke:
            continue
        middle = (box[0].start + box[0].stop - 1) / 2
        bucket = int(round(middle / tolerance))
        rows.setdefault(bucket, []).append((box[1].start, box[1].stop - box[1].start))
        # The mark's own middle, kept so the line can be placed between the
        # pixel rows rather than on one of them: the axis is what every radius
        # is measured from, so half a pixel here is half a pixel on every
        # diameter the model reports.
        pixels = np.nonzero(labels[box] == index)[0]
        centres.setdefault(bucket, []).append(float(pixels.mean()) + box[0].start)

    lines: list[DashedLine] = []

    for bucket, marks in rows.items():
        if len(marks) < MIN_MARKS:
            continue
        marks.sort()

        lengths = np.array([width for _, width in marks], dtype=float)
        gaps = [
            marks[i + 1][0] - (marks[i][0] + marks[i][1]) for i in range(len(marks) - 1)
        ]
        if gaps and max(gaps) > MAX_GAP * np.median(lengths) + 2 * stroke:
            continue

        spread = lengths.std() / lengths.mean()
        lines.append(
            DashedLine(
                row=float(np.mean(centres[bucket])),
                x0=marks[0][0],
                x1=marks[-1][0] + marks[-1][1],
                kind="centre" if spread > DASH_DOT_SPREAD else "hidden",
                marks=len(marks),
            )
        )

    return lines


def _part_only(heavy: np.ndarray, axis_row: float) -> tuple[np.ndarray, int]:
    """The heavy marks that belong to the part, and how many did not.

    A heading and an arrowhead are drawn as heavily as an outline, and a second
    view on the same sheet is drawn exactly as heavily. What separates the part
    is not weight but the centre line: the outline is the geometry the axis
    runs through the middle of. So the mark nearest the axis is taken to be the
    part, and everything reaching the same distances from the axis, over the
    same stretch of it, is taken to be the rest of it -- its own mirror image,
    most of all.
    """
    from scipy import ndimage

    labels, count = ndimage.label(heavy, structure=np.ones((3, 3)))
    if count == 0:
        raise DrawingError("nothing on this sheet is drawn heavily enough to be a part")

    pieces = []
    for index, box in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = np.nonzero(labels[box] == index)
        if rows.size < 20:
            continue
        radius = np.abs(rows + box[0].start - axis_row)
        pieces.append(
            {
                "index": index,
                "near": float(radius.min()),
                "far": float(radius.max()),
                "x0": box[1].start,
                "x1": box[1].stop,
            }
        )

    if not pieces:
        raise DrawingError("nothing on this sheet is drawn heavily enough to be a part")

    seed = min(pieces, key=lambda piece: piece["near"])
    kept, dropped = [], 0

    for piece in pieces:
        overlaps_radius = (
            piece["near"] <= seed["far"] and piece["far"] >= seed["near"] * 0.5
        )
        overlaps_length = piece["x0"] < seed["x1"] and piece["x1"] > seed["x0"]
        if overlaps_radius and overlaps_length:
            kept.append(piece["index"])
        else:
            dropped += 1

    return np.isin(labels, kept), dropped


def _radius_profile(
    heavy: np.ndarray, middle: np.ndarray, axis_row: float
) -> tuple[np.ndarray, np.ndarray]:
    """The outer radius at every column the part covers.

    Read off rather than traced. A solid of revolution is a radius at each
    position along its axis, so the outline is whatever is furthest from the
    axis in each column -- which needs no corners found and no strokes
    followed, and is why a groove is read correctly and an undercut cannot be.
    """
    ridge = heavy & middle
    rows, cols = np.nonzero(ridge)
    if cols.size == 0:
        raise DrawingError("nothing on this sheet is drawn heavily enough to be a part")

    radius = np.abs(rows - axis_row)
    span = np.arange(cols.min(), cols.max() + 1)
    outer = np.full(span.size, np.nan)

    order = np.argsort(cols)
    cols, radius = cols[order], radius[order]
    starts = np.searchsorted(cols, span, side="left")
    ends = np.searchsorted(cols, span, side="right")

    for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if end > start:
            outer[index] = radius[start:end].max()

    return span.astype(float), outer


def _fill_gaps(values: np.ndarray) -> np.ndarray:
    """Columns a dash happened to miss, filled from their neighbours."""
    known = ~np.isnan(values)
    if not known.any():
        raise DrawingError("the outline is broken all the way along")
    return np.interp(np.arange(values.size), np.flatnonzero(known), values[known])


def _corners(points: list[Point], shortest: float) -> list[Point]:
    """The rounding a stroke leaves at a corner, taken back off.

    A line has width, so where the outline turns, the middle of the stroke
    turns with it over about that width, and where it ends, the cap pulls the
    middle in. Read literally that is a small chamfer at every step and a taper
    at both ends, and neither is on the drawing.

    Anything shorter than a few stroke widths is therefore not an edge: it is
    the corner between the two edges either side, and it is replaced by where
    they actually meet. A chamfer a draughtsman drew is many times longer than
    this and survives.
    """
    while len(points) > 3:
        lengths = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
        shortest_at = min(range(len(lengths)), key=lengths.__getitem__)
        if lengths[shortest_at] >= shortest:
            break

        if shortest_at == 0:
            # No edge before it: the far end of the drawing, where the cap
            # pulls in. Carry the edge after it back out to where the part ends.
            after = _extend(points[1], points[2], points[0][0])
            points = [after] + points[2:]
        elif shortest_at == len(lengths) - 1:
            before = _extend(points[-2], points[-3], points[-1][0])
            points = points[:-2] + [before]
        else:
            meeting = _meet(
                points[shortest_at - 1],
                points[shortest_at],
                points[shortest_at + 1],
                points[shortest_at + 2],
            )
            if meeting is None:
                break
            points = points[:shortest_at] + [meeting] + points[shortest_at + 2 :]

    return points


def _extend(near: Point, far: Point, to_x: float) -> Point:
    """The point on the line through two points, at a given position along."""
    if abs(far[0] - near[0]) < 1e-9:
        return (to_x, near[1])
    slope = (far[1] - near[1]) / (far[0] - near[0])
    return (to_x, near[1] + slope * (to_x - near[0]))


def _meet(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Where the line through a and b crosses the line through c and d."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-9:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denominator
    return (a[0] + t * r[0], a[1] + t * r[1])


def _simplify(xs: np.ndarray, ys: np.ndarray, tolerance: float) -> list[Point]:
    """The fewest corners that stay within `tolerance` of the measured profile."""
    points = [(float(x), float(y)) for x, y in zip(xs, ys, strict=True)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True

    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue

        ax, ay = points[first]
        bx, by = points[last]
        length = math.hypot(bx - ax, by - ay)

        worst, at = 0.0, first
        for index in range(first + 1, last):
            px, py = points[index]
            if length < 1e-9:
                gap = math.hypot(px - ax, py - ay)
            else:
                gap = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
            if gap > worst:
                worst, at = gap, index

        if worst > tolerance:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))

    return [point for point, wanted in zip(points, keep, strict=True) if wanted]


def _square_up(points: list[Point], tolerance: float) -> tuple[list[Point], int]:
    """Edges very nearly along or across the axis, made exactly so.

    The tolerance is in pixels, not degrees, because what is being allowed for
    is the error in reading a stroke off a picture -- and that is a fixed
    number of pixels however long the edge is. A degree of slack would let a
    long edge wander much further than a short one, and it is the long ones
    that become the cylinders.

    A cylinder read a degree out of true is a cone: a different face, a
    diameter that differs end to end, and a measurement that disagrees with the
    drawing it came from. Anything visibly tapered is left alone.
    """
    squared = list(points)
    changed = 0

    for index in range(len(squared) - 1):
        (x1, y1), (x2, y2) = squared[index], squared[index + 1]
        run, rise = x2 - x1, y2 - y1

        if abs(rise) < tolerance <= abs(run):
            level = (y1 + y2) / 2
            squared[index] = (x1, level)
            squared[index + 1] = (x2, level)
            changed += 1
        elif abs(run) < tolerance <= abs(rise):
            level = (x1 + x2) / 2
            squared[index] = (level, y1)
            squared[index + 1] = (level, y2)
            changed += 1

    return squared, changed


def _inner_profile(
    hidden: list[DashedLine], span: np.ndarray, axis_row: float, reach: float
) -> np.ndarray:
    """How far the material stops short of the axis, column by column.

    A bore is drawn as a hidden edge, so the nearest hidden edge to the axis is
    where the material ends. Where nothing is hidden the part is solid and the
    profile runs to the axis.
    """
    inner = np.zeros(span.size)

    for line in hidden:
        radius = abs(line.row - axis_row)
        covered = (span >= line.x0) & (span <= line.x1)
        # The nearest to the axis wins: a deeper bore is still a bore.
        inner = np.where(covered & ((inner == 0) | (radius < inner)), radius, inner)

    return _reach_the_ends(inner, reach)


def _reach_the_ends(inner: np.ndarray, reach: float) -> np.ndarray:
    """A bore drawn a few pixels short of the face, carried out to it.

    The cap on a hidden line stops short of the end of the part for the same
    reason the outline's corner rounds: the stroke has width. Left as read, a
    through bore becomes a bore with a lid at each end -- a wall a fraction of
    a millimetre thick, and two faces that are not on the drawing.
    """
    filled = inner.copy()
    inside = np.flatnonzero(filled > 0)
    if inside.size == 0:
        return filled

    first, last = int(inside[0]), int(inside[-1])
    if first <= reach:
        filled[:first] = filled[first]
    if filled.size - 1 - last <= reach:
        filled[last + 1 :] = filled[last]

    return filled


def read_curves(source: Path) -> tuple[list[Curve], list[Curve], str, list[str]]:
    """A picture of a drawing as outline curves and a centre line, in pixels."""
    ink = _ink(source)
    heavy, light, weight = _split(ink)
    _, middle = _stroke_widths(ink)

    dashed = sorted(_dashed_lines(light, weight), key=lambda line: -line.length)
    centre = [line for line in dashed if line.kind == "centre"]
    if not centre:
        raise DrawingError(
            "no centre line was found. A turned part is read about its axis, "
            "and the axis has to be drawn as a chain line -- long and short "
            "marks in turn -- for there to be one to read."
        )

    axis = centre[0]
    part, dropped = _part_only(heavy, axis.row)
    span, outer = _radius_profile(part, middle, axis.row)
    outer = _fill_gaps(outer)

    # Half a stroke is the most a corner can be out, and the profile is read to
    # the middle of the stroke, so twice that is generous and still far short
    # of anything a draughtsman would draw on purpose.
    slack = 4 * weight

    corners = _square_up(_corners(_simplify(span, outer, weight), slack), slack)[0]
    inner = _inner_profile(
        [line for line in dashed if line.kind == "hidden"], span, axis.row, slack
    )

    left, right = corners[0][0], corners[-1][0]
    bore_left = float(np.interp(left, span, inner))
    bore_right = float(np.interp(right, span, inner))

    loop: list[Point] = [(left, bore_left), *corners, (right, bore_right)]
    if bore_left > 0 or bore_right > 0:
        loop.append((left, bore_left))

    outline = [
        Curve(a, b)
        for a, b in zip(loop, loop[1:], strict=False)
        if math.dist(a, b) > 1e-9
    ]

    ignored: list[str] = []
    if dropped:
        ignored.append(
            "1 other heavy mark on the sheet"
            if dropped == 1
            else f"{dropped} other heavy marks on the sheet"
        )
    lighter = (
        len(dashed)
        - len(centre)
        - len([line for line in dashed if line.kind == "hidden"])
    )
    if lighter:
        ignored.append(f"{lighter} other broken line(s)")

    axis_curve = Curve((float(axis.x0), 0.0), (float(axis.x1), 0.0))
    return outline, [axis_curve], "pixels", ignored


def read_profile(source: Path, length_mm: float | None = None) -> Profile:
    """A picture of a drawing, read down to the outline to revolve.

    `length_mm` is not optional here, and that is the difference between this
    and the other two readers. A DXF states its units and a printed sheet at
    least states a paper size; an image states nothing at all -- the same
    picture is a bolt or a bridge, and there is nothing in it to say which.
    """
    if length_mm is None:
        raise DrawingError(
            "the length of the part is needed to read a picture. An image has "
            "no units in it: nothing says whether it shows something 90 mm "
            "long or 900."
        )
    if length_mm <= 0:
        raise DrawingError("the length of the part has to be more than zero")

    profile = profile_from(*read_curves(source))

    extent = max(p[0] for c in profile.curves for p in (c.start, c.end)) - min(
        p[0] for c in profile.curves for p in (c.start, c.end)
    )
    if extent <= 0:
        raise DrawingError("the outline has no length along its axis")

    factor = length_mm / extent

    return Profile(
        axis=type(profile.axis)(
            point=(profile.axis.point[0] * factor, profile.axis.point[1] * factor),
            direction=profile.axis.direction,
            found_by=profile.axis.found_by,
        ),
        curves=[curve.scaled(factor) for curve in profile.curves],
        units="mm",
        assumptions=[
            f"read from a picture, scaled so the part is {length_mm:g} mm long",
            "the heavier lines were taken to be the part and the lighter ones "
            "to be dimensions and notes",
            *profile.assumptions,
        ],
        ignored=profile.ignored,
    )


def convert(
    source: Path,
    out_glb: Path,
    deflection: float | None = None,
    length_mm: float | None = None,
):
    """A picture of a turned part, as far as a .glb the viewer opens."""
    from app.cad import drawing, occt
    from app.models import DerivedGeometry

    profile = read_profile(source, length_mm)
    solid = drawing.revolve(profile)

    part = occt.Part(id="n1", name=source.stem, shape=solid, color=None, named=False)

    return occt.build(
        [part],
        out_glb,
        deflection,
        geometry_source="derived",
        derived=DerivedGeometry(
            method="raster-revolve",
            axis_point=(profile.axis.point[0], profile.axis.point[1], 0.0),
            axis_direction=(*profile.axis.direction, 0.0),
            assumptions=profile.assumptions,
            ignored=profile.ignored,
        ),
    )
