#!/usr/bin/env bash
# Copies the shared tool (src/nse.py, src/holidays.json) into every skill folder.
set -euo pipefail
cd "$(dirname "$0")"
for d in skills/nse-*/; do cp src/nse.py src/holidays.json "$d"; done
echo "built: $(ls -d skills/nse-* | wc -l) skills"
