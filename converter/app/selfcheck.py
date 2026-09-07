"""Does the toolkit in this environment still do what the code expects?

Runs at image build time, so that an environment OpenCascade cannot work in
fails the build rather than the first upload.

The reason it exists: `cadquery-ocp` was pinned by a floor alone, a new major
version appeared, and the image built cleanly with a binding the code cannot
call -- static methods lost the `_s` suffix they are called by in twenty-five
places, and `Bnd_Box.Get` stopped returning something Python can read. Nothing
noticed until a real file went through a real worker, because every earlier
check either ran against the development environment's older copy or never
touched geometry at all.

So this builds a shape and converts it, and checks numbers that are known
without measuring anything: a 40 by 20 by 5 box holds 4000 mm3 and has six flat
faces. Anything that makes those wrong makes every upload wrong.

    python -m app.selfcheck
"""

from __future__ import annotations

import sys
import tempfile
from math import pi
from pathlib import Path

BOX = (40.0, 20.0, 5.0)
CYLINDER_RADIUS = 4.0
CYLINDER_HEIGHT = 25.0


def run() -> list[str]:
    """What is wrong, or an empty list."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    from app.cad import occt

    problems: list[str] = []

    box = BRepPrimAPI_MakeBox(*BOX).Shape()
    cylinder = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), CYLINDER_RADIUS, CYLINDER_HEIGHT
    ).Shape()

    low, high = occt.bounding_box(box)
    measured = tuple(round(high[axis] - low[axis], 6) for axis in range(3))
    if measured != BOX:
        problems.append(f"a {BOX} box measures {measured}")

    parts = [
        occt.Part(id="n1", name="box", shape=box, color=None),
        occt.Part(id="n2", name="cylinder", shape=cylinder, color=None),
    ]

    with tempfile.TemporaryDirectory(prefix="selfcheck-") as workspace:
        out = Path(workspace) / "check.glb"
        result = occt.build(parts, out)

        if not out.exists() or out.stat().st_size < 1000:
            problems.append("the glb came out empty")

        metadata = result.metadata
        volume = metadata.parts["n1"].volume_mm3
        if volume is None or abs(volume - BOX[0] * BOX[1] * BOX[2]) > 1e-6:
            problems.append(f"the box measures {volume} mm3 rather than 4000")

        round_volume = metadata.parts["n2"].volume_mm3
        want = pi * CYLINDER_RADIUS**2 * CYLINDER_HEIGHT
        if round_volume is None or abs(round_volume - want) > 1e-3:
            problems.append(
                f"the cylinder measures {round_volume} mm3 rather than {want}"
            )

        kinds = sorted(f.kind for f in metadata.snap["n1"].faces)
        if kinds != ["plane"] * 6:
            problems.append(f"a box has faces {kinds} rather than six planes")

        if sorted(f.kind for f in metadata.snap["n2"].faces) != [
            "cylinder",
            "plane",
            "plane",
        ]:
            problems.append("a cylinder does not come back as a cylinder and two ends")

        groups = metadata.face_groups["n1"]
        if len(groups) != 6 or groups[0][0] != 0:
            problems.append("the face groups do not tile the box's triangles")

    return problems


def main() -> int:
    try:
        problems = run()
    except Exception as error:  # noqa: BLE001 -- the point is to report anything
        print(
            f"OpenCascade cannot be used in this environment: {error}",
            file=sys.stderr,
        )
        return 1

    for problem in problems:
        print(f"  {problem}", file=sys.stderr)

    if problems:
        print(
            "OpenCascade is installed but does not behave as expected.",
            file=sys.stderr,
        )
        return 1

    print("OpenCascade works: a box measures 4000 mm3 and has six flat faces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
