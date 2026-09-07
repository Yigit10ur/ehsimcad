"""Reading a turned part out of a printed sheet.

The same job as `drawing.py` and the same answer at the end: curves, a centre
line, a profile, a revolve. Only the reading differs, and it differs in one way
that matters.

A DXF says what each mark *is* -- a dimension is a `DIMENSION`, a note is a
`TEXT`. A PDF says only how each mark was drawn. So the filters here are about
how, not what: glyphs and arrowheads are filled and never stroked, which
separates every letter and every arrow from every line in one test; and a
dashed line arrives as a row of short strokes, which is how the centre line is
found again.

What a PDF keeps that an image throws away is everything else. The outline is
still lines and curves with real coordinates, so nothing here is a guess about
where an edge is -- only about what the drawing meant, which is the same guess
`drawing.py` makes.

Size is the one thing a sheet is vaguer about than a DXF: a print carries the
part at whatever scale it was plotted. Full size is assumed and said out loud;
one known length overrides it.
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path

from app.cad.drawing import (
    Curve,
    DrawingError,
    Point,
    Profile,
    profile_from,
    tolerance_for,
)

# PDF user space is 1/72 inch, whatever was drawn in it.
PT_TO_MM = 25.4 / 72

# A dashed line is many short strokes in a row. Fewer than this and it is more
# likely to be two collinear edges of the part, which must not be joined.
MIN_DASHES = 4

# Dash-dot alternates long and short; a plain dashed line does not. Measured as
# the spread of the stroke lengths against their mean, which needs no idea of
# what "long" is in a drawing of unknown size.
DASH_DOT_SPREAD = 0.3


def available() -> bool:
    """Whether the PDF reader can be imported in this environment."""
    try:
        import pdfminer.high_level  # noqa: F401
    except ImportError:
        return False
    return True


def _circle_through(a: Point, b: Point, c: Point) -> tuple[Point, float] | None:
    """The circle exactly through three points, or None if they are in a line.

    Exactness at the ends is what this is for. An arc is built from a circle
    and two points on it, so a circle that misses its own endpoints by a
    thousandth is not an arc OCCT will build -- and a fitted circle always
    misses them by something.
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = (
        (ax**2 + ay**2) * (by - cy)
        + (bx**2 + by**2) * (cy - ay)
        + (cx**2 + cy**2) * (ay - by)
    ) / d
    uy = (
        (ax**2 + ay**2) * (cx - bx)
        + (bx**2 + by**2) * (ax - cx)
        + (cx**2 + cy**2) * (bx - ax)
    ) / d
    return (ux, uy), math.dist((ux, uy), a)


def _fit_circle(points: list[Point]) -> tuple[Point, float] | None:
    """The circle that best passes through all the points, or None.

    Least squares over every sample rather than exactly through three of them:
    three points lie on a circle whichever curve they came from, and the cubics
    a PDF draws an arc with are themselves an approximation, so a fit that uses
    all of them lands nearer the radius the drawing meant.
    """
    n = len(points)
    if n < 3:
        return None

    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    us = [p[0] - mx for p in points]
    vs = [p[1] - my for p in points]

    suu = sum(u * u for u in us)
    svv = sum(v * v for v in vs)
    suv = sum(u * v for u, v in zip(us, vs, strict=True))
    suuu = sum(u**3 for u in us)
    svvv = sum(v**3 for v in vs)
    suvv = sum(u * v * v for u, v in zip(us, vs, strict=True))
    svuu = sum(v * u * u for u, v in zip(us, vs, strict=True))

    determinant = 2 * (suu * svv - suv * suv)
    if abs(determinant) < 1e-18:
        return None

    cu = (svv * (suuu + suvv) - suv * (svvv + svuu)) / determinant
    cv = (suu * (svvv + svuu) - suv * (suuu + suvv)) / determinant
    centre = (cu + mx, cv + my)
    radius = sum(math.dist(centre, p) for p in points) / n
    return centre, radius


def _bezier(p0: Point, c1: Point, c2: Point, p3: Point, t: float) -> Point:
    u = 1 - t
    return (
        u**3 * p0[0] + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * p3[1],
    )


