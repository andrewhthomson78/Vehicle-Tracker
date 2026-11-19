#!/bin/bash
# Keeps Vehicle Tracker running in the background on this Mac: it starts when you log in,
# restarts if it ever stops, and needs no Terminal window.
#
#   bash tools/install-service.sh            install (or update) the service and start it
#   bash tools/install-service.sh --uninstall stop it and remove it
#
# Logs go to data/server.log. The app stays at http://localhost:8765 (or $PORT in .env).
set -euo pipefail

LABEL="uk.co.vehicletracker.server"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

[ "$(uname)" = "Darwin" ] || { echo "This installs a macOS background service, and this isn't a Mac." >&2; exit 1; }

stop_service() {
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
    stop_service
    rm -f "$PLIST"
    echo "Vehicle Tracker will no longer start on its own. Run it with: python3 server.py"
    exit 0
fi

PYTHON="$(command -v python3 || true)"
[ -x "$PYTHON" ] || { echo "python3 isn't installed, or isn't on PATH." >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data"
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$ROOT/server.py</string>
    </array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>$ROOT/data/server.log</string>
    <key>StandardErrorPath</key><string>$ROOT/data/server.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST_END

# a server already running by hand would hold the port
pkill -f "$ROOT/server.py" 2>/dev/null || true
stop_service
launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

PORT_IN_USE="$(grep -E '^PORT=' "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2 || true)"
URL="http://localhost:${PORT_IN_USE:-8765}"
for _ in $(seq 1 20); do
    if curl -fs -o /dev/null --max-time 2 "$URL/api/version" 2>/dev/null; then
        echo "Vehicle Tracker is running at $URL and will start on its own from now on."
        echo "Log: $ROOT/data/server.log"
        exit 0
    fi
    sleep 1
done

echo "The service was installed but hasn't answered yet. Check $ROOT/data/server.log" >&2
exit 1
