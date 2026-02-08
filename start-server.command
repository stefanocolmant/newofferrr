#!/bin/zsh
set -euo pipefail

# Runs a local static server for this folder.
# Keep this Terminal window open while browsing the site.

cd "$(dirname "$0")"
python3 -m http.server 8080 --bind 127.0.0.1

