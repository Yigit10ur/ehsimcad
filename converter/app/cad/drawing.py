"""Reading a turned part out of a 2D drawing.

A drawing is not a model. What it holds is a projection, and turning one back
into a solid means deciding what the projection meant -- which is a guess, and
is labelled as one everywhere downstream (`geometry_source="derived"`).

For a turned part the guess is narrow enough to be worth making. Such a part is
a profile revolved about an axis; the drawing shows that profile and draws the
axis on it as a centre line. So the work is to find the axis, take the outline
on one side of it, and hand OCCT a closed wire to revolve. The result is a real
B-rep -- exact faces, exact edges, a volume that can be measured -- and the only
guess in it is that the part is a solid of revolution at all.

A sheet is divided into views before any of that. It holds more than one
drawing and more than one centre line, and an axis taken from the wrong one is
the worst thing this can do: not an obviously broken model, but a convincing
one with every radius wrong.

DXF rather than an image on purpose. In DXF the dimensions, the notes, the
hatching and the title block are separate entity types, so telling the part
from the annotation is a filter rather than a computer vision problem. Nothing
here reads pixels.

Everything below is 2D and pure Python, so it can be tested without OpenCascade
installed. The revolve, which cannot, is at the bottom behind a lazy import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.models import DerivedGeometry

# Entity types that describe the sheet rather than the part. Dropping them by
# type is the whole reason this reads DXF: on an image every one of these is a
# black line like any other.
ANNOTATION = frozenset(
    {
        "DIMENSION",
        "TEXT",
        "MTEXT",
        "ATTDEF",
        "ATTRIB",
        "HATCH",
        "LEADER",
        "MULTILEADER",
        "MLEADER",
        "TOLERANCE",
        "IMAGE",
        "WIPEOUT",
        "POINT",
        "SOLID",
        "INSERT",
    }
)

# AutoCAD keeps the anchor points of dimensions on this layer. It is not
# plotted and it is not geometry, but it is made of ordinary entities.
NON_PLOTTING_LAYERS = frozenset({"DEFPOINTS"})

# How a drawing says "this line is the axis". Linetype first, because it is the
# convention every drawing follows; layer name second, because plenty of offices
# also give it a layer of its own and some drawings set the linetype there.
AXIS_LINETYPES = ("CENTER", "CENTRE", "DASHDOT", "DASH_DOT")
AXIS_LAYER_WORDS = ("CENTER", "CENTRE", "AXIS", "EKSEN", "MITTE")

# $INSUNITS, in millimetres per unit. 0 means the file does not say.
UNIT_SCALE = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 11: 2.54e-5}

# How far apart two pieces of geometry can be and still belong to the same
# view, as a fraction of the sheet's own diagonal.
#
# A fraction rather than a figure in millimetres because the gap between two
# views is a property of the sheet, not of the part: it is whitespace a
# draughtsman left to keep one drawing from reading as another, and it is left
# at much the same proportion whether the part is a 5 mm pin or a 5 m shaft.
# Wide enough to swallow the gaps inside one view, which are the width of a
# line; narrow enough to fall well short of the space between two.
VIEW_GAP = 0.02

Point = tuple[float, float]


class DrawingError(ValueError):
    """The drawing could not be read as a turned part.

    Always raised with a sentence saying what was looked for and not found. A
    refusal a draughtsman can act on is worth far more than a solid that is
    plausible and wrong: the wrong solid gets measured.
    """


@dataclass(frozen=True)
class Curve:
    """One piece of the outline: a straight run, or an arc when `centre` is set.

    Arcs are kept as arcs rather than flattened to chords because the whole
    point of building a B-rep is that a fillet is a fillet.
    """

    start: Point
    end: Point
    centre: Point | None = None
    ccw: bool = True

    @property
    def is_arc(self) -> bool:
        return self.centre is not None

    @property
    def length(self) -> float:
        if not self.is_arc:
            return math.dist(self.start, self.end)
        radius = math.dist(self.centre, self.start)  # type: ignore[arg-type]
        return radius * abs(self._sweep())

    def _sweep(self) -> float:
        cx, cy = self.centre  # type: ignore[misc]
        a0 = math.atan2(self.start[1] - cy, self.start[0] - cx)
        a1 = math.atan2(self.end[1] - cy, self.end[0] - cx)
        sweep = a1 - a0
        if self.ccw and sweep <= 0:
            sweep += 2 * math.pi
        if not self.ccw and sweep >= 0:
            sweep -= 2 * math.pi
        return sweep

    def scaled(self, factor: float) -> Curve:
        def s(p: Point) -> Point:
            return (p[0] * factor, p[1] * factor)

        return Curve(
            s(self.start),
            s(self.end),
            s(self.centre) if self.centre else None,
            self.ccw,
        )

    def reversed(self) -> Curve:
        return Curve(self.end, self.start, self.centre, not self.ccw)


@dataclass(frozen=True)
class Axis:
    """The line the profile is revolved about, and how it was arrived at."""

    point: Point
    direction: Point
    # A centre line is what the drawing says. Symmetry is what the geometry
    # implies when nobody drew one, and is a weaker claim -- so the two are not
    # allowed to look the same from the outside.
    found_by: Literal["centre line", "symmetry"]

    def signed_distance(self, p: Point) -> float:
        """Positive to the left of the direction, so positive is above a
        left to right axis. Which side is which has to be stable, because it
        is what the note on the model ends up saying."""
        dx, dy = self.direction
        return dx * (p[1] - self.point[1]) - dy * (p[0] - self.point[0])


@dataclass
class View:
    """One group of geometry on the sheet, and the centre lines drawn for it.

    A sheet is not a drawing. It carries several -- a longitudinal view, an end
    view, a section, a detail -- and a title block, which is none of them.
    Until they are told apart, every rule that has to pick the part out is
    picking it out of a heap, and the rules here said so: the one that chooses
    the profile is written the way it is to keep from handing back the title
    block.

    Told apart by whitespace, because whitespace is what separates them on
    paper. One view's outline joins end to end, so its curves cluster at any
    distance worth the name; two views stand apart by a gap somebody left on
    purpose. Nothing here reads what a view *is* -- only that it is one of
    several, which is enough to stop the sheet being read as one drawing.
    """

    curves: list[Curve]
    axis_candidates: list[Curve]

    @property
    def box(self) -> tuple[float, float, float, float]:
        return _bbox(self.curves)

    @property
    def has_axis(self) -> bool:
        """Whether a straight centre line was drawn for this view.

        Straight, because what happens next revolves about a line. A view with
        only an arc centre line on it -- a bolt circle, say -- is not one this
        can read, and saying so here keeps it out of the running rather than
        failing on it later.
        """
        return any(not curve.is_arc for curve in self.axis_candidates)


@dataclass
class Prism:
    """A closed outline and how far it runs, ready to be extruded.

    The other thing a drawing can be read as, and the one that needs two
    views. A turned part carries its own axis on the sheet, so one view is
    enough; a part of constant section carries nothing of the sort, and the
    depth it runs is simply not in the view that shows its shape. It is in the
    one drawn above or beside it.

    Which is the whole claim: that the outline runs straight through, at the
    depth the second view gives. A part that needed a third view to describe
    it is a part this claim is wrong about, and is refused rather than read.
    """

    curves: list[Curve]
    depth: float
    units: str
    # Closed outlines inside the first one, each cut out of the part. A bolt
    # hole, a slot, a lightening pocket -- all the same thing to the build,
    # and all of them material the part does not have.
    holes: list[list[Curve]]
    assumptions: list[str]
    ignored: list[str]


@dataclass
class Profile:
    """A closed outline on one side of an axis, ready to be revolved."""

    axis: Axis
    curves: list[Curve]
    units: str
    # Everything the reader decided rather than read. This travels into the
    # metadata: someone looking at the model has to be able to see what was
    # assumed to make it.
    assumptions: list[str]
    # What was on the sheet and did not become part of the solid. A different
    # question from what was assumed, and the first place to look when the
    # result is the wrong shape.
    ignored: list[str]


def available() -> bool:
    """Whether the DXF reader can be imported in this environment."""
    try:
        import ezdxf  # noqa: F401
    except ImportError:
        return False
    return True


def _linetype_of(entity, doc) -> str:
    """The entity's linetype, with BYLAYER followed to the layer.

    Nearly every entity a CAD application writes says BYLAYER, so a reader that
    takes `entity.dxf.linetype` at face value finds no centre lines at all --
    and then falls back to guessing an axis on a drawing that stated one.
    """
    name = (entity.dxf.linetype or "BYLAYER").upper()
    if name in {"BYLAYER", "BYBLOCK"}:
        try:
            return (doc.layers.get(entity.dxf.layer).dxf.linetype or "").upper()
        except Exception:
            return ""
    return name


def _is_axis_line(entity, doc) -> bool:
    linetype = _linetype_of(entity, doc)
    if any(linetype.startswith(word) for word in AXIS_LINETYPES):
        return True
    layer = (entity.dxf.layer or "").upper()
    return any(word in layer for word in AXIS_LAYER_WORDS)


def _curves_of(entity) -> list[Curve]:
    """One DXF entity as straight runs and arcs, or nothing if it is neither."""
    kind = entity.dxftype()

    if kind == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return [Curve((s.x, s.y), (e.x, e.y))]

    if kind == "ARC":
        c, r = entity.dxf.center, entity.dxf.radius
        a0 = math.radians(entity.dxf.start_angle)
        a1 = math.radians(entity.dxf.end_angle)
        return [
            Curve(
                (c.x + r * math.cos(a0), c.y + r * math.sin(a0)),
                (c.x + r * math.cos(a1), c.y + r * math.sin(a1)),
                (c.x, c.y),
                ccw=True,
            )
        ]

    if kind in {"LWPOLYLINE", "POLYLINE"}:
        from ezdxf.math import bulge_to_arc

        try:
            points = [(p[0], p[1], p[4]) for p in entity.get_points("xyseb")]
        except (AttributeError, TypeError):
            points = [
                (v.dxf.location.x, v.dxf.location.y, 0.0) for v in entity.vertices
            ]

        closed = bool(
            getattr(entity, "closed", False) or entity.dxf.get("flags", 0) & 1
        )
        pairs = list(zip(points, points[1:], strict=False))
        if closed and len(points) > 2:
            pairs.append((points[-1], points[0]))

        out: list[Curve] = []
        for (x1, y1, bulge), (x2, y2, _) in pairs:
            if abs(bulge) < 1e-12:
                out.append(Curve((x1, y1), (x2, y2)))
                continue
            # Anticlockwise, always. bulge_to_arc hands back the two ends in
            # that order whichever way the polyline runs through the corner, so
            # a reader that carries the bulge's sign through as the direction
            # draws the negative ones the long way round -- 270 degrees where
            # the drawing says 90. Which way the outline is walked does not
            # matter afterwards; the chain is assembled from whichever ends
            # meet.
            centre, a0, a1, radius = bulge_to_arc((x1, y1), (x2, y2), bulge)
            out.append(
                Curve(
                    (
                        centre.x + radius * math.cos(a0),
                        centre.y + radius * math.sin(a0),
                    ),
                    (
                        centre.x + radius * math.cos(a1),
                        centre.y + radius * math.sin(a1),
                    ),
                    (centre.x, centre.y),
                    ccw=True,
                )
            )
        return out

    if kind == "CIRCLE":
        c, r = entity.dxf.center, entity.dxf.radius
        # Four quarters, and where they are split is the point. A centre line
        # drawn through the middle horizontally or vertically meets these arcs
        # at their ends and never crosses one partway -- and an arc crossed
        # partway is an arc this cannot cut.
        #
        # What a circle must never become is the outline to revolve: turning
        # one about a line through it builds a sphere nobody drew. Kept out of
        # that by `_circles`, where the rule can see what it is looking at,
        # rather than by refusing to read the entity at all.
        corners = [
            (c.x + r, c.y),
            (c.x, c.y + r),
            (c.x - r, c.y),
            (c.x, c.y - r),
        ]
        return [
            Curve(corners[i], corners[(i + 1) % 4], (c.x, c.y), ccw=True)
            for i in range(4)
        ]

    return []


def read_curves(source: Path) -> tuple[list[Curve], list[Curve], str, list[str]]:
    """Read a DXF into outline curves and axis candidates, both in millimetres.

    Returns (outline, axis candidates, unit name, notes about what was skipped).
    """
    import ezdxf

    try:
        doc = ezdxf.readfile(str(source))
    except Exception as error:  # ezdxf raises several unrelated types
        raise DrawingError(f"this file could not be read as DXF: {error}") from error

    code = doc.header.get("$INSUNITS", 0)
    scale = UNIT_SCALE.get(code, 1.0)
    units = {0: "assumed millimetres", 1: "inches", 2: "feet", 4: "millimetres"}.get(
        code, "millimetres"
    )

    outline: list[Curve] = []
    axis_candidates: list[Curve] = []
    skipped: dict[str, int] = {}

    for entity in doc.modelspace():
        kind = entity.dxftype()
        layer_name = (entity.dxf.layer or "").upper()

        if kind in ANNOTATION or layer_name in NON_PLOTTING_LAYERS:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue

        # An invisible layer is invisible for a reason. Building material out
        # of a construction line somebody switched off would be a surprise.
        try:
            layer = doc.layers.get(entity.dxf.layer)
            if layer.is_off() or layer.is_frozen():
                skipped["hidden layer"] = skipped.get("hidden layer", 0) + 1
                continue
        except Exception:
            pass

        curves = [c.scaled(scale) for c in _curves_of(entity)]
        if not curves:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue

        (axis_candidates if _is_axis_line(entity, doc) else outline).extend(curves)

    ignored = [
        f"{count} {kind}" if count == 1 else f"{count} {kind} entities"
        for kind, count in sorted(skipped.items())
    ]
    return outline, axis_candidates, units, ignored


def _normalise(v: Point) -> Point:
    length = math.hypot(*v)
    if length == 0:
        raise DrawingError("a zero length line cannot be an axis")
    return (v[0] / length, v[1] / length)


def _bbox(curves: list[Curve]) -> tuple[float, float, float, float]:
    xs = [p[0] for c in curves for p in (c.start, c.end)]
    ys = [p[1] for c in curves for p in (c.start, c.end)]
    return min(xs), min(ys), max(xs), max(ys)


def tolerance_for(curves: list[Curve]) -> float:
    """How far apart two points may be and still be the same point.

    Relative to the drawing, because an absolute figure is either meaningless
    on a 5 mm part or useless on a 5 m one. Small enough that it cannot join
    two features, large enough to close the gaps a CAD application leaves.
    """
    x0, y0, x1, y1 = _bbox(curves)
    return max(math.hypot(x1 - x0, y1 - y0) * 1e-5, 1e-9)


def _samples(curve: Curve, count: int = 7) -> list[Point]:
    """Points along a curve, enough of them to place an arc against a line."""
    if not curve.is_arc:
        return [curve.start, curve.end]
    cx, cy = curve.centre  # type: ignore[misc]
    radius = math.dist(curve.centre, curve.start)  # type: ignore[arg-type]
    a0 = math.atan2(curve.start[1] - cy, curve.start[0] - cx)
    sweep = curve._sweep()
    return [
        (
            cx + radius * math.cos(a0 + sweep * i / count),
            cy + radius * math.sin(a0 + sweep * i / count),
        )
        for i in range(count + 1)
    ]


def _curve_box(curve: Curve) -> tuple[float, float, float, float]:
    """One curve's bounding box, an arc's bulge included."""
    points = _samples(curve)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _diagonal(box: tuple[float, float, float, float]) -> float:
    """How big a box is, as one number."""
    return math.hypot(box[2] - box[0], box[3] - box[1])


def _box_gap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """How far apart two boxes are, and zero if they touch or overlap."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _cells(box: tuple[float, float, float, float], size: float):
    """The grid squares a box covers.

    Only so that each curve is compared against its neighbours rather than
    against the whole sheet. The squares are the width of the gap being looked
    for, so anything near enough to matter is at most one square away.
    """
    x0, y0, x1, y1 = box
    for cx in range(math.floor(x0 / size), math.floor(x1 / size) + 1):
        for cy in range(math.floor(y0 / size), math.floor(y1 / size) + 1):
            yield (cx, cy)


