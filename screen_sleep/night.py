#!/usr/bin/env python3
"""
The night filter: working out what colour temperature the screen should be
right now, and applying it through hyprsunset (via hyprctl).
"""

import subprocess

from common import (
    MAX_TEMP,
    RAMP_BOTH_DIRECTIONS,
    RAMP_INTERVAL_MIN,
    RAMP_STEP,
    SERVICE_NAME,
)

HYPRCTL = "hyprctl"

# Tools print numbers according to the locale; parsing needs a predictable one.
ENV_C = {"LC_ALL": "C"}


def _run(cmd: list, timeout: int = 2) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env.update(ENV_C)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def apply_temperature(kelvin) -> tuple[bool, str]:
    """Apply a temperature immediately via hyprctl. Returns (ok, message)."""
    try:
        result = _run([HYPRCTL, "hyprsunset", "temperature", str(int(kelvin))])
        if result.returncode != 0:
            return False, "hyprsunset is not responding (is the hyprsunset daemon running?)"
        return True, ""
    except FileNotFoundError:
        return False, "hyprctl not found — is this a Hyprland session?"
    except subprocess.TimeoutExpired:
        return False, "hyprctl did not answer in time"


def read_temperature() -> int | None:
    """The temperature currently on screen. None if hyprsunset does not answer."""
    try:
        result = _run([HYPRCTL, "hyprsunset", "temperature"])
        if result.returncode != 0:
            return None
        return int(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def check_hyprsunset_alive() -> tuple[bool, str]:
    try:
        result = _run([HYPRCTL, "hyprsunset", "profile"])
        if result.returncode != 0:
            return False, "hyprsunset is not running. Add to hyprland.conf: exec-once = hyprsunset"
        return True, ""
    except FileNotFoundError:
        return False, "hyprctl not found — is this a Hyprland session?"
    except subprocess.TimeoutExpired:
        return False, "hyprctl did not answer in time"


def check_daemon_alive() -> tuple[bool, str]:
    try:
        result = _run(["systemctl", "--user", "is-active", SERVICE_NAME])
        if result.stdout.strip() != "active":
            return False, ("The screen-sleep daemon is not running — the schedule "
                           "and AC/battery switching will not work")
        return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "Could not check the daemon status"


# ---------------------------------------------------------------------------
# The gradual transition
# ---------------------------------------------------------------------------

def ramp_duration_minutes(target_temp: int) -> float:
    """How many minutes a MAX_TEMP → target_temp transition takes at RAMP_STEP."""
    if target_temp >= MAX_TEMP:
        return 0.0
    steps_needed = (MAX_TEMP - target_temp) / RAMP_STEP
    return steps_needed * RAMP_INTERVAL_MIN


def compute_target_temperature(now_minutes: float, night: dict) -> int:
    """
    What the temperature should be RIGHT NOW, given the current time
    (now_minutes — minutes since midnight, may be fractional) and the schedule.
    Handles a window that crosses midnight correctly (e.g. on=22:00, off=07:00).
    """
    on_t = night["on_hour"] * 60 + night["on_minute"]
    off_t = night["off_hour"] * 60 + night["off_minute"]
    ramp_min = ramp_duration_minutes(night["target_temp"])

    # "how many minutes ago this boundary was last crossed" (modulo a day)
    since_on = (now_minutes - on_t) % 1440
    since_off = (now_minutes - off_t) % 1440

    if since_on < ramp_min:
        # ramp down from MAX_TEMP towards target_temp
        steps = int(since_on // RAMP_INTERVAL_MIN)
        return int(max(MAX_TEMP - steps * RAMP_STEP, night["target_temp"]))

    if RAMP_BOTH_DIRECTIONS and since_off < ramp_min:
        steps = int(since_off // RAMP_INTERVAL_MIN)
        return int(min(night["target_temp"] + steps * RAMP_STEP, MAX_TEMP))

    if since_on < since_off:
        # the "on" boundary was crossed more recently than "off" — so it is night
        return int(night["target_temp"])

    return int(MAX_TEMP)
