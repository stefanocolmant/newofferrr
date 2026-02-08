#!/bin/zsh
set -euo pipefail

# Dev server with live-reload (auto refresh on file changes).
# Keep this Terminal window open while browsing the site.

cd "$(dirname "$0")"
# Bind to all interfaces so you can open it from this Mac (localhost)
# and from other devices on the same Wi-Fi (use this Mac's LAN IP).
python3 dev_server.py --bind 0.0.0.0 --port 8080
