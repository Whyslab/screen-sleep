#!/usr/bin/env bash
# install.sh — install Screen & Sleep.
#
# Everything goes under your home directory; nothing needs root.
#
#   ./install.sh                    install for the current user
#   ./install.sh --dry-run          print every step, change nothing
#   ./install.sh --prefix /tmp/test install into a sandbox
#
# The first run of the GUI adopts whatever hypridle is already doing, so
# installing this does not silently change when your screen locks.

set -Eeuo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()  { printf '%b\n' "${BLUE}==>${NC} $*"; }
ok()   { printf '%b\n' "${GREEN}  ok${NC} $*"; }
warn() { printf '%b\n' "${YELLOW}  !!${NC} $*"; }
die()  { printf '%b\n' "${RED}error:${NC} $*" >&2; exit 1; }

DRY=0; PREFIX=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --prefix)  PREFIX="${2%/}"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

[[ $EUID -ne 0 ]] || die "do not run this as root — it installs into your home directory"

SRC_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

DATA_HOME="${PREFIX}${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${PREFIX}${XDG_CONFIG_HOME:-$HOME/.config}"
APP_DIR="${DATA_HOME}/screen-sleep"
DESKTOP_DIR="${DATA_HOME}/applications"
UNIT_DIR="${CONFIG_HOME}/systemd/user"

run() { if (( DRY )); then printf '   would run: %s\n' "$*"; else "$@"; fi; }

say "Install root: ${PREFIX:-$HOME}"
(( DRY )) && warn "dry run — nothing will be written"

# ---------------------------------------------------------------- deps

REQUIRED=(python3 hyprctl)
OPTIONAL=(hypridle hyprsunset brightnessctl notify-send systemd-inhibit)

if [[ -z "$PREFIX" ]]; then
    missing=()
    for d in "${REQUIRED[@]}"; do command -v "$d" >/dev/null || missing+=("$d"); done
    (( ${#missing[@]} == 0 )) || die "missing: ${missing[*]} — this needs a Hyprland session"

    if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null; then
        die "python-gobject with GTK 3 is missing. On Arch: sudo pacman -S python-gobject gtk3"
    fi
    ok "python3 with GTK 3 bindings present"

    for d in "${OPTIONAL[@]}"; do
        command -v "$d" >/dev/null || warn "missing: ${d} — $(
            case "$d" in
                hypridle)        echo "the idle chain will not work" ;;
                hyprsunset)      echo "the night filter will not work" ;;
                brightnessctl)   echo "the dim step will not work" ;;
                notify-send)     echo "no notification when the power source changes" ;;
                systemd-inhibit) echo "the stay-awake button will not work" ;;
            esac)"
    done
fi

# ---------------------------------------------------------------- code

say "Installing into ${APP_DIR}"
if [[ -d "$APP_DIR" ]]; then
    BACKUP="${APP_DIR}.bak-$(date +%Y%m%d_%H%M%S)"
    warn "already installed — keeping the previous copy at ${BACKUP}"
    run cp -a "$APP_DIR" "$BACKUP"
fi
run install -d -m 755 "$APP_DIR"
run install -m 644 "${SRC_DIR}"/screen_sleep/*.py "$APP_DIR/"
run chmod 755 "${APP_DIR}/gui.py" "${APP_DIR}/daemon.py"
ok "code installed"

# ---------------------------------------------------------------- desktop entry

say "Installing the application entry"
run install -d -m 755 "$DESKTOP_DIR"
if (( DRY )); then
    printf '   would write: %s (with APP_DIR=%s)\n' "${DESKTOP_DIR}/screen-sleep.desktop" "$APP_DIR"
else
    sed "s|__APP_DIR__|${APP_DIR}|g" "${SRC_DIR}/screen-sleep.desktop" \
        > "${DESKTOP_DIR}/screen-sleep.desktop"
    chmod 644 "${DESKTOP_DIR}/screen-sleep.desktop"
fi
ok "shows up in the launcher as \"Screen & Sleep\""

# ---------------------------------------------------------------- daemon

say "Installing the daemon unit"
run install -d -m 755 "$UNIT_DIR"
if (( DRY )); then
    printf '   would write: %s (with APP_DIR=%s)\n' "${UNIT_DIR}/screen-sleep-daemon.service" "$APP_DIR"
else
    sed "s|__APP_DIR__|${APP_DIR}|g" "${SRC_DIR}/systemd/screen-sleep-daemon.service" \
        > "${UNIT_DIR}/screen-sleep-daemon.service"
    chmod 644 "${UNIT_DIR}/screen-sleep-daemon.service"
fi

if [[ -n "$PREFIX" ]]; then
    echo
    ok "Sandbox install finished: ${PREFIX}"
    exit 0
fi

run systemctl --user daemon-reload
if (( DRY )); then
    printf '   would run: systemctl --user enable --now screen-sleep-daemon.service\n'
else
    systemctl --user enable --now screen-sleep-daemon.service
    ok "daemon enabled and started"
fi

# ---------------------------------------------------------------- done

echo
ok "Installation finished."
echo
echo "  Open the window:      from the launcher, or python3 ${APP_DIR}/gui.py"
echo "  Straight to the filter: python3 ${APP_DIR}/gui.py --night"
echo "  Daemon log:           journalctl --user -u screen-sleep-daemon -f"
echo
echo "  Bind it to a key, if you like (Hyprland):"
echo "    bind = SUPER SHIFT, N, exec, python3 ${APP_DIR}/gui.py"
echo
echo "  The night filter needs hyprsunset running. In hyprland.conf:"
echo "    exec-once = hyprsunset"
