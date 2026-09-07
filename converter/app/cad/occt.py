"""OCCT backed conversion: STEP/IGES -> tessellated .glb plus metadata.

Everything that imports OpenCascade lives behind this module so the rest of the
service -- and CI -- can run without it installed. Import it lazily.

The output contract is two files (ARCHITECTURE.md section 5):

    model.glb        one node per part, plus a LINES primitive per part holding
                     the B-rep edges. Edges are what make the result read as
                     CAD rather than as a triangle soup.
    metadata.json    assembly tree, exact mass properties, face groups.

Mass properties are computed from the B-rep, never from the tessellation: the
mesh is an approximation chosen for display, and quoting a volume derived from
it would be quietly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.models import (
    ConversionResult,
    DerivedGeometry,
    EdgeGeometry,
    FaceGeometry,
    GeometrySource,
    ModelMetadata,
    PartMetadata,
    SnapGeometry,
    TreeNode,
    Vec3,
)


def available() -> bool:
    """Whether the OCCT bindings can be imported in this environment."""
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True


# Angular deflection used for both surface tessellation and edge discretisation.
# 0.5 rad keeps cylinders smooth without exploding the triangle count.
ANGULAR_DEFLECTION = 0.5

# Used when the source file carries no colour for a part.
DEFAULT_COLOR = (0.62, 0.64, 0.67)


@dataclass
class Part:
    """One leaf solid of the assembly, with its placement already applied."""

    id: str
    name: str
    shape: object
    color: tuple[float, float, float] | None
    children: list[Part] = field(default_factory=list)
    is_assembly: bool = False
    # Whether `name` came from the file or from the fallback below. A caller
    # that wants to trust the name has to be able to tell the two apart, and
    # "does it look like 'Part 3'?" is not a test.
    named: bool = False


def _open_document(source: Path):
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.IGESCAFControl import IGESCAFControl_Reader
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    suffix = source.suffix.lower()
    reader = (
        IGESCAFControl_Reader()
        if suffix in {".iges", ".igs"}
        else STEPCAFControl_Reader()
    )
    reader.SetNameMode(True)
    reader.SetColorMode(True)

    status = reader.ReadFile(str(source))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"could not read {source.name}: {status}")
    if not reader.Transfer(doc):
        raise ValueError(f"no transferable content in {source.name}")

    return doc


def read_parts(source: Path) -> list[Part]:
    """Walk the XCAF product structure into a tree of named, placed parts."""
    from OCP.Quantity import Quantity_Color
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.XCAFDoc import (
        XCAFDoc_ColorGen,
        XCAFDoc_ColorSurf,
        XCAFDoc_DocumentTool,
    )

    doc = _open_document(source)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def name_of(label) -> str | None:
        attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
            return str(attr.Get().ToExtString())
        return None

    def color_of(label) -> tuple[float, float, float] | None:
        shape = shape_tool.GetShape_s(label)
        color = Quantity_Color()
        for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            if color_tool.GetColor(shape, kind, color):
                return (color.Red(), color.Green(), color.Blue())
        return None

    def walk(label, path: str, fallback: str) -> Part:
        # A component label points at a shared definition; the name and colour
        # live on the definition, the placement on the component.
        source_label = label
        if shape_tool.IsReference_s(label):
            referred = TDF_Label()
            shape_tool.GetReferredShape_s(label, referred)
            source_label = referred

        declared = name_of(source_label) or name_of(label)
        part = Part(
            id=path,
            name=declared or fallback,
            shape=shape_tool.GetShape_s(label),
            color=color_of(source_label),
            is_assembly=shape_tool.IsAssembly_s(source_label),
            named=declared is not None,
        )

        if part.is_assembly:
            components = TDF_LabelSequence()
            shape_tool.GetComponents_s(source_label, components)
            for index in range(1, components.Length() + 1):
                # Underscore, not a dot: three.js strips "." from glTF node
                # names when it loads them, which would collapse "n1.1" and
                # "n11" onto the same id in the viewer.
                child_path = f"{path}_{index}"
                part.children.append(
                    walk(
                        components.Value(index),
                        child_path,
                        fallback=f"Part {child_path}",
                    )
                )

        return part

    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)

    return [
        walk(free.Value(i), f"n{i}", fallback=f"Part {i}")
        for i in range(1, free.Length() + 1)
    ]


def bounding_box(shape) -> tuple[Vec3, Vec3]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    # Bnd_Box carries a gap by default and Add_s inflates it further by the
    # shape tolerance, which showed up as a 27 micron error on a face that sits
    # exactly on a plane. The bounding box is quoted to the user as a
    # measurement, so it has to be the optimal one, taken with the gap zeroed.
    box.SetGap(0.0)
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def mass_properties(shape) -> PartMetadata:
    """Exact volume, area, centre of mass and bounding box from the B-rep."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    volume = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume)

    surface = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface)

    com = volume.CentreOfMass()
    low, high = bounding_box(shape)

    return PartMetadata(
        volume_mm3=volume.Mass(),
        area_mm2=surface.Mass(),
        com=(com.X(), com.Y(), com.Z()),
        bbox=(low, high),
    )


