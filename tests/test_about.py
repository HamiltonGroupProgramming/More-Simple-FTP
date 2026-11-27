import os

import pytest

about = pytest.importorskip("about")  # needs tkinter
looks = pytest.importorskip("looks")


@pytest.mark.parametrize("name", ["icon.png", "icon.ico", "hgp_logo.png", "hgp_logo_reversed.png"])
def test_assets_are_shipped(name):
    assert os.path.isfile(about.asset_path(name))


def test_credit_matches_other_apps():
    assert about.CREDIT == "Built by Hamilton Group Programming with the help of Claude"


def test_dark_looks_get_the_reversed_logo():
    assert about.is_dark(looks.get_look("midnight"))
    assert not about.is_dark(looks.get_look("crayon"))