def _view_gap(outline: list[Curve]) -> float:
    """The whitespace that separates one view from the next on this sheet."""
    return _diagonal(_bbox(outline)) * VIEW_GAP


def _has_width(curves: list[Curve], tol: float) -> bool:
    """Whether a group of geometry spreads out in both directions."""
    x0, y0, x1, y1 = _bbox(curves)
    return min(x1 - x0, y1 - y0) > tol


def _reflected_box(
    box: tuple[float, float, float, float], line: Curve
) -> tuple[float, float, float, float]:
    """A box mirrored across a line.

    The corners are reflected and boxed again, which is the same box for a
    horizontal or vertical centre line and a slightly generous one for a
    centre line drawn at an angle. Generous the safe way: it can only make two
    halves look less alike than they are.
    """
    ax, ay = line.start
    dx, dy = _normalise((line.end[0] - line.start[0], line.end[1] - line.start[1]))

    points = []
    x0, y0, x1, y1 = box
    for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        along = (px - ax) * dx + (py - ay) * dy
        foot = (ax + along * dx, ay + along * dy)
        points.append((2 * foot[0] - px, 2 * foot[1] - py))

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _draw_in(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
    gap: float,
) -> bool:
    """Whether one box sits inside another."""
    return (
        inner[0] >= outer[0] - gap
        and inner[1] >= outer[1] - gap
        and inner[2] <= outer[2] + gap
        and inner[3] <= outer[3] + gap
    )