def tessellate(shape, deflection: float):
    """Triangulate a shape, keeping track of which triangles belong to which face.

    The face ranges are what allow the viewer to select a single B-rep face and
    to snap measurements to it, instead of picking an arbitrary triangle.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_Orientation
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    BRepMesh_IncrementalMesh(shape, deflection, False, ANGULAR_DEFLECTION, True)

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    face_ranges: list[tuple[int, int]] = []
    used_faces: list[object] = []

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)

        if triangulation is not None:
            start = len(triangles)
            offset = len(vertices)
            transform = location.Transformation()
            reversed_face = face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED

            for i in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(i).Transformed(transform)
                vertices.append((point.X(), point.Y(), point.Z()))

            for i in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(i).Get()
                if reversed_face:
                    a, c = c, a
                triangles.append((a - 1 + offset, b - 1 + offset, c - 1 + offset))

            face_ranges.append((start, len(triangles)))
            # Returned alongside the ranges so face metadata cannot drift out
            # of step with them: a face without a triangulation is skipped
            # here, and must be skipped there too.
            used_faces.append(face)

        explorer.Next()

    return (
        np.array(vertices, dtype=np.float64).reshape(-1, 3),
        np.array(triangles, dtype=np.int64).reshape(-1, 3),
        face_ranges,
        used_faces,
    )


def edge_polylines(shape) -> list[np.ndarray]:
    """Discretise every B-rep edge into a polyline.

    Sampled from the curve rather than from the triangulation so that circles
    stay round at coarse tessellation settings -- the edges carry most of the
    visual read of a CAD model.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)

    polylines: list[np.ndarray] = []
    for index in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge_s(edges.FindKey(index))
        if BRep_Tool.Degenerated_s(edge):
            continue

        curve = BRepAdaptor_Curve(edge)
        sampler = GCPnts_TangentialDeflection(curve, ANGULAR_DEFLECTION, 0.1)
        count = sampler.NbPoints()
        if count < 2:
            continue

        points = np.array(
            [
                (p.X(), p.Y(), p.Z())
                for p in (sampler.Value(i) for i in range(1, count + 1))
            ],
            dtype=np.float64,
        )
        polylines.append(points)

    return polylines


def describe_face(face) -> FaceGeometry:
    """Classify a face and pull out the parameters a measurement needs."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_Orientation

    adaptor = BRepAdaptor_Surface(face)
    kind = adaptor.GetType()

    if kind == GeomAbs_SurfaceType.GeomAbs_Plane:
        direction = adaptor.Plane().Axis().Direction()
        normal = (direction.X(), direction.Y(), direction.Z())
        # The surface normal ignores how the face is used in the solid, so a
        # reversed face would report an inward normal and flip any angle
        # measured against it.
        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            normal = (-normal[0], -normal[1], -normal[2])
        return FaceGeometry(kind="plane", normal=normal)

    if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        cylinder = adaptor.Cylinder()
        axis = cylinder.Axis().Direction()
        return FaceGeometry(
            kind="cylinder",
            axis=(axis.X(), axis.Y(), axis.Z()),
            radius=cylinder.Radius(),
        )

    if kind == GeomAbs_SurfaceType.GeomAbs_Cone:
        axis = adaptor.Cone().Axis().Direction()
        return FaceGeometry(
            kind="cone",
            axis=(axis.X(), axis.Y(), axis.Z()),
            radius=adaptor.Cone().RefRadius(),
        )

    if kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        return FaceGeometry(kind="sphere", radius=adaptor.Sphere().Radius())

    return FaceGeometry(kind="other")


def describe_edges(shape) -> list[EdgeGeometry]:
    """Every B-rep edge with its exact endpoints, length and circle parameters."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.GeomAbs import GeomAbs_CurveType
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)

    described: list[EdgeGeometry] = []
    for index in range(1, edge_map.Extent() + 1):
        edge = TopoDS.Edge_s(edge_map.FindKey(index))
        if BRep_Tool.Degenerated_s(edge):
            continue

        curve = BRepAdaptor_Curve(edge)
        start = curve.Value(curve.FirstParameter())
        end = curve.Value(curve.LastParameter())
        kind = curve.GetType()

        common = {
            "start": (start.X(), start.Y(), start.Z()),
            "end": (end.X(), end.Y(), end.Z()),
            "length": GCPnts_AbscissaPoint.Length_s(curve),
        }

        if kind == GeomAbs_CurveType.GeomAbs_Circle:
            circle = curve.Circle()
            centre = circle.Location()
            axis = circle.Axis().Direction()
            described.append(
                EdgeGeometry(
                    kind="circle",
                    centre=(centre.X(), centre.Y(), centre.Z()),
                    axis=(axis.X(), axis.Y(), axis.Z()),
                    radius=circle.Radius(),
                    **common,
                )
            )
        elif kind == GeomAbs_CurveType.GeomAbs_Line:
            described.append(EdgeGeometry(kind="line", **common))
        else:
            described.append(EdgeGeometry(kind="other", **common))

    return described


