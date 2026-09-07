"""Generate every fixture the converter tests read.

Written rather than checked in by hand so that what each file contains is
stated in code: a test asserting a volume of 4000 mm3 means nothing unless the
box it came from is visibly 40 x 20 x 5 somewhere.

The STEP is produced with the XCAF writer rather than the plain one so it
carries part names, colours and a real assembly structure -- otherwise the
reader path being tested would never see the interesting cases.

    python scripts/make_fixture.py
"""

from __future__ import annotations

from pathlib import Path

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
STEP_FIXTURE = FIXTURES / "assembly.step"
IGES_FIXTURE = FIXTURES / "two_solids.igs"
BOX_FIXTURE = FIXTURES / "box.stl"
OPEN_SURFACE_FIXTURE = FIXTURES / "open_surface.stl"
SHAFT_FIXTURE = FIXTURES / "stepped_shaft.dxf"
PLAIN_SHAFT_FIXTURE = FIXTURES / "plain_shaft.dxf"
PRINTED_FIXTURE = FIXTURES / "stepped_shaft_printed.pdf"
PLOTTED_FIXTURE = FIXTURES / "stepped_shaft_plotted.pdf"
FILLETED_FIXTURE = FIXTURES / "filleted_shaft_plotted.pdf"
SCAN_FIXTURE = FIXTURES / "stepped_shaft_scan.png"
SCAN_JPEG_FIXTURE = FIXTURES / "stepped_shaft_scan.jpg"


def _translation(x: float, y: float, z: float) -> TopLoc_Location:
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(trsf)


def write_step() -> None:
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    base = BRepPrimAPI_MakeBox(40.0, 20.0, 5.0).Shape()
    post = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 4.0, 25.0
    ).Shape()
    cap = BRepPrimAPI_MakeBox(12.0, 12.0, 3.0).Shape()

    parts = [
        ("Base Plate", base, (0.55, 0.57, 0.60), _translation(0, 0, 0)),
        ("Support Post", post, (0.80, 0.45, 0.20), _translation(20, 10, 5)),
        ("Top Cap", cap, (0.20, 0.45, 0.80), _translation(14, 4, 30)),
    ]

    assembly = shape_tool.NewShape()
    TDataStd_Name.Set_s(assembly, TCollection_ExtendedString("Bracket Assembly"))

    for name, shape, rgb, location in parts:
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        color_tool.SetColor(
            label, Quantity_Color(*rgb, Quantity_TOC_RGB), XCAFDoc_ColorSurf
        )
        shape_tool.AddComponent(assembly, label, location)

    shape_tool.UpdateAssemblies()

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc)
    STEP_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    writer.Write(str(STEP_FIXTURE))

    _report(STEP_FIXTURE)


def write_iges() -> None:
    """A box and a cylinder in one IGES file.

    Two separate solids on purpose. IGES has no product structure, so the
    reader has no way to keep them apart -- the test that asserts they arrive
    as a single unnamed part is the point of this fixture, not an accident of
    how it was written.
    """
    from OCP.BRep import BRep_Builder
    from OCP.IGESControl import IGESControl_Controller, IGESControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    IGESControl_Controller.Init_s()

    # BRepMode 1 writes solids as manifold_solid_brep. The default writes
    # surfaces instead, and a surface encloses no volume to measure.
    writer = IGESControl_Writer("MM", 1)

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(40.0, 20.0, 5.0).Shape())
    builder.Add(
        compound,
        BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(20, 10, 5), gp_Dir(0, 0, 1)), 4.0, 25.0
        ).Shape(),
    )

    writer.AddShape(compound)
    writer.ComputeModel()
    writer.Write(str(IGES_FIXTURE))

    _report(IGES_FIXTURE)


def write_meshes() -> None:
    """One watertight mesh and one open surface.

    The open surface exists to pin down the case where a volume cannot be
    measured at all, which is the honest answer rather than a number.
    """
    import numpy as np
    import trimesh

    box = trimesh.creation.box(extents=(40.0, 20.0, 5.0))
    box.apply_translation((20.0, 10.0, 2.5))
    box.export(BOX_FIXTURE)
    _report(BOX_FIXTURE)

    trimesh.Trimesh(
        vertices=np.array(
            [[0, 0, 0], [40, 0, 0], [40, 20, 0], [0, 20, 0]], dtype=float
        ),
        faces=np.array([[0, 1, 2], [0, 2, 3]]),
    ).export(OPEN_SURFACE_FIXTURE)
    _report(OPEN_SURFACE_FIXTURE)


