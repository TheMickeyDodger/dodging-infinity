#!/usr/bin/env bash
# Optional per-user macOS LaunchAgent for the DI-REMOTE-2 Runtime
# (`dirun run`). Mirrors the tgop LaunchAgent semantics: per-user
# only, absolute paths, RunAtLoad, KeepAlive with a restart throttle,
# logs in the protected state directory, exact uninstall.
#
# This lives in scripts/ (shell) deliberately: the Runtime package
# itself carries no subprocess surface outside its git transport seam
# (a structural guarantee the static suite pins), so the launchctl
# interaction belongs to the human-invoked installer, not to product
# code.
#
# E-1 binding consequence: with this agent loaded, the Runtime claims
# a durably authorized workflow automatically — after Approve Mission
# there is no manual Mac, clone, registration, Herdr-setup,
# configuration-switching, or terminal step. If the agent is NOT
# installed or running, the adapter's /status reports the Runtime as
# not running with this script named as the remedy.
#
# Usage:
#   scripts/dirun-agent.sh install [--config PATH]
#   scripts/dirun-agent.sh uninstall
# Test seams (hermetic tests only):
#   --home DIR   use DIR instead of $HOME
#   --no-load    write/remove the plist without invoking launchctl
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.dodginginfinity.dirun"
THROTTLE_SECONDS=10
# Fixed base PATH for the launchd job: launchd does not inherit the
# interactive shell PATH, and the handoff-validation role turn runs
# the `codex` binary by name. The directory codex resolves to at
# INSTALL time is placed FIRST so the validated binary always wins;
# the ambient PATH is never passed through.
BASE_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

COMMAND="${1:-}"
shift || true
CONFIG=""
HOME_DIR="$HOME"
NO_LOAD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --home) HOME_DIR="$2"; shift 2 ;;
    --no-load) NO_LOAD=1; shift ;;
    *) echo "dirun-agent: unknown argument: $1" >&2; exit 2 ;;
  esac
done

PLIST_DIR="$HOME_DIR/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"

if [[ "$COMMAND" == "uninstall" ]]; then
  if [[ ! -e "$PLIST" ]]; then
    echo "dirun-agent: nothing installed at $PLIST"
    exit 0
  fi
  if [[ "$NO_LOAD" -eq 0 ]]; then
    launchctl unload -w "$PLIST" || true
  fi
  rm -f "$PLIST"
  echo "dirun-agent: unloaded and removed $PLIST"
  exit 0
fi

if [[ "$COMMAND" != "install" ]]; then
  echo "usage: dirun-agent.sh install [--config PATH] | uninstall" >&2
  exit 2
fi

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "dirun-agent: python3 is not resolvable on PATH; nothing installed" >&2
  exit 2
fi
CODEX_BIN="$(command -v codex || true)"
if [[ -z "$CODEX_BIN" ]]; then
  echo "dirun-agent: the codex binary is not resolvable on the current PATH," >&2
  echo "so the installed Runtime could never complete a handoff-validation" >&2
  echo "turn. Nothing was installed. Put codex on PATH and re-run install." >&2
  exit 2
fi
CODEX_DIR="$(cd "$(dirname "$CODEX_BIN")" && pwd)"
JOB_PATH="$CODEX_DIR"
IFS=':' read -r -a BASE_PARTS <<< "$BASE_PATH"
for part in "${BASE_PARTS[@]}"; do
  if [[ "$part" != "$CODEX_DIR" ]]; then
    JOB_PATH="$JOB_PATH:$part"
  fi
done

if [[ -n "$CONFIG" ]]; then
  CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
  STATE_DIR="$(dirname "$CONFIG")"
else
  STATE_DIR="$HOME_DIR/Library/Application Support/DodgingInfinity/telegram"
fi

mkdir -p "$PLIST_DIR"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

CONFIG_ARGS=""
if [[ -n "$CONFIG" ]]; then
  CONFIG_ARGS="    <string>--config</string>
    <string>$CONFIG</string>
"
fi

umask 177
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$ROOT/dirun.py</string>
$CONFIG_ARGS    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$JOB_PATH</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>$THROTTLE_SECONDS</integer>
  <key>StandardOutPath</key>
  <string>$STATE_DIR/dirun.out.log</string>
  <key>StandardErrorPath</key>
  <string>$STATE_DIR/dirun.err.log</string>
</dict>
</plist>
PLISTEOF
umask 022

if [[ "$NO_LOAD" -eq 0 ]]; then
  if ! launchctl load -w "$PLIST"; then
    echo "dirun-agent: wrote $PLIST but launchctl load -w failed;" >&2
    echo "load it manually or check launchctl list" >&2
    exit 1
  fi
fi
echo "dirun-agent: installed $PLIST"
