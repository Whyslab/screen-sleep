"""
Config loading. Anything on disk is untrusted: the file can be truncated by a
crash, hand-edited into nonsense, or written by an older version. Loading must
always produce a usable config rather than raise.
"""
import json
from pathlib import Path

import common
from common import (
    MAX_MINUTES,
    MAX_TEMP,
    MIN_MINUTES,
    MIN_TEMP,
    STEPS,
    atomic_write_text,
    default_config,
    load_config,
    save_config,
)


def use_tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(common, "CONFIG_FILE", tmp_path / "config" / "config.json")
    monkeypatch.setattr(common, "STATE_DIR", tmp_path / "state")
    return tmp_path / "config" / "config.json"


def test_default_config_has_both_profiles_and_every_step():
    cfg = default_config()
    for profile in ("ac", "battery"):
        for step in STEPS:
            entry = cfg["idle"][profile][step]
            assert set(entry) == {"enabled", "minutes"}


def test_corrupt_json_falls_back_to_defaults(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")
    assert load_config() == default_config()


def test_a_truncated_file_falls_back_to_defaults(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text('{"night": {"target_temp": 27')
    assert load_config() == default_config()


def test_a_json_list_instead_of_an_object_is_survived(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]")
    assert load_config() == default_config()


def test_out_of_range_values_are_clamped_not_rejected(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "night": {"target_temp": 99999, "on_hour": 47, "off_minute": 120},
        "idle": {"ac": {"dim": {"enabled": True, "minutes": 99999}}},
    }))
    cfg = load_config()
    assert cfg["night"]["target_temp"] == MAX_TEMP
    assert 0 <= cfg["night"]["on_hour"] <= 23
    assert 0 <= cfg["night"]["off_minute"] <= 59
    assert cfg["idle"]["ac"]["dim"]["minutes"] == MAX_MINUTES


def test_a_negative_temperature_is_clamped_up(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"night": {"target_temp": -500}}))
    assert load_config()["night"]["target_temp"] == MIN_TEMP


def test_a_string_where_a_number_belongs_does_not_raise(tmp_path, monkeypatch):
    path = use_tmp_home(tmp_path, monkeypatch)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "night": {"target_temp": "warm"},
        "idle": {"battery": {"lock": {"enabled": True, "minutes": "soon"}}},
    }))
    cfg = load_config()
    assert isinstance(cfg["night"]["target_temp"], int)
    assert cfg["idle"]["battery"]["lock"]["minutes"] >= MIN_MINUTES


def test_a_saved_config_round_trips(tmp_path, monkeypatch):
    use_tmp_home(tmp_path, monkeypatch)
    cfg = default_config()
    cfg["night"]["target_temp"] = 3200
    cfg["idle"]["battery"]["suspend"] = {"enabled": False, "minutes": 42}
    save_config(cfg)
    assert load_config() == cfg


def test_atomic_write_leaves_no_temporary_files(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_text(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_replaces_content_wholesale(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_text(target, "a much longer first version\n")
    atomic_write_text(target, "short\n")
    assert target.read_text() == "short\n"


def test_xdg_config_home_is_honoured(monkeypatch, tmp_path):
    """
    The paths must follow XDG_CONFIG_HOME rather than hard-coding ~/.config.
    Without this, running the app with a scratch config — in a test, in a
    sandbox, or to take a screenshot — silently reads and rewrites the real
    user's settings.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    import importlib
    reloaded = importlib.reload(common)
    try:
        assert str(reloaded.CONFIG_FILE).startswith(str(tmp_path / "cfg"))
        assert str(reloaded.STATE_DIR).startswith(str(tmp_path / "state"))
    finally:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        importlib.reload(common)


def test_paths_fall_back_to_home_without_xdg(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    import importlib
    reloaded = importlib.reload(common)
    expected = Path.home() / ".config" / "screen-sleep" / "config.json"
    assert reloaded.CONFIG_FILE == expected


def test_an_empty_xdg_variable_is_treated_as_unset(monkeypatch):
    """An empty XDG_CONFIG_HOME must not produce a path rooted at "/"."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    import importlib
    reloaded = importlib.reload(common)
    expected = Path.home() / ".config" / "screen-sleep" / "config.json"
    try:
        assert reloaded.CONFIG_FILE == expected
    finally:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        importlib.reload(common)


def test_the_gui_css_is_pure_ascii():
    """
    The stylesheet is a bytes literal (Gtk.CssProvider.load_from_data wants
    bytes), and a bytes literal cannot hold non-ASCII. A stray em dash in a
    comment there is a syntax error that only shows up when the GUI is opened —
    which is exactly when no test is watching.
    """
    import re
    source = (Path(__file__).resolve().parent.parent / "screen_sleep" / "gui.py").read_text()
    css = re.search(r'CSS = b"""(.*?)"""', source, re.S)
    assert css, "the CSS block was not found — did it stop being a bytes literal?"
    offenders = [ch for ch in css.group(1) if ord(ch) > 127]
    assert not offenders, f"non-ASCII in the CSS bytes literal: {offenders}"
