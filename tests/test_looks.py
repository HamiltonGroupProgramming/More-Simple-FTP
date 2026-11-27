import os

import pytest

from appsettings import load_settings, save_settings

looks = pytest.importorskip("looks")  # needs tkinter

COLOR_KEYS = {"bg", "panel", "ink", "muted", "field", "border", "select_bg", "select_fg",
              "button", "button_fg", "button_active", "primary", "primary_fg", "primary_active",
              "secondary", "secondary_fg", "secondary_active", "danger", "danger_fg",
              "danger_active", "progress", "trough", "local", "remote", "title", "accent2",
              "ok", "heading"}


def test_all_four_looks_and_classic_exist():
    assert {"crayon", "modern", "midnight", "big", "classic"} <= set(looks.LOOKS)
    assert looks.DEFAULT_LOOK == "crayon"


@pytest.mark.parametrize("key", [k for k, v in looks.LOOKS.items() if v.colors])
def test_looks_define_every_colour(key):
    missing = COLOR_KEYS - set(looks.LOOKS[key].colors)
    assert not missing


def test_labels_fall_back_to_defaults():
    crayon = looks.get_look("crayon")
    assert crayon.label("upload") == "Send it →"
    assert crayon.label("rename") == "Rename"
    assert looks.get_look("nope").key == looks.DEFAULT_LOOK
    assert looks.get_look(None).key == looks.DEFAULT_LOOK
    for look in looks.LOOKS.values():
        assert look.actions in ("middle", "top", "bottom")
        for key in looks.DEFAULT_LABELS:
            look.label(key)


def test_settings_round_trip(tmp_path):
    path = str(tmp_path / "sub" / "settings.json")
    assert load_settings(path) == {}
    save_settings({"look": "midnight"}, path)
    assert load_settings(path) == {"look": "midnight"}


def test_broken_settings_file_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load_settings(str(path)) == {}
    path.write_text("[1, 2]")
    assert load_settings(str(path)) == {}
    assert not os.path.exists(str(path) + ".tmp")
