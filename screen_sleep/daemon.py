#!/usr/bin/env python3
"""
The Screen & Sleep background daemon. Every CHECK_INTERVAL_SECONDS it does two
things.

1. The night filter: looks at the clock, works out what temperature the screen
   should be right now (a gradual warm-up in the evening, an instant reset in
   the morning), and applies it through hyprctl if it changed. While the config
   carries manual_override — the user moved the slider — the daemon leaves the
   temperature alone; the flag clears itself at the next schedule boundary.

2. Power: compares whether the charger is actually plugged in against the
   profile currently written into hypridle.conf, and on a mismatch regenerates
   the config and restarts hypridle.

Runs under systemd --user.
"""

import contextlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import idle
from common import CHECK_INTERVAL_SECONDS, PROFILE_LABELS, load_config, save_config
from night import apply_temperature, check_hyprsunset_alive, compute_target_temperature


def _wait_for_hyprsunset(timeout: float = 30.0) -> None:
    """
    The daemon and hyprsunset start at almost the same moment, and we usually
    win the race. Wait for it, or the first apply fails for no real reason.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive, _ = check_hyprsunset_alive()
        if alive:
            return
        time.sleep(1)
    print("screen-sleep: hyprsunset never answered, carrying on without it")


def _minutes_since_midnight(now: datetime) -> float:
    return now.hour * 60 + now.minute + now.second / 60


def _notify(title: str, body: str) -> None:
    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(["notify-send", "-a", "Screen & Sleep", title, body],
                       capture_output=True, timeout=3)


def _describe(profile: dict) -> str:
    parts = []
    for step, label in (("lock", "lock"), ("suspend", "suspend")):
        entry = profile[step]
        parts.append(f"{label} {entry['minutes']} min" if entry["enabled"] else f"{label} off")
    return ", ".join(parts)


def _sync_power(cfg: dict, force: bool) -> None:
    power = idle.current_power()
    if not force and power == idle.applied_power():
        return

    ok, message = idle.apply_profile(cfg, power)
    if not ok:
        print(f"screen-sleep: could not apply the \"{PROFILE_LABELS[power]}\" profile — {message}")
        return

    print(f"screen-sleep: applied the \"{PROFILE_LABELS[power]}\" profile")
    if not force:
        _notify(f"Power: {PROFILE_LABELS[power].lower()}",
                f"Idle timings — {_describe(cfg['idle'][power])}")


def main():
    # Under systemd stdout is a pipe, not a terminal, and Python block-buffers
    # such output by default — print() can fail to reach journalctl for hours.
    # Force line buffering so the logs show up immediately.
    sys.stdout.reconfigure(line_buffering=True)

    last_applied_temp = None
    prev_since_on = None
    prev_since_off = None
    first_pass = True

    print("screen-sleep: starting")
    _wait_for_hyprsunset()

    while True:
        cfg = load_config()

        # ---- 1. night filter ----
        now_minutes = _minutes_since_midnight(datetime.now())
        night = cfg["night"]

        on_t = night["on_hour"] * 60 + night["on_minute"]
        off_t = night["off_hour"] * 60 + night["off_minute"]
        since_on = (now_minutes - on_t) % 1440
        since_off = (now_minutes - off_t) % 1440

        crossed_boundary = (
            (prev_since_on is not None and since_on < prev_since_on) or
            (prev_since_off is not None and since_off < prev_since_off)
        )

        if crossed_boundary and night.get("manual_override"):
            cfg["night"]["manual_override"] = False
            save_config(cfg)
            print("screen-sleep: schedule boundary crossed, clearing manual_override")

        if not cfg["night"].get("manual_override", False):
            target = compute_target_temperature(now_minutes, cfg["night"])
            if target != last_applied_temp:
                ok, message = apply_temperature(target)
                if ok:
                    print(f"screen-sleep: applied {target}K")
                    last_applied_temp = target
                else:
                    print(f"screen-sleep: failed to apply {target}K — {message}")

        prev_since_on, prev_since_off = since_on, since_off

        # ---- 2. AC or battery ----
        # Apply unconditionally on the first pass: after login the config must
        # match the actual power state, even if nothing has changed since.
        try:
            _sync_power(cfg, force=first_pass)
        except OSError as error:
            print(f"screen-sleep: power sync failed — {error}")
        first_pass = False

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