def _arc_or_lines(p0: Point, c1: Point, c2: Point, p3: Point) -> list[Curve]:
    """A cubic as the arc it was, or as straight runs when it was not one.

    A fillet leaves the CAD application as a circular arc and reaches the PDF as
    two or three cubics, because that is all a PDF can draw. Fitting the circle
    back is what keeps a fillet a fillet instead of a row of chords -- the same
    thing the DXF reader gets for free from the file saying `ARC`.
    """
    samples = [_bezier(p0, c1, c2, p3, t / 8) for t in range(9)]
    fitted = _fit_circle(samples)

    # Two circles, and they do different jobs. The fitted one answers "is this
    # cubic a piece of a circle at all", using every sample -- checked against
    # each of them, because a straight run fits a circle of enormous radius
    # perfectly well and calling that an arc would put a curve where the
    # drawing has an edge.
    if fitted is not None:
        centre, radius = fitted
        if radius > 1e-9 and all(
            abs(math.dist(centre, p) - radius) < radius * 1e-3 for p in samples
        ):
            # The one that gets built goes exactly through the ends.
            exact = _circle_through(p0, samples[4], p3)
            if exact is not None:
                cross = (samples[4][0] - p0[0]) * (p3[1] - samples[4][1]) - (
                    samples[4][1] - p0[1]
                ) * (p3[0] - samples[4][0])
                return [Curve(p0, p3, exact[0], ccw=cross > 0)]

    return [Curve(a, b) for a, b in zip(samples, samples[1:], strict=False)]


def _curves_of(item) -> list[Curve]:
    """One drawn path as straight runs and arcs.

    Read from `original_path` rather than from the flattened points, which are
    the control points of any cubic -- following those draws the control
    polygon, which is a different and wrong shape.
    """
    path = getattr(item, "original_path", None)
    if not path:
        return []

    out: list[Curve] = []
    start: Point | None = None
    here: Point | None = None

    for segment in path:
        op, points = segment[0], [tuple(p) for p in segment[1:]]
        if op == "m" and points:
            here = start = points[0]
        elif op == "l" and points and here is not None:
            if math.dist(here, points[0]) > 1e-9:
                out.append(Curve(here, points[0]))
            here = points[0]
        elif op == "c" and len(points) == 3 and here is not None:
            out.extend(_arc_or_lines(here, *points))
            here = points[2]
        elif op == "h" and here is not None and start is not None:
            if math.dist(here, start) > 1e-9:
                out.append(Curve(here, start))
            here = start

    return _merge_arcs(out)


def _same_circle(one: Curve, other: Curve) -> bool:
    """Whether two arcs are pieces of the same circle.

    Compared against the radius rather than against an absolute figure: each
    piece is fitted separately, so their centres agree to the accuracy of the
    fit and not to the last bit. A tolerance tight enough to be exact rejects
    every real pair and leaves every fillet in halves.
    """
    r1 = math.dist(one.centre, one.start)  # type: ignore[arg-type]
    r2 = math.dist(other.centre, other.start)  # type: ignore[arg-type]
    if abs(r1 - r2) > max(r1, r2) * 1e-3:
        return False
    return math.dist(one.centre, other.centre) < max(r1, r2) * 1e-3  # type: ignore[arg-type]


def _merge_arcs(curves: list[Curve]) -> list[Curve]:
    """Adjacent pieces of one circle, put back together.

    A PDF cannot draw a quarter circle in one go: it comes as two or three
    cubics, and each fits the same circle. Left apart they revolve into two
    faces where the part has one, so a fillet would be half selectable and
    measure as two.
    """
    merged: list[Curve] = []

    for curve in curves:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.is_arc
            and curve.is_arc
            and previous.ccw == curve.ccw
            and _same_circle(previous, curve)
            and math.dist(previous.end, curve.start) < 1e-6
        ):
            # Through the two far ends and the join between them, so the arc
            # that results passes exactly through all three rather than through
            # the first piece's circle and near the second's.
            through = _circle_through(previous.start, previous.end, curve.end)
            # Not when the two would close the circle: a full turn has no start
            # and no end, and an edge built on one is not the arc that was
            # drawn.
            if through is not None and math.dist(previous.start, curve.end) > 1e-9:
                merged[-1] = Curve(previous.start, curve.end, through[0], previous.ccw)
                continue

        merged.append(curve)

    return merged