def write_drawings() -> None:
    """Two turned parts, drawn the way a drawing office draws them.

    Not a tidy list of profile edges: the outline is mirrored about the centre
    line, the bore is dashed, and there are dimensions, notes and a title block
    on top. All of that is what the reader has to see past, so leaving it out
    would make the tests agree with an easier problem than the real one.

    Both are drawn full size in millimetres, and both have a volume that can be
    worked out by hand -- which is what the tests check.
    """
    import ezdxf

    def new_document():
        # setup=True brings in the standard linetypes, CENTER and HIDDEN among
        # them. They are how a drawing says "this is an axis" and "this edge is
        # behind the material", and both are load bearing here.
        doc = ezdxf.new("R2010", setup=True)
        doc.header["$INSUNITS"] = 4  # millimetres
        doc.layers.add("OUTLINE")
        doc.layers.add("CENTER", linetype="CENTER")
        doc.layers.add("HIDDEN", linetype="HIDDEN")
        doc.layers.add("DIMENSIONS")
        doc.layers.add("TITLE")
        return doc

    def outline(msp, points: list[tuple[float, float]]) -> None:
        """The chain, and its mirror image below the axis."""
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "OUTLINE"})
            msp.add_line((x1, -y1), (x2, -y2), dxfattribs={"layer": "OUTLINE"})

    def clutter(msp, length: float, height: float, note: str) -> None:
        """Everything on the sheet that is not the part."""
        msp.add_linear_dim(
            base=(0, height + 12), p1=(0, height), p2=(length, height)
        ).render()
        msp.add_text(
            note, dxfattribs={"layer": "DIMENSIONS", "height": 3}
        ).set_placement((2, height + 18))
        # A title block, well away from the part, on its own layer.
        for a, b in [
            ((-20, -60), (length + 20, -60)),
            ((length + 20, -60), (length + 20, -40)),
            ((length + 20, -40), (-20, -40)),
            ((-20, -40), (-20, -60)),
        ]:
            msp.add_line(a, b, dxfattribs={"layer": "TITLE"})

    # A stepped shaft with a through bore. Hollow, so the profile is closed by
    # the bore rather than by the axis: 10 for 30, then 15 for 40, then 8 for
    # 20, bored 4 the whole way.
    #   pi * (10^2*30 + 15^2*40 + 8^2*20) - pi * 4^2*90 = pi * 11840 mm3
    doc = new_document()
    msp = doc.modelspace()
    outline(
        msp,
        [(0, 4), (0, 10), (30, 10), (30, 15), (70, 15), (70, 8), (90, 8), (90, 4)],
    )
    for y in (4, -4):
        msp.add_line((0, y), (90, y), dxfattribs={"layer": "HIDDEN"})
    msp.add_line((-8, 0), (98, 0), dxfattribs={"layer": "CENTER"})
    clutter(msp, 90, 15, "MATERIAL: C45")
    doc.saveas(SHAFT_FIXTURE)
    _report(SHAFT_FIXTURE)

    # A plain cylinder, 10 radius over 50. Solid, so the end faces are drawn
    # straight across the centre line and the profile is closed by the axis
    # itself -- the other of the two cases, and the reason there are two files.
    #   pi * 10^2 * 50 = pi * 5000 mm3
    doc = new_document()
    msp = doc.modelspace()
    for a, b in [
        ((0, -10), (0, 10)),
        ((0, 10), (50, 10)),
        ((50, 10), (50, -10)),
        ((50, -10), (0, -10)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "OUTLINE"})
    msp.add_line((-8, 0), (58, 0), dxfattribs={"layer": "CENTER"})
    clutter(msp, 50, 10, "MATERIAL: 304")
    doc.saveas(PLAIN_SHAFT_FIXTURE)
    _report(PLAIN_SHAFT_FIXTURE)


