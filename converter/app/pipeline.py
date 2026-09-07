"""Format dispatch for the conversion pipeline."""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.models import ConversionResult

BREP_FORMATS = {".step", ".stp", ".iges", ".igs"}
MESH_FORMATS = {".stl", ".obj", ".ply"}
# A drawing is not a model, and what comes out of one is a reading of it. It is
# converted all the same, and labelled `derived` so that nothing downstream can
# mistake it for a part somebody actually modelled.
#
# A PDF is the same drawing after it has been printed. It keeps the geometry --
# a line is still a line, with coordinates -- and loses only the names for
# things, which is why it has a reader of its own.
DRAWING_FORMATS = {".dxf", ".pdf"}
SUPPORTED_FORMATS = BREP_FORMATS | MESH_FORMATS | DRAWING_FORMATS | {".glb", ".gltf"}


class UnsupportedFormatError(ValueError):
    """Raised for a file extension the MVP does not handle.

    Native CAD formats (.sldprt, .catpart, .prt, .ipt) land here on purpose:
    they need a commercial SDK and are out of scope. See ARCHITECTURE.md
    section 9.
    """


def choose_deflection(bbox_diagonal_mm: float) -> float:
    """Scale tessellation deflection with model size, within configured bounds.

    A fixed value is the single most common way to end up with an unusable
    output file, so deflection is always derived from the bounding box.
    """
    proposed = bbox_diagonal_mm / 1000.0
    return min(max(proposed, settings.min_deflection), settings.max_deflection)


def convert(
    source: Path, out_glb: Path, length_mm: float | None = None
) -> ConversionResult:
    """Read a file and write the .glb and metadata the viewer opens.

    `length_mm` is how long the part is along its axis, for a source that
    carries a shape without a size. Only a printed sheet is one; everything
    else states its own units, and passing a length with one would be a way to
    quietly rescale a model that was already right.
    """
    suffix = source.suffix.lower()

    if suffix not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(f"unsupported format: {suffix}")

    if suffix in BREP_FORMATS or suffix in DRAWING_FORMATS:
        from app.cad import occt

        if not occt.available():
            raise RuntimeError(
                "OCCT bindings are not installed; "
                'run: pip install -e ".[cad]" or use the Docker image'
            )
        if suffix == ".pdf":
            from app.cad import pdf

            return pdf.convert(source, out_glb, length_mm=length_mm)

        if suffix in DRAWING_FORMATS:
            from app.cad import drawing

            return drawing.convert(source, out_glb)
        # Deflection is left to the reader: it needs the bounding box, which
        # is not known until the file has been read.
        return occt.convert(source, out_glb)

    from app.cad import mesh

    return mesh.convert(source, out_glb)
