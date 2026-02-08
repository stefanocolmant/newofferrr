#!/bin/zsh
set -euo pipefail

# Runs a local static server for this folder.
# Keep this Terminal window open while browsing the site.

cd "$(dirname "$0")"
# Bind to all interfaces so you can open it from this Mac (localhost)
# and from other devices on the same Wi-Fi (use this Mac's LAN IP).
python3 -m http.server 8080 --bind 0.0.0.0
