"""Schema of the metadata.json emitted alongside every generated .glb.

This mirrors ARCHITECTURE.md section 5. The viewer reads nothing else about
model structure, so this file is the contract between the two services.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Vec3 = tuple[float, float, float]
BBox = tuple[Vec3, Vec3]


class TreeNode(BaseModel):
    """One node of the assembly tree, as read from the STEP product structure."""

    id: str
    name: str
    children: list[TreeNode] = Field(default_factory=list)
    mesh_index: int | None = None


class PartMetadata(BaseModel):
    """Mass properties of one part.

    Exact when the source was a B-rep. A mesh source can only be measured, and
    a mesh that is not watertight encloses no volume at all -- hence the
    optional volume rather than a confident wrong number.
    """

    volume_mm3: float | None
    area_mm2: float
    com: Vec3
    bbox: BBox


class EdgeGeometry(BaseModel):
    """One B-rep edge, described well enough to snap a measurement to it.

    Circles carry their centre, axis and radius so the viewer can report a
    diameter that comes from the CAD definition rather than from three points
    fitted to a tessellated polyline.
    """

    kind: Literal["line", "circle", "other"]
    start: Vec3
    end: Vec3
    length: float
    centre: Vec3 | None = None
    axis: Vec3 | None = None
    radius: float | None = None


class FaceGeometry(BaseModel):
    """One B-rep face. Parallel to the part's entry in `face_groups`."""

    kind: Literal["plane", "cylinder", "cone", "sphere", "other"]
    # Planes carry a normal, cylinders and cones an axis and a radius. Angle
    # measurement between two planar faces needs the exact normals, not ones
    # averaged from triangles.
    normal: Vec3 | None = None
    axis: Vec3 | None = None
    radius: float | None = None


class SnapGeometry(BaseModel):
    """What a part offers a measurement to snap onto.

    Kept per part and exact. For very large assemblies this grows faster than
    the rest of the metadata and will want a binary sidecar, but at MVP sizes
    it is small next to the glb.
    """

    vertices: list[Vec3] = Field(default_factory=list)
    edges: list[EdgeGeometry] = Field(default_factory=list)
    faces: list[FaceGeometry] = Field(default_factory=list)


GeometrySource = Literal["brep", "mesh", "derived"]


class DerivedGeometry(BaseModel):
    """How a model was worked out from a drawing, rather than read from a solid.

    Present only when `geometry_source` is `derived`. Everything in here is a
    decision the reader made on the viewer's behalf, written down so that
    somebody looking at the model can see what it rests on -- which line was
    taken for the axis, which outline was revolved, what was ignored. A derived
    model measures as exactly as any other; what is uncertain is whether it is
    the right model, and that is not something a number can carry.
    """

    method: Literal["dxf-revolve"]
    axis_point: Vec3
    axis_direction: Vec3
    # What the reading decided. Plain sentences, meant to be shown to whoever
    # opens the model.
    assumptions: list[str] = Field(default_factory=list)
    # What was on the sheet and is not in the part. Kept apart from the
    # assumptions because it answers a different question, and because it is
    # the first place to look when the result is the wrong shape: a drawing
    # whose outline came through as splines says so here.
    ignored: list[str] = Field(default_factory=list)


class ModelMetadata(BaseModel):
    """What the viewer knows about a model.

    `geometry_source` is the honest label on everything else here. A `brep`
    model carries exact mass properties, face groups and snap targets; a `mesh`
    model carries measured properties and no snap data at all, because a
    triangle corner is not a design intent and pretending otherwise would put a
    wrong number in front of someone reading a dimension.

    `derived` is a B-rep too, and measures like one -- but it was reconstructed
    from a drawing, so its numbers are only as right as the reading of that
    drawing. `derived` below says what that reading assumed.
    """

    geometry_source: GeometrySource = "brep"
    # Set only for a `derived` source: what was assumed to get here. Absent on
    # anything read from a real solid, where nothing was assumed.
    derived: DerivedGeometry | None = None
    # What the CAD file calls this model, or None when it does not say. A mesh
    # file never says: an STL has no product structure, so its "name" would only
    # ever be the file name coming back around.
    declared_name: str | None = None
    tree: list[TreeNode]
    parts: dict[str, PartMetadata]
    units: str = "mm"
    # Part id -> list of [start, end) triangle ranges, one per B-rep face.
    # The ranges tile the part's triangle list in order. Face level selection
    # and measurement snapping both depend on this.
    face_groups: dict[str, list[tuple[int, int]]] = Field(default_factory=dict)
    # Part id -> exact vertices, edges and face definitions for measurement
    # snapping. See ARCHITECTURE.md section 10: measurements are snapped to
    # this, never to the mesh.
    # Present only for a B-rep source. Absent means the viewer falls back to
    # the raw point under the cursor, which is the correct behaviour for a
    # mesh: there is nothing exact to snap to.
    snap: dict[str, SnapGeometry] = Field(default_factory=dict)


class ConversionResult(BaseModel):
    glb_path: str
    metadata: ModelMetadata
    triangle_count: int
    deflection: float


TreeNode.model_rebuild()
