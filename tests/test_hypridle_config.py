"""
Generating hypridle.conf.

This code edits a file the user also edits by hand, which is the whole risk:
it must replace only its own managed block and leave everything else — the
header, the general block, hand-written listeners outside the markers —
exactly as it found it.
"""
import idle
from idle import MARK_END, MARK_START, _strip_legacy, build_managed_block, render


def profile(**minutes):
    """A profile where the named steps are on and the rest are off."""
    return {
        step: {"enabled": step in minutes, "minutes": minutes.get(step, 60)}
        for step in ("dim", "lock", "dpms", "suspend")
    }


HAND_WRITTEN = """\
# My own hypridle config, please do not eat it.

general {
    lock_cmd = pidof hyprlock || hyprlock
    before_sleep_cmd = loginctl lock-session
}
"""


def test_the_block_is_delimited_by_both_markers():
    text = build_managed_block(profile(lock=10), "ac")
    assert text.startswith(MARK_START)
    assert text.rstrip().endswith(MARK_END)


def test_only_enabled_steps_produce_listeners():
    text = build_managed_block(profile(lock=10, suspend=30), "ac")
    assert "$lock_timeout" in text
    assert "$suspend_timeout" in text
    assert "$dim_timeout" not in text
    assert "$dpms_timeout" not in text
    assert text.count("listener {") == 2


def test_minutes_become_seconds():
    text = build_managed_block(profile(lock=7), "battery")
    assert "$lock_timeout = 420" in text


def test_a_profile_with_nothing_enabled_is_still_a_valid_block():
    text = build_managed_block(profile(), "ac")
    assert "listener {" not in text
    assert MARK_START in text and MARK_END in text


def test_the_profile_in_use_is_written_into_the_block():
    assert "on battery" in build_managed_block(profile(lock=5), "battery")
    assert "on AC power" in build_managed_block(profile(lock=5), "ac")


def test_hand_written_content_survives_a_render():
    out = render(HAND_WRITTEN, profile(lock=10), "ac")
    assert "please do not eat it" in out
    assert "lock_cmd = pidof hyprlock || hyprlock" in out
    assert "before_sleep_cmd" in out


def test_rendering_twice_is_idempotent():
    once = render(HAND_WRITTEN, profile(lock=10), "ac")
    twice = render(once, profile(lock=10), "ac")
    assert once == twice


def test_a_second_render_replaces_the_block_rather_than_appending():
    once = render(HAND_WRITTEN, profile(lock=10), "ac")
    twice = render(once, profile(lock=25), "ac")
    assert twice.count(MARK_START) == 1
    assert twice.count(MARK_END) == 1
    assert "$lock_timeout = 1500" in twice
    assert "$lock_timeout = 600" not in twice


def test_switching_profiles_does_not_accumulate_listeners():
    text = render(HAND_WRITTEN, profile(dim=1, lock=2, dpms=3, suspend=4), "ac")
    text = render(text, profile(lock=70), "battery")
    assert text.count("listener {") == 1
    assert "on battery" in text


def test_content_after_the_block_is_kept():
    base = render(HAND_WRITTEN, profile(lock=10), "ac")
    base += "\n# a note added below the block\n"
    out = render(base, profile(lock=20), "ac")
    assert "a note added below the block" in out


def test_legacy_listeners_are_stripped_on_first_adoption():
    legacy = HAND_WRITTEN + """
$lock_timeout = 300

# 1. Lock the screen
listener {
    timeout = $lock_timeout
    on-timeout = loginctl lock-session
}
"""
    out = render(legacy, profile(lock=10), "ac")
    # The old listener is gone, replaced by exactly one managed one.
    assert out.count("listener {") == 1
    assert "$lock_timeout = 600" in out
    assert "$lock_timeout = 300" not in out
    assert "please do not eat it" in out


def test_strip_legacy_leaves_the_general_block_intact():
    out = _strip_legacy(HAND_WRITTEN)
    assert "general {" in out
    assert "lock_cmd" in out


def test_read_current_timeouts_returns_minutes(tmp_path, monkeypatch):
    conf = tmp_path / "hypridle.conf"
    conf.write_text("$lock_timeout = 600\n$suspend_timeout = 7200\n")
    monkeypatch.setattr(idle, "HYPRIDLE_CONF", conf)
    assert idle.read_current_timeouts() == {"lock": 10, "suspend": 120}


def test_read_current_timeouts_on_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(idle, "HYPRIDLE_CONF", tmp_path / "nope.conf")
    assert idle.read_current_timeouts() == {}