def _absorb_enclosed(views: list[View], gap: float) -> list[View]:
    """Take into each view whatever is drawn inside its outline.

    Whitespace groups what touches, and a hole touches nothing. A bore drawn
    as a circle, a pocket, a hidden edge, a boss in the middle of a face --
    each is a run of geometry standing clear of the outline around it, and
    each would come out of the grouping as a view of its own.

    It is not one. Nothing is drawn inside a view but the part that view
    shows, so what a view encloses belongs to it -- and for a part read as a
    constant section, that is the difference between a plate and a plate with
    a hole in it.
    """
    boxes = [view.box for view in views]
    parent = list(range(len(views)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(views)):
        for j in range(len(views)):
            if i == j or not _draw_in(boxes[i], boxes[j], gap):
                continue
            # Two boxes each inside the other are the same box, and joining
            # them either way round is the same answer.
            if _draw_in(boxes[j], boxes[i], gap) and j > i:
                continue
            ri, rj = root(i), root(j)
            if ri != rj:
                parent[ri] = rj

    joined: dict[int, View] = {}
    for i, view in enumerate(views):
        into = joined.get(root(i))
        if into is None:
            joined[root(i)] = View(list(view.curves), [])
            continue
        into.curves.extend(view.curves)

    return list(joined.values())


def _join_mirrored(
    views: list[View], candidates: list[Curve], gap: float
) -> list[View]:
    """Put back together the halves of a view that whitespace pulled apart.

    A turned part is drawn on both sides of its centre line, and the two halves
    stand apart by the bore. A bore is a feature of the part, not a gap in the
    layout: on a thin-walled tube it is most of the drawing, far wider than the
    whitespace between two views, so whitespace alone cuts one view in two.

    What puts it back is that the halves are each other's reflection in the
    line between them. Two views stacked on the sheet are not -- they show
    different things -- so this joins the one case it is meant to and leaves
    the sheet's own divisions alone.

    Before the centre lines are handed out, on purpose. A half standing off
    its own axis by the bore is exactly the case the handing out cannot judge,
    so the halves are made whole first and the question is asked once, of a
    view that straddles its axis the way a drawing of a turned part does.
    """
    parent = list(range(len(views)))
    boxes = [view.box for view in views]

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(views)):
        for j in range(i + 1, len(views)):
            if not any(
                all(
                    abs(p - q) <= gap
                    for p, q in zip(
                        _reflected_box(boxes[i], line), boxes[j], strict=True
                    )
                )
                for line in candidates
                if not line.is_arc
            ):
                continue
            ra, rb = root(i), root(j)
            if ra != rb:
                parent[rb] = ra

    joined: dict[int, View] = {}
    for i, view in enumerate(views):
        into = joined.get(root(i))
        if into is None:
            joined[root(i)] = View(list(view.curves), [])
            continue
        into.curves.extend(view.curves)

    return list(joined.values())


