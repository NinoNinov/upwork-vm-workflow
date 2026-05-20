#!/bin/bash
# Container entrypoint: start Xvfb in the background, then exec the scraper.
#
# Why not xvfb-run? The wrapper hangs in this container (likely auth/mcookie
# interaction with the non-root scraper user). Starting Xvfb explicitly and
# setting $DISPLAY is more reliable.

set -e

# Pick a display number. :99 is the convention.
export DISPLAY=:99

# Start the X virtual framebuffer in the background.
# -nolisten tcp: no remote X clients (security; we only need local).
# +extension RANDR: many Chromium versions ICE without RANDR available.
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp +extension RANDR >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!

# Wait briefly for Xvfb to be ready.
for i in 1 2 3 4 5 6 7 8 9 10; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Clean up Xvfb when the main process exits.
trap "kill $XVFB_PID 2>/dev/null || true" EXIT

# Hand off to the real entrypoint (whatever was set previously).
exec python -u main.py "$@"
