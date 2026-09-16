"""End to end conversion tests.

Skipped when OCCT is not installed, which is the case in CI: the geometry path
is covered here and by the Docker build, not by every pull request.
"""

from __future__ import annotations

import json
import struct
from math import pi
from pathlib import Path

import pytest

from app.cad import occt

pytestmark = pytest.mark.skipif(
    not occt.available(), reason="OCCT bindings not installed"
)

FIXTURE = Path(__file__).parent / "fixtures" / "assembly.step"

# The fixture is three primitives with known dimensions, so the exact values
# the B-rep should report can be written down rather than recorded from a run.
EXPECTED = {
    "Base Plate": {"volume": 40 * 20 * 5, "faces": 6},
    "Support Post": {"volume": pi * 4**2 * 25, "faces": 3},
    "Top Cap": {"volume": 12 * 12 * 3, "faces": 6},
}


@pytest.fixture(scope="module")
def converted(tmp_path_factory) -> tuple[dict, bytes]:
    out = tmp_path_factory.mktemp("out") / "assembly.glb"
    result = occt.convert(FIXTURE, out)
    metadata = json.loads(out.with_suffix(".json").read_text())
    assert result.triangle_count > 0
    return metadata, out.read_bytes()


def _names(metadata: dict) -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(node: dict) -> None:
        found[node["id"]] = node["name"]
        for child in node["children"]:
            walk(child)

    for root in metadata["tree"]:
        walk(root)
    return found


def _gltf_json(glb: bytes) -> dict:
    json_length = struct.unpack("<I", glb[12:16])[0]
    return json.loads(glb[20 : 20 + json_length])


def test_assembly_tree_keeps_names_and_structure(converted):
    metadata, _ = converted
    assert len(metadata["tree"]) == 1
    root = metadata["tree"][0]
    assert root["name"] == "Bracket Assembly"
    assert [child["name"] for child in root["children"]] == list(EXPECTED)


def test_volumes_are_exact_not_estimated_from_the_mesh(converted):
    metadata, _ = converted
    names = _names(metadata)

    for part_id, part in metadata["parts"].items():
        expected = EXPECTED[names[part_id]]["volume"]
        # A mesh derived volume would be off by whole percent at this
        # tessellation; the B-rep value is exact.
        assert part["volume_mm3"] == pytest.approx(expected, rel=1e-9)


def test_component_placement_is_applied(converted):
    metadata, _ = converted
    names = _names(metadata)
    by_name = {names[pid]: part for pid, part in metadata["parts"].items()}

    # The post sits on top of the 5 mm base plate and is 25 mm tall.
    low, high = by_name["Support Post"]["bbox"]
    assert low[2] == pytest.approx(5.0)
    assert high[2] == pytest.approx(30.0)


def test_face_groups_cover_every_brep_face(converted):
    metadata, _ = converted
    names = _names(metadata)

    for part_id, ranges in metadata["face_groups"].items():
        assert len(ranges) == EXPECTED[names[part_id]]["faces"]
        # Ranges must tile the triangle list without gaps or overlaps,
        # otherwise face level picking would land on the wrong face.
        assert ranges[0][0] == 0
        for (_, end), (start, _) in zip(ranges, ranges[1:], strict=False):
            assert end == start


def test_edges_are_exported_as_line_primitives(converted):
    _, glb = converted
    gltf = _gltf_json(glb)

    names = [node.get("name") for node in gltf["nodes"]]
    assert sum(name.endswith("__edges") for name in names) == len(EXPECTED)

    modes = {
        primitive.get("mode", 4)
        for mesh in gltf["meshes"]
        for primitive in mesh["primitives"]
    }
    assert modes == {1, 4}  # LINES and TRIANGLES


def test_part_colours_survive_the_round_trip(converted):
    _, glb = converted
    gltf = _gltf_json(glb)
    materials = {
        material.get("name"): material["pbrMetallicRoughness"]["baseColorFactor"]
        for material in gltf.get("materials", [])
    }

    assert materials["Support Post"][:3] == pytest.approx([0.80, 0.45, 0.20], abs=0.01)


