#!/usr/bin/env python3
"""
Idle, lock and suspend: generating ~/.config/hypr/hypridle.conf from the config,
restarting hypridle, the stay-awake mode, and detecting the power source.

hypridle cannot reload its config and knows nothing about batteries, hence:
  * the timings live in a managed region between two markers — everything else
    in the file (the header, the general block) stays exactly as written;
  * after a write the process is restarted with pkill + hyprctl dispatch exec,
    because hypridle is normally started from exec-once in hyprland.conf rather
    than through systemd, where graphical-session.target may not be active;
  * which profile applies is decided by the daemon from the actual power state.
"""

import contextlib
import os
import re
import signal
import subprocess
import time
from datetime import date
from pathlib import Path

from common import STEPS, atomic_write_text, state_path

HYPRIDLE_CONF = Path.home() / ".config" / "hypr" / "hypridle.conf"

MARK_START = "# >>> screen-sleep: this block is managed by the Screen & Sleep window"
MARK_END = "# <<< screen-sleep"

AC_ONLINE = Path("/sys/class/power_supply/AC/online")
CAFFEINE_PID_FILE = "caffeine.pid"
APPLIED_POWER_FILE = "applied_power"

# The commands are deliberately the stock hypridle ones — only the timings and
# whether a step exists at all are changed.
STEP_SPECS = {
    "dim": {
        "var": "dim_timeout",
        "note": "dim the backlight",
        "comment": "Dim early, as a gentle warning that the lock is coming",
        "on_timeout": "brightnessctl -s set 10%",
        "on_resume": "brightnessctl -r",
    },
    "lock": {
        "var": "lock_timeout",
        "note": "lock the screen (starts hyprlock)",
        "comment": "Lock the screen",
        "on_timeout": "loginctl lock-session",
        "on_resume": None,
    },
    "dpms": {
        "var": "dpms_timeout",
        "note": "switch the monitor signal off (DPMS off)",
        "comment": "Turn the monitor off rather than burn the panel for nothing",
        "on_timeout": "hyprctl dispatch dpms off",
        "on_resume": "hyprctl dispatch dpms on",
    },
    "suspend": {
        "var": "suspend_timeout",
        "note": "suspend the machine (systemctl suspend)",
        "comment": "Suspend, if nothing responded at all",
        "on_timeout": "systemctl suspend",
        "on_resume": None,
    },
}

_VAR_NAMES = "|".join(spec["var"] for spec in STEP_SPECS.values())
_VAR_RE = re.compile(rf"^\$({_VAR_NAMES})\s*=\s*(\d+)")


def _env_c() -> dict:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    return env


# ---------------------------------------------------------------------------
# Parsing an existing config
# ---------------------------------------------------------------------------

def read_current_timeouts() -> dict:
    """The timings actually in hypridle.conf, in minutes. {} if there is no file."""
    if not HYPRIDLE_CONF.exists():
        return {}
    var_to_step = {spec["var"]: step for step, spec in STEP_SPECS.items()}
    found = {}
    try:
        for line in HYPRIDLE_CONF.read_text().splitlines():
            match = _VAR_RE.match(line.strip())
            if match:
                step = var_to_step[match.group(1)]
                found[step] = max(1, round(int(match.group(2)) / 60))
    except OSError:
        return {}
    return found


def _strip_legacy(text: str) -> str:
    """Drop stray $*_timeout lines, listener blocks and the comments orphaned by them."""
    lines = text.splitlines()
    out = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _VAR_RE.match(stripped) or stripped.startswith("# ---- "):
            index += 1
            continue

        # A numbered comment like "# 3. Turn the monitor off ..." always belonged
        # to the listener below it, and goes with it
        if re.match(r"^#\s*\d+\.\s", stripped):
            index += 1
            continue

        if re.match(r"^listener\s*\{", stripped):
            depth = line.count("{") - line.count("}")
            index += 1
            while index < len(lines) and depth > 0:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue

        out.append(line)
        index += 1

    # collapse the blank space left behind by the removed blocks
    collapsed = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed).rstrip() + "\n"


def build_managed_block(profile: dict, power: str) -> str:
    """The managed region: variables and listeners for the enabled steps only."""
    enabled = [step for step in STEPS if profile[step]["enabled"]]

    lines = [
        MARK_START,
        "# Edits inside this block are overwritten. Everything outside it is kept.",
        f"# Profile: {'on AC power' if power == 'ac' else 'on battery'}.",
        "",
    ]

    if not enabled:
        lines.append("# Every step is disabled — hypridle does nothing.")
        lines.append(MARK_END)
        return "\n".join(lines) + "\n"

    width = max(len(STEP_SPECS[step]["var"]) for step in enabled)
    for step in enabled:
        spec = STEP_SPECS[step]
        minutes = profile[step]["minutes"]
        name = f"${spec['var']}".ljust(width + 1)
        lines.append(f"{name} = {minutes * 60:<6} # {minutes} min — {spec['note']}")

    for step in enabled:
        spec = STEP_SPECS[step]
        lines.append("")
        lines.append(f"# {spec['comment']}")
        lines.append("listener {")
        lines.append(f"    timeout = ${spec['var']}")
        lines.append(f"    on-timeout = {spec['on_timeout']}")
        if spec["on_resume"]:
            lines.append(f"    on-resume = {spec['on_resume']}")
        lines.append("}")

    lines.append("")
    lines.append(MARK_END)
    return "\n".join(lines) + "\n"


