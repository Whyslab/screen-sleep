#!/usr/bin/env bash
# uninstall.sh — remove Screen & Sleep.
#
#   ./uninstall.sh                    remove the program, keep your settings
#   ./uninstall.sh --purge            also remove the config and saved state
#   ./uninstall.sh --dry-run          print what would happen
#   ./uninstall.sh --prefix /tmp/test undo a sandbox install
#
# Your hypridle.conf is left exactly as it is. The managed block inside it
# keeps working on its own — it is ordinary hypridle configuration.

set -Eeuo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()  { printf '%b\n' "${BLUE}==>${NC} $*"; }
ok()   { printf '%b\n' "${GREEN}  ok${NC} $*"; }
warn() { printf '%b\n' "${YELLOW}  !!${NC} $*"; }
die()  { printf '%b\n' "${RED}error:${NC} $*" >&2; exit 1; }

DRY=0; PURGE=0; PREFIX=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --purge)   PURGE=1; shift ;;
        --prefix)  PREFIX="${2%/}"; shift 2 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

DATA_HOME="${PREFIX}${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${PREFIX}${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${PREFIX}${XDG_STATE_HOME:-$HOME/.local/state}"
APP_DIR="${DATA_HOME}/screen-sleep"
UNIT_DIR="${CONFIG_HOME}/systemd/user"

run() { if (( DRY )); then printf '   would run: %s\n' "$*"; else "$@"; fi; }
(( DRY )) && warn "dry run — nothing will be removed"

if [[ -z "$PREFIX" ]]; then
    say "Stopping the daemon"
    if systemctl --user list-unit-files screen-sleep-daemon.service --no-legend 2>/dev/null | grep -q .; then
        run systemctl --user disable --now screen-sleep-daemon.service
    fi
    # A stay-awake inhibitor would otherwise outlive the program that made it.
    if [[ -r "${STATE_HOME}/screen-sleep/caffeine.pid" ]]; then
        pid=$(cat "${STATE_HOME}/screen-sleep/caffeine.pid" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            say "Releasing the stay-awake inhibitor"
            run kill "$pid"
        fi
    fi
fi

say "Removing files"
run rm -rf "$APP_DIR"
run rm -f "${DATA_HOME}/applications/screen-sleep.desktop"
run rm -f "${UNIT_DIR}/screen-sleep-daemon.service"
[[ -z "$PREFIX" ]] && run systemctl --user daemon-reload
ok "program removed"

if (( PURGE )); then
    say "--purge: removing settings and state"
    run rm -rf "${CONFIG_HOME}/screen-sleep" "${STATE_HOME}/screen-sleep"
    ok "settings and state removed"
else
    say "Keeping your settings"
    echo "   ${CONFIG_HOME}/screen-sleep/config.json"
fi

echo
say "hypridle.conf was NOT touched"
echo "   ${CONFIG_HOME}/hypr/hypridle.conf"
echo
echo "   The managed block inside it is ordinary hypridle configuration and keeps"
echo "   working. To go back to a hand-written config, delete the block between"
echo "   the '# >>> screen-sleep' and '# <<< screen-sleep' markers, or restore one"
echo "   of the hypridle.conf.bak-* files this program left beside it."
echo
ok "Uninstall finished."