def write_printed_sheets() -> None:
    """The same shaft as two PDFs, drawn the two ways a PDF can be dashed.

    A dashed stroke reaches a PDF either as a pattern set on a whole line or as
    a row of short lines already broken up, and which one depends on what wrote
    the file rather than on what the drawing means. The centre line is found
    from that pattern, so a reader tested against only one of them is tested
    against half the problem.

    `_printed` is the DXF put through a renderer, which breaks the dashes up.
    `_plotted` is the same geometry drawn with real dash patterns. Both are
    the same part, and the tests hold both to the same volume as the DXF.
    """
    import matplotlib

    matplotlib.use("Agg")
    import ezdxf
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.patches import Arc

    doc = ezdxf.readfile(SHAFT_FIXTURE)
    figure = plt.figure(figsize=(11.69, 8.27))  # A4, landscape
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_axis_off()
    Frontend(RenderContext(doc), MatplotlibBackend(axes)).draw_layout(
        doc.modelspace(), finalize=True
    )
    figure.savefig(PRINTED_FIXTURE, format="pdf")
    plt.close(figure)
    _report(PRINTED_FIXTURE)

    # The same outline, drawn as strokes carrying their own dash patterns.
    outline = [(0, 4), (0, 10), (30, 10), (30, 15), (70, 15), (70, 8), (90, 8), (90, 4)]
    figure = plt.figure(figsize=(11.69, 8.27))
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_axis_off()
    # Equal, or the sheet carries a stretched part: a drawing plotted with a
    # different scale across than up is a different shaft, and nothing in the
    # geometry says it was not meant.
    axes.set_aspect("equal")
    axes.set_xlim(-30, 120)
    axes.set_ylim(-55, 55)

    for sign in (1, -1):
        xs = [x for x, _ in outline]
        ys = [sign * y for _, y in outline]
        axes.plot(xs, ys, linestyle="-", color="k", lw=0.7)
        # The bore, dashed: one mark length repeating, which is what makes it
        # an edge behind material rather than an axis.
        axes.plot(
            [0, 90], [sign * 4, sign * 4], linestyle=(0, (4, 2)), color="k", lw=0.7
        )

    # The axis: two mark lengths in turn, long and short.
    axes.plot([-8, 98], [0, 0], linestyle=(0, (8, 2, 1, 2)), color="k", lw=0.7)
    axes.text(2, 20, "MATERIAL: C45", fontsize=8)

    figure.savefig(PLOTTED_FIXTURE, format="pdf")
    plt.close(figure)
    _report(PLOTTED_FIXTURE)

    # A shaft with a radius on one corner, which is the case a PDF is worst at
    # and the reader has to be good at: an arc leaves the CAD application as an
    # arc, reaches the PDF as cubics because that is all a PDF draws, and has
    # to be fitted back. A row of chords would close and revolve just the same,
    # and would not be a fillet.
    figure = plt.figure(figsize=(8, 6))
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_axis_off()
    axes.set_aspect("equal")
    axes.set_xlim(-15, 65)
    axes.set_ylim(-30, 30)

    for sign in (1, -1):
        axes.plot([0, 0], [0, sign * 10], color="k", lw=0.7)
        axes.plot([0, 45], [sign * 10, sign * 10], color="k", lw=0.7)
        axes.add_patch(
            Arc(
                (45, sign * 5),
                10,
                10,
                theta1=0 if sign > 0 else -90,
                theta2=90 if sign > 0 else 0,
                lw=0.7,
                color="k",
            )
        )
        axes.plot([50, 50], [sign * 5, 0], color="k", lw=0.7)

    axes.plot([-8, 58], [0, 0], linestyle=(0, (8, 2, 1, 2)), color="k", lw=0.7)

    figure.savefig(FILLETED_FIXTURE, format="pdf")
    plt.close(figure)
    _report(FILLETED_FIXTURE)


def write_scans() -> None:
    """The same shaft as an image, twice.

    Line weight is the whole of what tells the part from the annotation here,
    so it is drawn the way the standard prescribes: the outline twice the
    weight of everything else. Dimensions, an arrow, a note and a title block
    are on the sheet for the same reason they are on the DXF -- a reader tested
    against a bare outline is tested against an easier drawing than exists.

    The JPEG is the one that matters: it is what a drawing arrives as, and its
    compression puts a halo around every line that the PNG does not have.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    outline = [(0, 4), (0, 10), (30, 10), (30, 15), (70, 15), (70, 8), (90, 8), (90, 4)]

    figure = plt.figure(figsize=(11.69, 8.27))
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_axis_off()
    axes.set_aspect("equal")
    axes.set_xlim(-30, 120)
    axes.set_ylim(-55, 55)

    for sign in (1, -1):
        axes.plot(
            [x for x, _ in outline],
            [sign * y for _, y in outline],
            color="k",
            lw=1.4,  # the outline: twice the weight of everything else
        )
        axes.plot(
            [0, 90], [sign * 4, sign * 4], linestyle=(0, (4, 2)), color="k", lw=0.7
        )

    axes.plot([-8, 98], [0, 0], linestyle=(0, (8, 2, 1, 2)), color="k", lw=0.7)

    axes.annotate(
        "",
        xy=(0, 22),
        xytext=(90, 22),
        arrowprops={"arrowstyle": "<->", "lw": 0.7, "color": "k"},
    )
    axes.plot([0, 0], [10, 23], color="k", lw=0.7)
    axes.plot([90, 90], [8, 23], color="k", lw=0.7)
    axes.text(40, 24, "90", fontsize=9)
    axes.text(2, 30, "MATERIAL: C45", fontsize=8)

    for a, b in [
        ((-20, -45), (110, -45)),
        ((110, -45), (110, -30)),
        ((110, -30), (-20, -30)),
        ((-20, -30), (-20, -45)),
    ]:
        axes.plot([a[0], b[0]], [a[1], b[1]], color="k", lw=0.7)

    figure.savefig(SCAN_FIXTURE, dpi=200)
    plt.close(figure)
    _report(SCAN_FIXTURE)

    Image.open(SCAN_FIXTURE).convert("L").save(SCAN_JPEG_FIXTURE, quality=80)
    _report(SCAN_JPEG_FIXTURE)


def _report(path: Path) -> None:
    print(f"wrote {path.name} ({path.stat().st_size} bytes)")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    write_step()
    write_iges()
    write_meshes()
    write_drawings()
    write_printed_sheets()
    write_scans()


if __name__ == "__main__":
    main()