def corner_points(shape) -> list[Vec3]:
    """Exact B-rep vertices, the first thing a measurement should snap to."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    vertex_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vertex_map)

    seen: list[Vec3] = []
    for index in range(1, vertex_map.Extent() + 1):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex_map.FindKey(index)))
        candidate = (point.X(), point.Y(), point.Z())
        # A vertex shared by several faces can appear more than once with a
        # different orientation, which would put duplicate snap targets on the
        # same corner.
        if not any(
            abs(candidate[0] - existing[0]) < 1e-7
            and abs(candidate[1] - existing[1]) < 1e-7
            and abs(candidate[2] - existing[2]) < 1e-7
            for existing in seen
        ):
            seen.append(candidate)

    return seen


def choose_deflection(shape) -> float:
    """Scale tessellation deflection with model size, within configured bounds."""
    from app.pipeline import choose_deflection as clamp

    low, high = bounding_box(shape)
    diagonal = float(np.linalg.norm(np.array(high) - np.array(low)))
    return clamp(diagonal)


def convert(
    source: Path, out_glb: Path, deflection: float | None = None
) -> ConversionResult:
    """Read a B-rep file, tessellate it and write a .glb plus its metadata."""
    roots = read_parts(source)
    if not roots:
        raise ValueError(f"no shapes found in {source.name}")
    return build(roots, out_glb, deflection)


def build(
    roots: list[Part],
    out_glb: Path,
    deflection: float | None = None,
    geometry_source: GeometrySource = "brep",
    derived: DerivedGeometry | None = None,
) -> ConversionResult:
    """Tessellate shapes and write the .glb and metadata the viewer reads.

    Separate from `convert` because where the shapes came from is the reader's
    business and nothing below here has an opinion about it. A solid recovered
    from a drawing gets the same face groups, the same exact edges and the same
    snap targets as one read from STEP -- it differs only in `geometry_source`,
    which is the label saying so.
    """
    import trimesh
    from trimesh.path.entities import Line
    from trimesh.visual import TextureVisuals
    from trimesh.visual.material import PBRMaterial

    leaves: list[Part] = []

    def collect(part: Part) -> None:
        if part.children:
            for child in part.children:
                collect(child)
        else:
            leaves.append(part)

    for root in roots:
        collect(root)

    if deflection is None:
        deflection = min(choose_deflection(root.shape) for root in roots)

    scene = trimesh.Scene()
    parts: dict[str, PartMetadata] = {}
    face_groups: dict[str, list[tuple[int, int]]] = {}
    snap: dict[str, SnapGeometry] = {}
    triangle_total = 0

    for index, part in enumerate(leaves):
        vertices, faces, ranges, brep_faces = tessellate(part.shape, deflection)
        if len(faces) == 0:
            continue

        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        # A PBR material rather than vertex colours: one colour per part is the
        # shape of the data, and it keeps the glb free of a per-vertex colour
        # buffer the viewer would only overwrite on selection anyway.
        colour = part.color if part.color is not None else DEFAULT_COLOR
        mesh.visual = TextureVisuals(
            material=PBRMaterial(
                name=part.name,
                baseColorFactor=[*(int(c * 255) for c in colour), 255],
                metallicFactor=0.1,
                roughnessFactor=0.6,
            )
        )
        scene.add_geometry(mesh, geom_name=part.id, node_name=part.id)

        polylines = edge_polylines(part.shape)
        if polylines:
            offsets = np.cumsum([0, *(len(p) for p in polylines)])
            scene.add_geometry(
                trimesh.path.Path3D(
                    entities=[
                        Line(np.arange(offsets[i], offsets[i + 1]))
                        for i in range(len(polylines))
                    ],
                    vertices=np.vstack(polylines),
                ),
                geom_name=f"{part.id}__edges",
                node_name=f"{part.id}__edges",
            )

        parts[part.id] = mass_properties(part.shape)
        face_groups[part.id] = ranges
        snap[part.id] = SnapGeometry(
            vertices=corner_points(part.shape),
            edges=describe_edges(part.shape),
            faces=[describe_face(face) for face in brep_faces],
        )
        triangle_total += len(faces)
        part.mesh_index = index  # type: ignore[attr-defined]

    def to_node(part: Part) -> TreeNode:
        return TreeNode(
            id=part.id,
            name=part.name,
            children=[to_node(child) for child in part.children],
            mesh_index=leaves.index(part) if part in leaves else None,
        )

    metadata = ModelMetadata(
        geometry_source=geometry_source,
        derived=derived,
        # The name the CAD file gives the model as a whole, which exists only
        # when a single root carries a name of its own. Several roots have no
        # one name between them, and an unnamed root has none to give.
        declared_name=roots[0].name if len(roots) == 1 and roots[0].named else None,
        tree=[to_node(root) for root in roots],
        parts=parts,
        units="mm",
        face_groups=face_groups,
        snap=snap,
    )

    out_glb.parent.mkdir(parents=True, exist_ok=True)
    out_glb.write_bytes(scene.export(file_type="glb"))
    out_glb.with_suffix(".json").write_text(metadata.model_dump_json(indent=2))

    return ConversionResult(
        glb_path=str(out_glb),
        metadata=metadata,
        triangle_count=triangle_total,
        deflection=deflection,
    )