def render(text: str, profile: dict, power: str) -> str:
    """Insert or replace the managed region, leaving the rest of the file alone."""
    block = build_managed_block(profile, power)

    start = text.find(MARK_START)
    end = text.find(MARK_END)
    if start != -1 and end != -1 and end > start:
        tail = text[end + len(MARK_END):].lstrip("\n")
        head = text[:start].rstrip("\n")
        return (head + "\n\n" + block + ("\n" + tail if tail else "")).lstrip("\n")

    head = _strip_legacy(text)
    return head.rstrip("\n") + "\n\n" + block


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def _backup_once() -> None:
    backup = HYPRIDLE_CONF.with_name(f"{HYPRIDLE_CONF.name}.bak-{date.today().isoformat()}")
    if not backup.exists() and HYPRIDLE_CONF.exists():
        backup.write_text(HYPRIDLE_CONF.read_text())


def hypridle_running() -> bool:
    try:
        return subprocess.run(["/usr/bin/pgrep", "-x", "hypridle"],
                              capture_output=True, timeout=3).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def restart_hypridle() -> tuple[bool, str]:
    """hypridle does not reload its config — only a full restart works."""
    with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
        subprocess.run(["/usr/bin/pkill", "-x", "hypridle"], capture_output=True, timeout=5)
    time.sleep(0.3)
    try:
        subprocess.run(["hyprctl", "dispatch", "exec", "hypridle"],
                       capture_output=True, text=True, timeout=5, env=_env_c())
    except FileNotFoundError:
        return False, "hyprctl not found — is this a Hyprland session?"
    except subprocess.TimeoutExpired:
        return False, "hyprctl did not answer in time"

    for _ in range(10):
        time.sleep(0.2)
        if hypridle_running():
            return True, ""
    return False, "hypridle did not come back up after the restart"


def apply_profile(cfg: dict, power: str) -> tuple[bool, str]:
    """Write the chosen profile's timings and restart hypridle."""
    profile = cfg["idle"][power]
    try:
        previous = HYPRIDLE_CONF.read_text() if HYPRIDLE_CONF.exists() else ""
    except OSError as error:
        return False, f"cannot read hypridle.conf: {error}"

    _backup_once()
    new_text = render(previous, profile, power)
    if new_text == previous and hypridle_running():
        _remember_power(power)
        return True, ""

    try:
        atomic_write_text(HYPRIDLE_CONF, new_text)
    except OSError as error:
        return False, f"cannot write hypridle.conf: {error}"

    ok, message = restart_hypridle()
    if not ok:
        # roll back to what was known to work
        with contextlib.suppress(OSError):
            atomic_write_text(HYPRIDLE_CONF, previous)
        restart_hypridle()
        return False, f"{message} — the config was rolled back"

    _remember_power(power)
    return True, ""


# ---------------------------------------------------------------------------
# Power source
# ---------------------------------------------------------------------------

def on_ac() -> bool:
    try:
        return AC_ONLINE.read_text().strip() == "1"
    except OSError:
        pass
    # fallback: any supply of type Mains
    try:
        for entry in Path("/sys/class/power_supply").iterdir():
            try:
                if ((entry / "type").read_text().strip() == "Mains"
                        and (entry / "online").read_text().strip() == "1"):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def current_power() -> str:
    return "ac" if on_ac() else "battery"


def _remember_power(power: str) -> None:
    with contextlib.suppress(OSError):
        state_path(APPLIED_POWER_FILE).write_text(power + "\n")


def applied_power() -> str | None:
    try:
        value = state_path(APPLIED_POWER_FILE).read_text().strip()
    except OSError:
        return None
    return value if value in ("ac", "battery") else None


# ---------------------------------------------------------------------------
# Stay-awake mode
# ---------------------------------------------------------------------------

def caffeine_pid() -> int | None:
    """PID of the live inhibitor, else None (cleaning up a stale file on the way)."""
    path = state_path(CAFFEINE_PID_FILE)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        path.unlink(missing_ok=True)
        return None
    if b"systemd-inhibit" not in cmdline:
        path.unlink(missing_ok=True)
        return None
    return pid


def caffeine_active() -> bool:
    return caffeine_pid() is not None


def caffeine_set(enabled: bool) -> tuple[bool, str]:
    pid = caffeine_pid()

    if not enabled:
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
            state_path(CAFFEINE_PID_FILE).unlink(missing_ok=True)
        return True, ""

    if pid is not None:
        return True, ""

    try:
        process = subprocess.Popen(
            ["systemd-inhibit",
             "--what=idle:sleep",
             "--who=Screen & Sleep",
             "--why=Stay-awake mode is on",
             "--mode=block",
             "sleep", "infinity"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False, "systemd-inhibit not found"

    time.sleep(0.3)
    if process.poll() is not None:
        return False, "could not turn on stay-awake mode"

    state_path(CAFFEINE_PID_FILE).write_text(f"{process.pid}\n")
    return True, ""
