#!/bin/zsh
set -euo pipefail

# Dev server with live-reload (auto refresh on file changes).
# Keep this Terminal window open while browsing the site.

cd "$(dirname "$0")"
python3 dev_server.py --bind 127.0.0.1 --port 8080