def _runs_along(line: Curve, view: View, gap: float) -> bool:
    """Whether a centre line is drawn the length of a group of geometry.

    Not whether it is near it. A bored part stands clear of its own axis by
    the bore radius, and a bore can be most of the part -- so a rule about
    distance takes the axis away from the one view that needs it most.

    Length instead. A centre line is drawn along the part and a little past it
    at each end, which is what ISO 128 asks for, so a line reaching across a
    group was drawn for that group and a line stopping inside it was not. The
    title block is the case this has to get right, and it gets it right for
    the reason that matters: the centre line does not reach across it.

    Reaching across, with only the overhang to spare -- not merely lying
    inside. A plate is covered in centre lines that stop inside it, one pair
    crossing at every bolt hole, and a rule that let a line in for being near
    would hand a plate the axis of one of its holes and revolve it about that.
    """
    dx, dy = _normalise((line.end[0] - line.start[0], line.end[1] - line.start[1]))

    def along(p: Point) -> float:
        return p[0] * dx + p[1] * dy

    x0, y0, x1, y1 = view.box
    reach = sorted(along(p) for p in (line.start, line.end))
    span = sorted(along(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    return reach[0] <= span[0] + gap and reach[-1] >= span[-1] - gap


def _passes_through(line: Curve, view: View, gap: float) -> bool:
    """Whether a view lies across the line, rather than off to one side of it.

    The second half of belonging, and the half that keeps one view's axis from
    reaching another. A sheet is laid out in aligned rows and columns, so a
    centre line drawn for the front view runs clear across whatever is drawn
    above and below it and would otherwise claim them all -- an end view's
    vertical centre line, handed to the front view beside it, turns a shaft on
    its side.

    Safe to ask because a turned part is drawn on both sides of its axis, and
    the halves have already been put back together: what reaches this is a
    view its axis runs through, bore and all.
    """
    axis = Axis(
        line.start,
        _normalise((line.end[0] - line.start[0], line.end[1] - line.start[1])),
        "centre line",
    )
    x0, y0, x1, y1 = view.box
    sides = [axis.signed_distance(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return min(sides) <= gap and max(sides) >= -gap


def _lay_axes_over(views: list[View], candidates: list[Curve], gap: float) -> None:
    """Give each view the centre lines that were drawn for it.

    Two questions, and a line has to answer both: does it run the length of
    the view, and does the view lie across it. Length alone lets a line claim
    anything it is no shorter than, several rows down the sheet; position
    alone lets a bore push a part away from its own axis. Together they say
    what a centre line is -- a line drawn through a part, end to end.
    """
    for line in candidates:
        # An arc is no use as an axis and is not offered as one. Leaving it
        # out here is what keeps `has_axis` from promising a view a reading
        # that `find_axis` would then refuse.
        if line.is_arc or math.dist(line.start, line.end) <= gap:
            continue

        for view in views:
            if _runs_along(line, view, gap) and _passes_through(line, view, gap):
                view.axis_candidates.append(line)


def split_views(outline: list[Curve], axis_candidates: list[Curve]) -> list[View]:
    """The sheet's geometry in groups, each set apart from the rest by whitespace.

    Only the outline is grouped. The centre lines are kept out of the grouping
    on purpose: a centre line is drawn running past the part at both ends,
    often far enough to reach whatever is drawn next to it, and one line drawn
    long would otherwise sew two views into one. So the groups are formed from
    part geometry alone, and the centre lines are laid over them afterwards --
    each on whichever groups it was drawn for, which may be more than one.

    Pure 2D, like everything above it: the grouping is where a reading of a
    sheet can go wrong quietly, so it is testable without OpenCascade.
    """
    if not outline:
        return []

    gap = _view_gap(outline)
    if gap <= 0:
        # Everything at one point. Not a sheet with views on it.
        return [View(list(outline), list(axis_candidates))]

    boxes = [_curve_box(curve) for curve in outline]
    parent = list(range(len(outline)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    occupants: dict[tuple[int, int], list[int]] = {}
    for i, box in enumerate(boxes):
        for cell in _cells(box, gap):
            occupants.setdefault(cell, []).append(i)

    for i, box in enumerate(boxes):
        reach = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
        for cell in _cells(reach, gap):
            for j in occupants.get(cell, ()):
                if j <= i or _box_gap(box, boxes[j]) > gap:
                    continue
                a, b = root(i), root(j)
                if a != b:
                    parent[b] = a

    grouped: dict[int, list[Curve]] = {}
    for i, curve in enumerate(outline):
        grouped.setdefault(root(i), []).append(curve)

    # A group with no width in one direction is a stray line, not a view: a
    # construction line left in, or the axis drawn a second time as ordinary
    # geometry. It encloses nothing, so no reading is lost by leaving it out
    # -- and leaving it in would let it stand between a centre line and the
    # view that line was drawn for, being nearer than the part itself.
    tol = tolerance_for(outline)
    views = [View(curves, []) for curves in grouped.values() if _has_width(curves, tol)]

    # Enclosed first, while every box is still one group's own. A mirrored
    # pair joined into one covers the whitespace between its halves, and
    # anything drawn in that gap would look enclosed by a part it has nothing
    # to do with.
    views = _absorb_enclosed(views, gap)
    views = _join_mirrored(views, axis_candidates, gap)
    _lay_axes_over(views, axis_candidates, gap)

    # Biggest first, so that a tie anywhere downstream falls to the group that
    # carries more of the drawing rather than to whichever happened to be read
    # first.
    views.sort(key=lambda view: -_diagonal(view.box))
    return views


def _in_projection(a: View, b: View, gap: float) -> bool:
    """Whether two views are one part seen from two directions.

    Orthographic projection lines them up, and that is the whole of the test.
    A view drawn above or below another shares its width; a view drawn beside
    it shares its height. Shares, not merely overlaps: both show the same
    dimension of the same part at the same scale, so what they have in common
    is the whole of both.

    Which is also what a detail does not do. A detail is the same part drawn
    at 2:1 or 5:1, so it lines up with nothing -- and a second view is
    therefore worth more than a bigger outline, which is the one thing a
    reading of a sheet cannot tell from size alone.
    """
    ax0, ay0, ax1, ay1 = a.box
    bx0, by0, bx1, by1 = b.box

    over_x = min(ax1, bx1) > max(ax0, bx0)
    over_y = min(ay1, by1) > max(ay0, by0)

    if over_x and not over_y:
        return abs(ax0 - bx0) <= gap and abs(ax1 - bx1) <= gap
    if over_y and not over_x:
        return abs(ay0 - by0) <= gap and abs(ay1 - by1) <= gap
    # Corner to corner, or one inside the other. Neither is a projection.
    return False


def in_projection(views: list[View], gap: float) -> list[list[View]]:
    """The views grouped by which of them show the same part.

    A sheet can hold more than one part, and a part more than one drawing that
    is not a projection of it -- a detail, a magnified corner. Lining up is
    what says two drawings are of one thing, and it is the drawing standard
    saying it rather than this guessing.
    """
    parent = list(range(len(views)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(views)):
        for j in range(i + 1, len(views)):
            if not _in_projection(views[i], views[j], gap):
                continue
            ri, rj = root(i), root(j)
            if ri != rj:
                parent[rj] = ri

    grouped: dict[int, list[View]] = {}
    for i, view in enumerate(views):
        grouped.setdefault(root(i), []).append(view)
    return list(grouped.values())


def find_axis(candidates: list[Curve]) -> Axis:
    """The line to revolve about, taken from the drawing's centre line.

    Only from the centre line. There is no fallback that guesses one, and the
    absence is deliberate: the axis fixes every diameter in the part, so an axis
    inferred wrongly does not produce an obviously broken model -- it produces a
    convincing one with every radius wrong, which somebody then measures.

    Nothing is lost by refusing. A centre line on a turned part is not a
    courtesy, it is what ISO 128 asks for, and a drawing without one is
    incomplete rather than merely terse. When one is genuinely missing the
    answer is for a person to say where the axis is, not for this to decide.
    """
    straight = [c for c in candidates if not c.is_arc]
    if not straight:
        raise DrawingError(
            "no centre line found, so there is nothing to say where the axis "
            "of the part is. Draw the axis with a CENTER linetype, or put it "
            "on a layer named CENTER, CENTRE, AXIS or EKSEN."
        )

    longest = max(straight, key=lambda c: c.length)
    return Axis(
        longest.start,
        _normalise(
            (longest.end[0] - longest.start[0], longest.end[1] - longest.start[1])
        ),
        "centre line",
    )


def _clip(curve: Curve, axis: Axis, keep: int, tol: float) -> Curve | None:
    """The part of a curve on the `keep` side of the axis, or None.

    A drawing of a solid turned part draws its end faces straight across the
    centre line, so those lines have to be cut at the axis rather than thrown
    away -- half of each is profile.
    """
    d0 = axis.signed_distance(curve.start) * keep
    d1 = axis.signed_distance(curve.end) * keep

    if d0 >= -tol and d1 >= -tol:
        return curve
    if d0 <= tol and d1 <= tol:
        return None

    if curve.is_arc:
        # An arc across the axis is a spherical or toroidal end. It can be cut,
        # but not by this, and a wrong cut here would be invisible in the
        # result -- so say so instead.
        raise DrawingError(
            "an arc crosses the centre line, which this cannot cut. Split it at "
            "the centre line in the drawing, or draw the profile on one side "
            "only."
        )

    t = d0 / (d0 - d1)
    crossing = (
        curve.start[0] + t * (curve.end[0] - curve.start[0]),
        curve.start[1] + t * (curve.end[1] - curve.start[1]),
    )
    return Curve(curve.start, crossing) if d0 > 0 else Curve(crossing, curve.end)


def _chains(curves: list[Curve], tol: float) -> list[list[Curve]]:
    """Order the curves into runs that join end to end."""

    def key(p: Point) -> tuple[int, int]:
        return (round(p[0] / tol), round(p[1] / tol))

    ends: dict[tuple[int, int], list[int]] = {}
    for i, c in enumerate(curves):
        for p in (c.start, c.end):
            ends.setdefault(key(p), []).append(i)

    crowded = [k for k, v in ends.items() if len(v) > 2]
    if crowded:
        raise DrawingError(
            f"{len(crowded)} point(s) where more than two lines meet. The "
            "outline has to be a single loop, so a stray line touching it -- a "
            "construction line, or a view drawn over another -- has to go."
        )

    remaining = set(range(len(curves)))
    chains: list[list[Curve]] = []

    while remaining:
        chain = [curves[remaining.pop()]]
        grew = True
        while grew:
            grew = False
            for i in list(remaining):
                candidate = curves[i]
                for c in (candidate, candidate.reversed()):
                    if key(c.start) == key(chain[-1].end):
                        chain.append(c)
                    elif key(c.end) == key(chain[0].start):
                        chain.insert(0, c)
                    else:
                        continue
                    remaining.discard(i)
                    grew = True
                    break
        chains.append(chain)

    return chains


def _polygon(curves: list[Curve]) -> list[Point]:
    return [p for c in curves for p in _samples(c)]


def section_area(curves: list[Curve]) -> float:
    """The area the outline encloses: the section that gets revolved.

    Exact, arcs included, because the figure is shown to whoever opens the
    model. Corner to corner and ignore the bulge is out by a fraction of a
    percent on a filleted part -- small, and still a wrong number on a screen.

    Each piece contributes its own part of the same contour integral, so
    nothing here has to reason about which way round the outline was walked or
    which side of its chord an arc leans: getting that wrong is how an area
    comes out plausible and wrong, and this way there is nothing to get wrong.
    """
    total = 0.0

    for curve in curves:
        if not curve.is_arc:
            x1, y1 = curve.start
            x2, y2 = curve.end
            total += (x1 * y2 - x2 * y1) / 2
            continue

        cx, cy = curve.centre  # type: ignore[misc]
        radius = math.dist(curve.centre, curve.start)  # type: ignore[arg-type]
        a0 = math.atan2(curve.start[1] - cy, curve.start[0] - cx)
        a1 = a0 + curve._sweep()
        total += (
            cx * radius * (math.sin(a1) - math.sin(a0))
            - cy * radius * (math.cos(a1) - math.cos(a0))
            + radius**2 * (a1 - a0)
        ) / 2

    return abs(total)


def _within(point: Point, polygon: list[Point]) -> bool:
    """Whether a point lies inside a closed polygon.

    Ray casting, counting the edges a ray to the right crosses. Odd is inside.
    """
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        (xi, yi), (xj, yj) = current, previous
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        previous = current
    return inside


def _enclosed_by(inner: list[Curve], outer: list[Curve]) -> bool:
    """Whether one closed outline lies wholly within another.

    Within the outline itself rather than within the box around it, and the
    difference is a part with a notch cut out of its corner: the empty square
    where the notch is falls inside the box and is not inside the part. A hole
    cut there would be a hole in nothing.
    """
    boundary = _polygon(outer)
    return all(_within(point, boundary) for point in _polygon(inner))


def _closed_loops(curves: list[Curve], tol: float) -> list[list[Curve]]:
    """The closed outlines in a group of geometry, with no axis to clip against.

    What a turned part needs is the outline beside its centre line, which is a
    different question and has `_loops_on` to answer it. This is the plainer
    one: which chains here close, and enclose something. It is what a part of
    constant section is read from, and what the groups nobody read are counted
    by.
    """
    return [
        chain
        for chain in _chains(curves, tol)
        if math.dist(chain[0].start, chain[-1].end) <= tol
        and section_area(chain) > tol * tol
    ]


def _closed_count(curves: list[Curve], tol: float) -> int:
    """How many closed outlines a group of geometry holds.

    For the groups that were not read. The question is only how much was left
    on the sheet, so anything that closes and encloses something counts once.
    """
    try:
        return len(_closed_loops(curves, tol))
    except DrawingError:
        # Lines crossing inside a group nobody read is not a reason to refuse
        # the drawing. It is a reason not to claim a count for that group.
        return 1


def _outline_note(count: int) -> list[str]:
    """What is said about the closed outlines that did not become the part.

    Said rather than dropped: a closed outline left on the sheet is the first
    thing worth knowing when the answer comes out the wrong shape.
    """
    if count == 1:
        return ["1 other closed outline on the sheet"]
    if count > 1:
        return [f"{count} other closed outlines on the sheet"]
    return []


def _loose_note(count: int) -> list[str]:
    """What is said about edges that were drawn and did not become anything.

    An outline is assembled end to end, so an edge that stops partway along
    another joins nothing and falls out of every loop. That is how an ordinary
    outside view of a bored shaft loses its bore: the hidden lines meet the end
    faces in the middle rather than at a corner, the outline closes without
    them, and back comes a convincing solid cylinder a fifth heavier than the
    part. Measured on one: 31415.927 mm3 where the drawing says 26389.378.

    Said rather than refused, because the same leftover is sometimes the right
    answer. A keyway shown with two hidden lines is left out on purpose -- a
    turned part does not have it cut -- and nothing in the geometry tells that
    edge from the one that mattered. What can be said is that an edge was
    drawn and not used, which is enough to check the section against the
    drawing.
    """
    if count == 1:
        return ["1 edge that closes into no outline, left out"]
    if count > 1:
        return [f"{count} edges that close into no outline, left out"]
    return []


def _loose_count(curves: list[Curve], tol: float) -> int:
    """How many edges here belong to no closed outline at all.

    For the plain reading, where there is no axis to close anything along.
    """
    try:
        chains = _chains(curves, tol)
    except DrawingError:
        # Lines crossing is reported by whoever asked for the loops. Not
        # somewhere to raise a second time from.
        return 0
    return sum(
        len(chain)
        for chain in chains
        if math.dist(chain[0].start, chain[-1].end) > tol
        or section_area(chain) <= tol * tol
    )


def _circles(curves: list[Curve], tol: float) -> set[int]:
    """Which curves here are arcs that together come to whole circles.

    A full circle in a view is a hole seen end-on, a bolt circle, or a round
    part seen down its axis. What it is never is the profile of a turned part:
    revolving a circle about a line through it builds a sphere, and about a
    line beside it a torus, and the drawing said neither.

    Totted up by centre and radius rather than taken on trust from the entity
    that produced them, so a circle drawn as two halves, or as a closed
    polyline of arcs, is still a circle.
    """
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, curve in enumerate(curves):
        if not curve.is_arc:
            continue
        cx, cy = curve.centre  # type: ignore[misc]
        radius = math.dist(curve.centre, curve.start)  # type: ignore[arg-type]
        key = (round(cx / tol), round(cy / tol), round(radius / tol))
        groups.setdefault(key, []).append(i)

    whole: set[int] = set()
    for members in groups.values():
        sweep = sum(abs(curves[i]._sweep()) for i in members)
        if abs(sweep - 2 * math.pi) <= 1e-9:
            whole.update(members)
    return whole


def _loops_on(
    curves: list[Curve], axis: Axis, keep: int, tol: float
) -> tuple[list[list[Curve]], list[Curve]]:
    """Closed outlines on one side of the axis, closing along it where needed.

    With the edges that closed into none of them, which are not thrown away
    here: see `_loose_note` for what a dropped edge costs.
    """
    # A circle is not a profile, and it is the one shape that would otherwise
    # look like a very good one: closed, and lying right against the axis.
    circles = _circles(curves, tol)

    kept: list[Curve] = []
    for index, curve in enumerate(curves):
        if index in circles:
            continue
        # A curve lying along the axis is either the centre line itself or an
        # edge with no radius. Neither bounds anything to revolve.
        if (
            abs(axis.signed_distance(curve.start)) <= tol
            and abs(axis.signed_distance(curve.end)) <= tol
        ):
            continue
        clipped = _clip(curve, axis, keep, tol)
        if clipped is not None and clipped.length > tol:
            kept.append(clipped)

    loops: list[list[Curve]] = []
    loose: list[Curve] = []
    for chain in _chains(kept, tol):
        first, last = chain[0].start, chain[-1].end
        if math.dist(first, last) <= tol:
            closed = chain
        elif (
            # Open, but both ends on the axis: this is the solid case, where
            # the material runs to the centre and the axis is the missing edge.
            abs(axis.signed_distance(first)) <= tol
            and abs(axis.signed_distance(last)) <= tol
        ):
            closed = [*chain, Curve(last, first)]
        else:
            loose.extend(chain)
            continue

        # An outline that encloses nothing is a line drawn back over itself.
        # It is no more the profile than the loose ones are, and it was no
        # less drawn.
        if section_area(closed) > tol * tol:
            loops.append(closed)
        else:
            loose.extend(chain)

    return loops, loose


def _signature(candidate: tuple[float, float, int, list[Curve]]) -> tuple[float, float]:
    """How near the axis a closed outline lies, and how much it encloses.

    Enough to recognise an outline's own reflection, which is the only thing
    that has to be recognised.
    """
    return (round(candidate[0], 6), round(candidate[1], 6))


def profile_of(
    outline: list[Curve], axis: Axis, tol: float
) -> tuple[list[Curve], list[str], int, int, tuple[float, float]]:
    """The outline to revolve, chosen from both sides of the axis.

    One view's worth of outline, since the sheet has been split by then. What
    is left to choose between is the two sides of the centre line, and a
    drawing office draws both.

    Both sides, rather than picking one by which carries more geometry: a title
    block is more line than a small part, and a rule that counts length hands
    back the title block on a drawing where it happens to sit on the busier
    side. What identifies the profile is that it is the closed outline lying
    against the axis, and that holds whichever side it was drawn on.

    Reports how near the axis the winner lies and how much it encloses, which
    is what one view is weighed against another with, and how many edges on
    the winning side became nothing.
    """
    by_side: dict[int, list[tuple[float, float, int, list[Curve]]]] = {}
    adrift: dict[int, int] = {}
    for keep in (1, -1):
        found = []
        on_side, loose = _loops_on(outline, axis, keep, tol)
        for loop in on_side:
            distance = min(abs(axis.signed_distance(p)) for p in _polygon(loop))
            found.append((distance, -section_area(loop), keep, loop))
        by_side[keep] = found
        # Per side, because the side that was not revolved is the drawing's
        # other half: everything on it is left out, and none of it is missing.
        adrift[keep] = len(loose)

    candidates = by_side[1] + by_side[-1]

    if not candidates:
        raise DrawingError(
            "no closed outline was found beside the centre line. The profile "
            "has to close: check for gaps at the corners, and that the end "
            "faces are drawn."
        )

    distance, negative_area, keep, loop = min(candidates, key=lambda c: (c[0], c[1]))
    side = "above" if keep > 0 else "below"
    assumptions = [
        f"revolved the outline {side} the centre line",
        f"{len(loop)} edges enclosing {-negative_area:.1f} mm2 of section",
    ]
    if distance > tol:
        assumptions.append(
            f"bored: the section stops {distance:.3f} mm short of the axis"
        )

    # A symmetric drawing offers the same outline twice, once per side, and its
    # own reflection is not another outline. Matched off one side against the
    # other rather than by collapsing everything that looks alike: four
    # identical views in a row are four, and saying one would be a quieter kind
    # of wrong.
    unmatched = [_signature(c) for c in by_side[1]]
    others = len(unmatched)
    for candidate in by_side[-1]:
        signature = _signature(candidate)
        if signature in unmatched:
            unmatched.remove(signature)
        else:
            others += 1
    others -= 1  # the one being revolved

    return loop, assumptions, others, adrift[keep], (distance, negative_area)


def read_profile(source: Path) -> Profile:
    """A DXF drawing of a turned part, read down to the outline to revolve."""
    return profile_from(*read_curves(source))


def read_part(source: Path) -> Profile | Prism:
    """A DXF drawing read down to the one thing it can be built from.

    Two readings, and the sheet says which. A turned part is drawn with a
    centre line running the length of it -- ISO 128 asks for one, and without
    it there is nothing to revolve about. A part of constant section is drawn
    without one, and instead in two views that line up, because the depth it
    runs is not in the view that shows its shape.

    Turned first, and by the stronger evidence: a centre line claimed by a
    view. A sheet with centre lines that claim nothing -- a plate covered in
    bolt-hole crosses -- reaches the prism, which is where it belongs.
    """
    outline, candidates, units, ignored = read_curves(source)
    if not outline:
        raise DrawingError(
            "the drawing has no lines or arcs outside its dimensions and notes."
        )

    views = split_views(outline, candidates)
    if any(view.has_axis for view in views):
        return profile_from(outline, candidates, units, ignored)

    if any(len(group) > 1 for group in in_projection(views, _view_gap(outline))):
        return prism_from(outline, candidates, units, ignored)

    # Neither: no view holds a centre line and no two views line up. The
    # turned reading owns the refusal, because a sheet that reaches here is a
    # sheet with one drawing on it and no axis -- and its message is the one
    # that says what to draw.
    return profile_from(outline, candidates, units, ignored)


def prism_from(
    outline: list[Curve],
    candidates: list[Curve],
    units: str,
    ignored: list[str],
) -> Prism:
    """Two views in projection, read down to an outline and the depth it runs.

    The shape comes from the view that carries it and the depth from the view
    that does not: of two views in projection, one shows the section and the
    other shows it edge-on, as a band whose width is the whole thickness of
    the part. Which is which is read off the outlines -- the section has
    corners, the band has four.

    Two views and no more. A part that needed a third to describe it is not a
    part of constant section, and reading it as one would be the quiet kind of
    wrong: a solid that measures exactly, and is the wrong solid.
    """
    tol = tolerance_for(outline + candidates)
    gap = _view_gap(outline)
    views = split_views(outline, candidates)

    lined_up = [group for group in in_projection(views, gap) if len(group) > 1]
    if not lined_up:
        raise DrawingError(
            "no centre line runs the length of anything here, so this is not "
            "a turned part -- and no two of its views line up, so there is no "
            "second view to take a depth from. A part of constant section "
            "needs two: the shape in one, the thickness in the other."
        )

    group = max(lined_up, key=len)
    if len(group) > 2:
        raise DrawingError(
            f"{len(group)} views of this part line up. A part drawn in more "
            "than two views is not usually one that runs straight through, "
            "and reading it as though it does would give a solid that "
            "measures exactly and is the wrong shape."
        )

    loops: dict[int, list[list[Curve]]] = {}
    for view in group:
        try:
            found = _closed_loops(view.curves, tol)
        except DrawingError:
            found = []
        if found:
            loops[id(view)] = found

    def widest(view: View) -> list[Curve]:
        return max(loops[id(view)], key=section_area)

    if len(loops) < 2:
        raise DrawingError(
            "the two views that line up do not both close into an outline. "
            "Check for gaps at the corners: the shape and the thickness are "
            "each read as a closed loop."
        )

    # The section is the view with more to it. A part drawn edge-on is a
    # rectangle whatever its shape, so corners are what tell the two apart --
    # and area breaks the tie for a part that really is a box.
    section = max(
        group, key=lambda view: (len(widest(view)), section_area(widest(view)))
    )
    edge_on = next(view for view in group if view is not section)
    loop = widest(section)

    # Everything else that closes inside the outline is material the part
    # does not have. A loop that is not inside it is something else entirely,
    # and guessing which would be a way to cut a hole in the wrong place.
    holes, beside = [], 0
    for other in loops[id(section)]:
        if other is loop:
            continue
        if _enclosed_by(other, loop):
            holes.append(other)
        else:
            beside += 1

    if beside:
        raise DrawingError(
            f"{beside} closed outline(s) sit beside the part rather than "
            "inside it, in the view being read. What they are is not "
            "something this can tell, and cutting them out or leaving them "
            "in would each be a guess."
        )

    sx0, _sy0, sx1, _sy1 = section.box
    ex0, ey0, ex1, ey1 = edge_on.box
    # Which way the two views line up is which way the depth is measured. They
    # share a row or a column -- never both, or they would be one view.
    lengthways = min(sx1, ex1) <= max(sx0, ex0)
    depth = (ex1 - ex0) if lengthways else (ey1 - ey0)

    if depth <= tol:
        raise DrawingError("the second view has no thickness in it to read")

    read = {id(view) for view in group}
    elsewhere = sum(
        _closed_count(view.curves, tol) for view in views if id(view) not in read
    )
    # In the view the outline and its holes come from. The other view of the
    # pair is measured across rather than read, so nothing in it is left out.
    loose = _loose_count(section.curves, tol)

    assumptions = [
        "read as a part of constant section: the outline is taken to run "
        "straight through, unchanged",
        f"{depth:.4g} mm deep, from the view drawn "
        + ("beside it" if lengthways else "above or below it"),
        f"{len(loop)} edges enclosing {section_area(loop):.1f} mm2 of section",
    ]
    if holes:
        cut = sum(section_area(hole) for hole in holes)
        assumptions.append(
            f"{len(holes)} outline(s) inside it, cut out as holes running the "
            f"same depth: {cut:.1f} mm2 of section taken away"
        )

    return Prism(
        curves=loop,
        depth=depth,
        units=units,
        holes=holes,
        assumptions=assumptions,
        ignored=[*_outline_note(elsewhere), *_loose_note(loose), *ignored],
    )


def profile_from(
    outline: list[Curve],
    candidates: list[Curve],
    units: str,
    ignored: list[str],
) -> Profile:
    """Everything after the file has been read, whatever read it.

    A DXF and a printed sheet arrive as different files and end up as the same
    thing: curves, and the ones among them that were drawn as a centre line.
    From here on there is nothing left that knows which it was, which is why
    this is one function rather than two.

    The sheet is divided into views first, and then read one view at a time.
    The difference is the axis: a sheet holds more than one centre line -- an
    end view has two crossing it, a section has its own -- and taking the
    longest of them for the whole sheet is how a part comes out with every
    radius wrong and nothing to show for it.
    """
    if not outline:
        raise DrawingError(
            "the drawing has no lines or arcs outside its dimensions and notes."
        )

    tol = tolerance_for(outline + candidates)
    gap = _view_gap(outline)
    views = split_views(outline, candidates)
    readable = [view for view in views if view.has_axis]

    # A sheet where no group has a centre line drawn for it is one this has
    # no division of, so it is not divided: reading the whole of it at once is what this
    # did before views existed, and is blinder rather than wrong. Refusing a
    # drawing that reads today would be the worse trade.
    divided = bool(readable)
    if not divided:
        readable = [View(list(outline), list(candidates))]

    # How many views each one is drawn in line with, itself included. A view
    # that is one of several of the same part is worth more than a lone
    # outline that happens to enclose more, which is the only thing size on
    # its own can say -- and a detail drawn at 5:1 encloses a great deal.
    kin: dict[int, list[View]] = {}
    if divided:
        for group in in_projection(views, gap):
            for view in group:
                kin[id(view)] = group

    readings = []
    refused: DrawingError | None = None
    for view in readable:
        axis = find_axis(view.axis_candidates)
        try:
            loop, said, others, loose, score = profile_of(view.curves, axis, tol)
        except DrawingError as error:
            # One view that cannot be read is ordinary: an end view has a
            # centre line and no profile beside it. Keep the first refusal in
            # case every view turns out that way.
            refused = refused or error
            continue
        standing = len(kin.get(id(view), (view,)))
        readings.append(((-standing, *score), view, axis, loop, said, others, loose))

    if not readings:
        # Unreachable with nothing to raise: `readable` is never empty, so
        # either a view was read or a view refused.
        raise refused  # type: ignore[misc]

    readings.sort(key=lambda reading: reading[0])
    _key, chosen, axis, loop, said, others, loose = readings[0]

    # Readings of outlines this one does not line up with. Each is a part this
    # sheet could have been saying, and nothing in the geometry chooses
    # between them.
    family = {id(view) for view in kin.get(id(chosen), [chosen])}
    rivals = sum(1 for reading in readings[1:] if id(reading[1]) not in family)

    elsewhere = (
        sum(_closed_count(view.curves, tol) for view in views if view is not chosen)
        if divided
        else 0
    )

    assumptions = [
        "read as a solid of revolution: the part is taken to be turned",
        f"axis from the {axis.found_by}",
    ]
    if divided and len(views) > 1:
        assumptions.append(
            f"one of {len(views)} groups of geometry on the sheet: "
            "the one this centre line runs along"
        )
    if len(family) > 1:
        assumptions.append(
            f"{len(family)} views of the part line up on the sheet, and the "
            "profile is read from this one"
        )
    if rivals:
        readings_left = (
            "another reading of this sheet was possible"
            if rivals == 1
            else f"{rivals} other readings of this sheet were possible"
        )
        assumptions.append(
            f"{readings_left}, of an outline that does not line up with this "
            "one -- a detail drawn to another scale looks like that, and "
            "nothing in the geometry chooses between them"
        )

    return Profile(
        axis=axis,
        curves=loop,
        units=units,
        assumptions=[*assumptions, *said],
        ignored=[*_outline_note(others + elsewhere), *_loose_note(loose), *ignored],
    )


def _whole_circle(curves: list[Curve]) -> tuple[Point, float] | None:
    """The centre and radius, if these curves are one complete circle.

    Asked at the point of building rather than of reading, and that is the
    division of labour: the reading splits a circle into quarters so that a
    centre line through it is never crossed partway, and the build puts it
    back so that a bore is one face.

    One face matters. Every measurement in the viewer snaps to a face, and a
    bore in four pieces is a bore somebody has to click four times and can
    never take a diameter from.
    """
    if not curves or not all(curve.is_arc for curve in curves):
        return None

    centre = curves[0].centre
    radius = math.dist(centre, curves[0].start)  # type: ignore[arg-type]
    if radius <= 0:
        return None

    near = radius * 1e-9
    for curve in curves:
        if math.dist(curve.centre, centre) > near:  # type: ignore[arg-type]
            return None
        if abs(math.dist(curve.centre, curve.start) - radius) > near:  # type: ignore[arg-type]
            return None

    sweep = sum(abs(curve._sweep()) for curve in curves)
    if abs(sweep - 2 * math.pi) > 1e-9:
        return None

    return centre, radius  # type: ignore[return-value]


def _wire_of(curves: list[Curve]):
    """One closed outline as an OCCT wire.

    Arcs stay arcs here, which is why the reading kept them as arcs all the
    way down: a fillet built from a chord is a fillet nobody can measure.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    def point(p: Point) -> gp_Pnt:
        return gp_Pnt(p[0], p[1], 0.0)

    wire = BRepBuilderAPI_MakeWire()

    round_one = _whole_circle(curves)
    if round_one is not None:
        centre, radius = round_one
        whole = gp_Circ(gp_Ax2(point(centre), gp_Dir(0, 0, 1)), radius)
        wire.Add(BRepBuilderAPI_MakeEdge(whole).Edge())
        if not wire.IsDone():
            raise DrawingError("a circle on the sheet did not close")
        return wire.Wire()

    for curve in curves:
        if not curve.is_arc:
            wire.Add(
                BRepBuilderAPI_MakeEdge(point(curve.start), point(curve.end)).Edge()
            )
            continue
        centre = curve.centre
        radius = math.dist(centre, curve.start)  # type: ignore[arg-type]
        # An edge built on a circle runs anticlockwise about that circle's own
        # axis, so a clockwise arc is the same circle seen from the other side.
        normal = gp_Dir(0, 0, 1 if curve.ccw else -1)
        circle = gp_Circ(gp_Ax2(point(centre), normal), radius)  # type: ignore[arg-type]
        wire.Add(
            BRepBuilderAPI_MakeEdge(circle, point(curve.start), point(curve.end)).Edge()
        )

    if not wire.IsDone():
        raise DrawingError("the outline did not close into a single loop")

    return wire.Wire()


def _face_of(curves: list[Curve], holes: list[list[Curve]] | None = None):
    """The closed outline as a face OCCT can build on, with its holes in it.

    A hole is a wire added to the face running the other way round. Which way
    round it runs is the whole of it: the same wire the right way is a second
    face, and the same wire the wrong way is a face OCCT will build and then
    measure as though the hole were solid.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.TopoDS import TopoDS

    face = BRepBuilderAPI_MakeFace(_wire_of(curves), True)
    if not face.IsDone():
        raise DrawingError("the outline does not bound a section that can be built on")

    for hole in holes or []:
        # Reversing hands back a shape rather than a wire, and `Add` takes a
        # wire. The cast is the same object seen as what it is.
        face.Add(TopoDS.Wire_s(_wire_of(hole).Reversed()))
        if not face.IsDone():
            raise DrawingError("an outline inside the part could not be cut out of it")

    return face.Face()


def revolve(profile: Profile):
    """The profile swept a full turn about its axis, as an OCCT solid.

    From here on the result is an ordinary B-rep and is treated as one: the
    same tessellation, the same face groups, the same exact edges and faces the
    measurement tools snap to. What it is not is a reading of a part -- it is a
    reading of a drawing of a part, which is why it is labelled `derived` and
    carries its assumptions with it.
    """
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    axis = gp_Ax1(
        gp_Pnt(profile.axis.point[0], profile.axis.point[1], 0.0),
        gp_Dir(profile.axis.direction[0], profile.axis.direction[1], 0.0),
    )
    solid = BRepPrimAPI_MakeRevol(_face_of(profile.curves), axis, 2 * math.pi).Shape()

    if not BRepCheck_Analyzer(solid).IsValid():
        raise DrawingError(
            "revolving the outline did not produce a valid solid. The usual "
            "cause is an outline that crosses itself, or one drawn on both "
            "sides of the centre line at once."
        )
    return solid


def extrude(prism: Prism):
    """The outline run straight through by its depth, as an OCCT solid.

    The other of the two builds, and the same afterwards: a real B-rep, with
    the same tessellation, the same face groups, the same exact edges the
    measurement tools snap to. Labelled `derived` for the same reason -- it is
    a reading of a drawing of a part, not of a part.

    Drawn flat on the sheet and pushed along Z, so the model comes out standing
    the way the section was drawn. Which face is which was never in the
    drawing to begin with; what the drawing gave was a shape and a thickness,
    and that is what this builds.
    """
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    solid = BRepPrimAPI_MakePrism(
        _face_of(prism.curves, prism.holes), gp_Vec(0.0, 0.0, prism.depth)
    ).Shape()

    if not BRepCheck_Analyzer(solid).IsValid():
        raise DrawingError(
            "running the outline through did not produce a valid solid. The "
            "usual cause is an outline that crosses itself."
        )
    return solid


def convert(source: Path, out_glb: Path, deflection: float | None = None):
    """A DXF drawing, as far as a .glb the viewer can open.

    Whichever of the two the sheet turns out to be. The build differs and the
    label differs; everything after is the same B-rep treated the same way.
    """
    from app.cad import occt

    read = read_part(source)

    if isinstance(read, Prism):
        solid = extrude(read)
        derived = DerivedGeometry(
            method="dxf-extrude",
            assumptions=read.assumptions,
            ignored=read.ignored,
        )
    else:
        solid = revolve(read)
        derived = DerivedGeometry(
            method="dxf-revolve",
            axis_point=(read.axis.point[0], read.axis.point[1], 0.0),
            axis_direction=(read.axis.direction[0], read.axis.direction[1], 0.0),
            assumptions=read.assumptions,
            ignored=read.ignored,
        )

    part = occt.Part(
        id="n1",
        # A DXF names a drawing, not a part. Leaving `named` false is what
        # keeps the file name from being presented as the part's own name.
        name=source.stem,
        shape=solid,
        color=None,
        named=False,
    )

    return occt.build(
        [part], out_glb, deflection, geometry_source="derived", derived=derived
    )
