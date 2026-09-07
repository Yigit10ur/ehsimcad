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

    # CIRCLE is deliberately absent. A full circle in a turned part's drawing
    # belongs to the end view, not to the profile, and revolving it would build
    # a torus nobody drew.
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


def _loops_on(
    curves: list[Curve], axis: Axis, keep: int, tol: float
) -> list[list[Curve]]:
    """Closed outlines on one side of the axis, closing along it where needed."""
    kept: list[Curve] = []
    for curve in curves:
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
    for chain in _chains(kept, tol):
        first, last = chain[0].start, chain[-1].end
        if math.dist(first, last) <= tol:
            loops.append(chain)
            continue
        # Open, but both ends on the axis: this is the solid case, where the
        # material runs to the centre and the axis is the missing edge.
        if (
            abs(axis.signed_distance(first)) <= tol
            and abs(axis.signed_distance(last)) <= tol
        ):
            loops.append([*chain, Curve(last, first)])

    return [loop for loop in loops if section_area(loop) > tol * tol]


def _signature(candidate: tuple[float, float, int, list[Curve]]) -> tuple[float, float]:
    """How near the axis a closed outline lies, and how much it encloses.

    Enough to recognise an outline's own reflection, which is the only thing
    that has to be recognised.
    """
    return (round(candidate[0], 6), round(candidate[1], 6))


def profile_of(
    outline: list[Curve], axis: Axis, tol: float
) -> tuple[list[Curve], list[str], list[str]]:
    """The outline to revolve, chosen from both sides of the axis.

    Both sides, rather than picking one by which carries more geometry: a title
    block is more line than a small part, and a rule that counts length hands
    back the title block on a drawing where it happens to sit on the busier
    side. What identifies the profile is that it is the closed outline lying
    against the axis, and that holds whichever side it was drawn on.
    """
    by_side: dict[int, list[tuple[float, float, int, list[Curve]]]] = {}
    for keep in (1, -1):
        found = []
        for loop in _loops_on(outline, axis, keep, tol):
            distance = min(abs(axis.signed_distance(p)) for p in _polygon(loop))
            found.append((distance, -section_area(loop), keep, loop))
        by_side[keep] = found

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
    ignored = []
    if others == 1:
        ignored.append("1 other closed outline on the sheet")
    elif others > 1:
        ignored.append(f"{others} other closed outlines on the sheet")

    return loop, assumptions, ignored


def read_profile(source: Path) -> Profile:
    """A DXF drawing of a turned part, read down to the outline to revolve."""
    return profile_from(*read_curves(source))


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
    """
    if not outline:
        raise DrawingError(
            "the drawing has no lines or arcs outside its dimensions and notes."
        )

    tol = tolerance_for(outline + candidates)
    axis = find_axis(candidates)
    loop, assumptions, left_out = profile_of(outline, axis, tol)

    return Profile(
        axis=axis,
        curves=loop,
        units=units,
        assumptions=[
            "read as a solid of revolution: the part is taken to be turned",
            f"axis from the {axis.found_by}",
            *assumptions,
        ],
        ignored=[*left_out, *ignored],
    )


def revolve(profile: Profile):
    """The profile swept a full turn about its axis, as an OCCT solid.

    From here on the result is an ordinary B-rep and is treated as one: the
    same tessellation, the same face groups, the same exact edges and faces the
    measurement tools snap to. What it is not is a reading of a part -- it is a
    reading of a drawing of a part, which is why it is labelled `derived` and
    carries its assumptions with it.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    def point(p: Point) -> gp_Pnt:
        return gp_Pnt(p[0], p[1], 0.0)

    wire = BRepBuilderAPI_MakeWire()
    for curve in profile.curves:
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

    face = BRepBuilderAPI_MakeFace(wire.Wire(), True)
    if not face.IsDone():
        raise DrawingError("the outline does not bound a section that can be revolved")

    axis = gp_Ax1(
        gp_Pnt(profile.axis.point[0], profile.axis.point[1], 0.0),
        gp_Dir(profile.axis.direction[0], profile.axis.direction[1], 0.0),
    )
    solid = BRepPrimAPI_MakeRevol(face.Face(), axis, 2 * math.pi).Shape()

    if not BRepCheck_Analyzer(solid).IsValid():
        raise DrawingError(
            "revolving the outline did not produce a valid solid. The usual "
            "cause is an outline that crosses itself, or one drawn on both "
            "sides of the centre line at once."
        )
    return solid


def convert(source: Path, out_glb: Path, deflection: float | None = None):
    """A DXF drawing of a turned part, as far as a .glb the viewer can open."""
    from app.cad import occt

    profile = read_profile(source)
    solid = revolve(profile)

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
        [part],
        out_glb,
        deflection,
        geometry_source="derived",
        derived=DerivedGeometry(
            method="dxf-revolve",
            axis_point=(profile.axis.point[0], profile.axis.point[1], 0.0),
            axis_direction=(
                profile.axis.direction[0],
                profile.axis.direction[1],
                0.0,
            ),
            assumptions=profile.assumptions,
            ignored=profile.ignored,
        ),
    )
