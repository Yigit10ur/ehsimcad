"""Exporting an estimated solid as STEP, and what the file says about itself.

The reason this exists is that a .step file outlives its context. Inside the
platform an estimated model is labelled everywhere -- the catalogue marks it,
the viewer lists what the reading assumed. Downloaded, it is just a part on
somebody's disk, and the only warning it can still give is the one written
into it. So what is tested here is mostly wording: that the name carries the
marker, that the header carries the sentence, and that both survive being
written into a format that only speaks ASCII.

The parts that need OCCT are skipped without it, like the other geometry
tests. The parts that decide what the file *says* need no geometry at all, and
are tested unconditionally -- being wrong there is invisible until the file is
already in somebody else's hands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cad import drawing, occt
from app.models import DerivedGeometry

FIXTURES = Path(__file__).parent / "fixtures"


def derived(**overrides) -> DerivedGeometry:
    base = {
        "method": "dxf-revolve",
        "axis_point": (0.0, 0.0, 0.0),
        "axis_direction": (1.0, 0.0, 0.0),
        "assumptions": ["revolved the outline above the centre line"],
        "ignored": [],
    }
    return DerivedGeometry(**{**base, **overrides})


class TestAsciiOnly:
    """A name has to survive the trip into an ASCII-only header."""

    def test_folds_turkish_letters_to_their_base(self):
        # The alternative is letting the encoding decide, which is how a name
        # turns into mojibake in a supplier's CAD system.
        assert occt.ascii_only("şaft") == "saft"
        assert occt.ascii_only("ığüöçĞÜÖÇİ") == "iguocGUOCI"

    def test_folds_the_dotless_i_which_decomposes_into_nothing(self):
        # It is already a base letter, just not one ASCII has, so NFKD leaves
        # it alone and it would otherwise be dropped entirely.
        assert occt.ascii_only("mil") == "mil"
        assert occt.ascii_only("ısıtıcı") == "isitici"

    def test_leaves_plain_ascii_untouched(self):
        assert occt.ascii_only("stepped_shaft rev.2") == "stepped_shaft rev.2"

    def test_drops_what_has_no_base_letter_rather_than_guessing(self):
        assert occt.ascii_only("шафт") == ""
        assert occt.ascii_only("part 東") == "part "


class TestProductName:
    """The marker that rides in the CAD tree rather than in the file name."""

    def test_marks_the_part_as_an_estimate(self):
        assert occt.step_product_name("stepped_shaft") == "stepped_shaft_ESTIMATED"

    def test_does_not_mark_an_already_marked_name_twice(self):
        once = occt.step_product_name("shaft")
        assert occt.step_product_name(once) == once

    def test_falls_back_rather_than_producing_a_bare_marker(self):
        # A product called "_ESTIMATED" says what it is and not what it is of.
        assert occt.step_product_name("") == "part_ESTIMATED"
        assert occt.step_product_name("東") == "part_ESTIMATED"

    def test_carries_a_folded_name_through(self):
        assert occt.step_product_name("şaft") == "saft_ESTIMATED"


class TestDescription:
    """The sentence somebody reads when they have the file and nothing else."""

    def test_leads_with_what_the_thing_is(self):
        text = occt.step_description(derived(), "stepped_shaft.dxf")

        # Before the method, before the provenance: the one line that changes
        # what the reader does next.
        assert text.startswith("ESTIMATED GEOMETRY - NOT A MODELLED PART.")

    def test_says_where_it_came_from_and_how(self):
        text = occt.step_description(derived(), "stepped_shaft.dxf")

        assert "stepped_shaft.dxf" in text
        assert "dxf-revolve" in text
        assert "EhsimCAD" in text

    def test_tells_the_reader_what_to_do_about_it(self):
        # A warning that does not say what to check is decoration.
        text = occt.step_description(derived(), "shaft.dxf")
        assert "Check it against the drawing" in text

    def test_carries_the_readings_own_account_of_itself(self):
        text = occt.step_description(
            derived(
                assumptions=["revolved the outline below the centre line"],
                ignored=["2 spline entities"],
            ),
            "shaft.dxf",
        )

        # Verbatim, so the file and the viewer cannot end up disagreeing about
        # what was assumed.
        assert "revolved the outline below the centre line" in text
        assert "2 spline entities" in text

    def test_says_nothing_about_an_empty_list(self):
        text = occt.step_description(derived(assumptions=[], ignored=[]), "shaft.dxf")

        assert "Assumed:" not in text
        assert "Ignored:" not in text

    def test_is_ascii_whatever_it_was_given(self):
        # It goes into a header that has no way to carry anything else.
        text = occt.step_description(
            derived(assumptions=["eksen çizgisinin üstündeki profil"]),
            "şaft çizimi.dxf",
        )

        assert text.isascii()
        assert "saft cizimi.dxf" in text


@pytest.mark.skipif(not occt.available(), reason="OCCT bindings not installed")
class TestWrittenFile:
    """The export itself, which needs a real kernel to produce anything."""

    def test_a_drawing_conversion_writes_a_step_beside_the_glb(self, tmp_path):
        result = drawing.convert(FIXTURES / "stepped_shaft.dxf", tmp_path / "model.glb")

        assert result.step_path is not None
        written = Path(result.step_path)
        assert written.exists() and written.stat().st_size > 0

    def test_the_written_file_warns_about_itself(self, tmp_path):
        result = drawing.convert(FIXTURES / "stepped_shaft.dxf", tmp_path / "model.glb")
        text = Path(result.step_path).read_text(encoding="ascii", errors="replace")

        # The header, which travels with the file wherever it goes.
        assert "ESTIMATED GEOMETRY - NOT A MODELLED PART." in text
        assert "EhsimCAD" in text
        # And the product name, which shows in the tree of whatever opens it.
        assert "stepped_shaft_ESTIMATED" in text

    def test_the_written_file_states_millimetres(self, tmp_path):
        # A STEP file that does not say is read as whatever the receiving
        # system defaults to, which is how a 90 mm shaft becomes 90 inches.
        result = drawing.convert(FIXTURES / "stepped_shaft.dxf", tmp_path / "model.glb")
        text = Path(result.step_path).read_text(encoding="ascii", errors="replace")

        assert "MILLI" in text.upper()

    def test_a_failed_export_does_not_cost_the_model(self, tmp_path, monkeypatch):
        """The glb is what was asked for. The STEP is an extra on top of it.

        Trading a drawing that converted perfectly for a failure report,
        because a second and optional output could not be written, is the
        wrong way round. Nothing is claimed instead: no path comes back, so
        no key is recorded and no download is offered.
        """

        def explode(*_args, **_kwargs):
            raise RuntimeError("the kernel refused")

        monkeypatch.setattr(occt, "export_step", explode)

        result = drawing.convert(FIXTURES / "stepped_shaft.dxf", tmp_path / "model.glb")

        assert result.step_path is None
        assert (tmp_path / "model.glb").exists()
        assert result.metadata.geometry_source == "derived"

    def test_a_real_solid_is_not_re_exported(self, tmp_path):
        # The uploader already has this file. Handing back a copy of it would
        # dress a round trip up as the original.
        result = occt.convert(FIXTURES / "assembly.step", tmp_path / "model.glb")

        assert result.step_path is None
        assert not (tmp_path / "estimated.step").exists()
