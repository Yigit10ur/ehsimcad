from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import UnsupportedFormatError, choose_deflection, convert


def test_native_cad_format_is_rejected():
    with pytest.raises(UnsupportedFormatError):
        convert(Path("part.sldprt"), Path("out.glb"))


def test_deflection_scales_with_model_size():
    small = choose_deflection(50.0)
    large = choose_deflection(5000.0)
    assert small < large


def test_deflection_stays_within_configured_bounds():
    assert choose_deflection(0.001) == settings.min_deflection
    assert choose_deflection(1_000_000.0) == settings.max_deflection


def test_derived_files_land_next_to_the_source():
    from app.storage import sibling_key

    source = "proj-1/model-2/version-3/source.step"
    assert sibling_key(source, "model.glb") == "proj-1/model-2/version-3/model.glb"
    assert (
        sibling_key(source, "metadata.json") == "proj-1/model-2/version-3/metadata.json"
    )


def test_the_self_check_agrees_that_this_environment_works():
    """The check the image build runs, run here too.

    Skipped without OCCT, like everything else that touches geometry -- which
    is exactly why it cannot be the only place this is checked, and why the
    build runs it as well.
    """
    from app.cad import occt

    if not occt.available():
        pytest.skip("OCCT bindings not installed")

    from app import selfcheck

    assert selfcheck.run() == []