def test_snap_geometry_matches_the_solids(converted):
    metadata, _ = converted
    names = _names(metadata)
    by_name = {names[pid]: snap for pid, snap in metadata["snap"].items()}

    plate = by_name["Base Plate"]
    assert len(plate["vertices"]) == 8
    assert len(plate["edges"]) == 12
    assert {edge["kind"] for edge in plate["edges"]} == {"line"}
    assert {face["kind"] for face in plate["faces"]} == {"plane"}

    post = by_name["Support Post"]
    circles = [edge for edge in post["edges"] if edge["kind"] == "circle"]
    assert len(circles) == 2


def test_circular_edges_carry_the_cad_radius(converted):
    metadata, _ = converted
    names = _names(metadata)
    post_id = next(pid for pid, name in names.items() if name == "Support Post")

    for edge in metadata["snap"][post_id]["edges"]:
        if edge["kind"] == "circle":
            # A diameter read off the tessellated polyline would be short by
            # the chord error; this one comes from the circle definition.
            assert edge["radius"] == pytest.approx(4.0, rel=1e-9)


def test_plane_normals_point_out_of_the_solid(converted):
    metadata, _ = converted
    names = _names(metadata)
    plate_id = next(pid for pid, name in names.items() if name == "Base Plate")

    normals = [
        face["normal"] for face in metadata["snap"][plate_id]["faces"] if face["normal"]
    ]
    # A box has one face per axis direction; a reversed face would show up as a
    # duplicate normal and a missing opposite.
    assert len(normals) == 6
    for axis in range(3):
        assert any(normal[axis] == pytest.approx(1.0) for normal in normals)
        assert any(normal[axis] == pytest.approx(-1.0) for normal in normals)


def test_face_geometry_is_aligned_with_face_groups(converted):
    metadata, _ = converted

    for part_id, ranges in metadata["face_groups"].items():
        # The two lists are indexed the same way; if they ever drift, picking a
        # face would report another face's type and radius.
        assert len(metadata["snap"][part_id]["faces"]) == len(ranges)


def test_declares_the_name_the_file_carries(tmp_path: Path) -> None:
    """The model's own name, for the catalogue to use instead of a file name.

    A file name is whatever the operating system was holding; one upload here
    arrived with its Turkish characters already stripped. The name inside the
    file is written by the modeller and correctly encoded, so it is the better
    thing to show -- but only when the file actually carries one.
    """
    result = occt.convert(FIXTURE, tmp_path / "model.glb")

    assert result.metadata.declared_name == "Bracket Assembly"


def test_reports_what_an_unnamed_file_declares_without_judging_it(
    tmp_path: Path,
) -> None:
    """A STEP file with no product name still declares something.

    Whatever wrote the file fills the gap -- OCCT's own writer leaves its
    version string. The converter reports that faithfully; deciding whether a
    declared name is worth showing belongs to the worker, which can compare it
    against the name the file was uploaded under.
    """
    import re

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    # STEPControl_Writer, unlike the XCAF writer, records no product names.
    source = tmp_path / "unnamed.step"
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape(), STEPControl_AsIs)
    writer.Write(str(source))

    # Read out of the file rather than written down here. The version string
    # ends in a counter the OCCT session keeps across every STEP written in
    # the process, so the exact name depends on what ran before this. What the
    # converter promises is not a particular string -- it is that whatever the
    # file declares is what comes back.
    declared = re.search(r"PRODUCT\('([^']*)'", source.read_text())
    assert declared is not None

    result = occt.convert(source, tmp_path / "unnamed.glb")

    assert result.metadata.tree[0].name
    assert result.metadata.declared_name == declared.group(1)
    assert declared.group(1).startswith("Open CASCADE STEP translator")