def _is_dash_dot(style) -> bool:
    """Whether a stroke's dash pattern is the one a centre line is drawn with.

    A pattern is on and off lengths in turn. Dashed is one length repeating --
    long, gap, long, gap. Dash-dot alternates two, and that difference is the
    whole of what tells an axis from a hidden edge. Read as "are the marks all
    the same length", which needs no idea of how big the drawing is.
    """
    if not style:
        return False
    pattern = style[0] if isinstance(style, tuple) else style
    if not pattern or len(pattern) < 4:
        return False
    marks = [round(value, 3) for value in pattern[::2] if value > 0]
    return len(set(marks)) > 1


def _line_key(curve: Curve, tol: float) -> tuple[int, int] | None:
    """Which infinite line a straight run lies on, to the given tolerance."""
    if curve.is_arc:
        return None
    dx = curve.end[0] - curve.start[0]
    dy = curve.end[1] - curve.start[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return None
    ux, uy = dx / length, dy / length
    # One of the two directions, chosen the same way every time, so a run and
    # the same run drawn backwards land in one group.
    if (ux, uy) < (-ux, -uy):
        ux, uy = -ux, -uy
    offset = ux * curve.start[1] - uy * curve.start[0]
    return (round(math.atan2(uy, ux) / 1e-3), round(offset / tol))


def _rejoin_dashes(curves: list[Curve], tol: float) -> tuple[list[Curve], list[Curve]]:
    """Put dashed lines back together, and say which were centre lines.

    A PDF has no linetypes. What a drawing calls CENTER arrives as a row of
    short strokes on one line, alternating long and short with even gaps, and
    that pattern is as good a signature as the name was -- better, in that it
    is what was actually drawn.

    Returns (everything else, the centre lines).
    """
    groups: dict[tuple[int, int], list[Curve]] = {}
    others: list[Curve] = []

    for curve in curves:
        key = _line_key(curve, tol)
        if key is None:
            others.append(curve)
        else:
            groups.setdefault(key, []).append(curve)

    axes: list[Curve] = []

    for group in groups.values():
        if len(group) < MIN_DASHES:
            others.extend(group)
            continue

        direction = (
            group[0].end[0] - group[0].start[0],
            group[0].end[1] - group[0].start[1],
        )
        length = math.hypot(*direction)
        ux, uy = direction[0] / length, direction[1] / length

        def along(p: Point, ux: float = ux, uy: float = uy) -> float:
            return ux * p[0] + uy * p[1]

        ordered = sorted(group, key=lambda c: min(along(c.start), along(c.end)))
        spans = [sorted((along(c.start), along(c.end))) for c in ordered]
        lengths = [b - a for a, b in spans]
        gaps = [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]

        # Gaps shorter than the marks: one line drawn broken. Every drawing
        # convention makes the gap the smaller half -- a hidden line is four
        # and one, a centre line twenty-four and three -- so a gap wider than
        # the marks is not a dash pattern but two edges that line up, and those
        # must stay two.
        if not lengths or max(gaps) > statistics.median(lengths):
            others.extend(group)
            continue

        first = (
            ordered[0].start
            if along(ordered[0].start) < along(ordered[0].end)
            else ordered[0].end
        )
        last = (
            ordered[-1].end
            if along(ordered[-1].end) > along(ordered[-1].start)
            else ordered[-1].start
        )
        rejoined = Curve(first, last)

        spread = statistics.pstdev(lengths) / statistics.mean(lengths)
        (axes if spread > DASH_DOT_SPREAD else others).append(rejoined)

    return others, axes


def read_curves(source: Path) -> tuple[list[Curve], list[Curve], str, list[str]]:
    """A printed sheet as outline curves and centre lines, in PDF points."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTCurve

    try:
        pages = list(extract_pages(str(source), laparams=None, maxpages=1))
    except Exception as error:
        raise DrawingError(f"this file could not be read as PDF: {error}") from error

    if not pages:
        raise DrawingError("the PDF has no pages")

    drawn: list[Curve] = []
    stroked_axes: list[Curve] = []
    filled = 0

    for item in pages[0]:
        if not isinstance(item, LTCurve):
            continue
        # Every letter, every arrowhead and every solid triangle is filled and
        # not stroked. Every line of the drawing is stroked. One test separates
        # the annotation from the part, and it is the reason a PDF is worth
        # reading directly rather than as a picture of itself.
        if item.fill and not item.stroke:
            filled += 1
            continue
        if not item.stroke:
            continue

        curves = _curves_of(item)
        # A dashed stroke reaches a PDF one of two ways, and which one is the
        # writer's habit rather than the drawing's meaning: as a pattern on a
        # whole line, or as a row of short lines already broken up. Both say
        # the same thing, so both are read.
        if _is_dash_dot(getattr(item, "dashing_style", None)):
            stroked_axes.extend(c for c in curves if not c.is_arc)
            continue
        drawn.extend(curves)

    if not drawn:
        raise DrawingError(
            "nothing on the first page was drawn as a line. If the sheet is a "
            "scanned image inside a PDF there is no geometry in it to read."
        )

    tol = tolerance_for(drawn + stroked_axes)
    outline, rejoined_axes = _rejoin_dashes(drawn, tol)
    axes = stroked_axes + rejoined_axes

    ignored = []
    if filled:
        ignored.append(
            f"{filled} filled shape" if filled == 1 else f"{filled} filled shapes"
        )

    return outline, axes, "PDF points", ignored


def _extent_along_axis(profile: Profile) -> float:
    """How long the part is, measured the way its length would be dimensioned."""
    ux, uy = profile.axis.direction
    along = [ux * p[0] + uy * p[1] for c in profile.curves for p in (c.start, c.end)]
    return max(along) - min(along)


def read_profile(source: Path, length_mm: float | None = None) -> Profile:
    """A printed sheet, read down to the outline to revolve, in millimetres.

    `length_mm` is the part's length along its own axis, if it is known. Given
    one, the sheet's own scale stops mattering: everything follows from it.
    Without one the sheet is taken to have been printed full size, which is
    checkable by whoever knows the part and is said in the assumptions either
    way.
    """
    profile = profile_from(*read_curves(source))

    extent = _extent_along_axis(profile)
    if length_mm is not None:
        if length_mm <= 0:
            raise DrawingError("the length of the part has to be more than zero")
        if extent <= 0:
            raise DrawingError("the outline has no length along its axis")
        factor = length_mm / extent
        note = f"scaled so the part is {length_mm:g} mm along its axis"
    else:
        factor = PT_TO_MM
        note = (
            f"size assumes the sheet was printed full size, making the part "
            f"{extent * PT_TO_MM:.1f} mm long. If it was plotted to a scale, "
            f"every dimension here is out by that scale"
        )

    scaled_axis = type(profile.axis)(
        point=(profile.axis.point[0] * factor, profile.axis.point[1] * factor),
        direction=profile.axis.direction,
        found_by=profile.axis.found_by,
    )

    return Profile(
        axis=scaled_axis,
        curves=[c.scaled(factor) for c in profile.curves],
        units="mm",
        # First, because size is what a printed sheet is vaguest about and the
        # panel is read from the top.
        assumptions=[note, *profile.assumptions],
        ignored=profile.ignored,
    )


def convert(
    source: Path,
    out_glb: Path,
    deflection: float | None = None,
    length_mm: float | None = None,
):
    """A printed drawing of a turned part, as far as a .glb the viewer opens."""
    from app.cad import drawing, occt
    from app.models import DerivedGeometry

    profile = read_profile(source, length_mm)
    solid = drawing.revolve(profile)

    part = occt.Part(
        id="n1",
        name=source.stem,
        shape=solid,
        color=None,
        named=False,
    )

    return occt.build(
        [part],
        out_glb,
        deflection,
        geometry_source="derived",
        derived=DerivedGeometry(
            method="pdf-revolve",
            axis_point=(profile.axis.point[0], profile.axis.point[1], 0.0),
            axis_direction=(*profile.axis.direction, 0.0),
            assumptions=profile.assumptions,
            ignored=profile.ignored,
        ),
    )
