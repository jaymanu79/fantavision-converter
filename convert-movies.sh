#!/bin/sh
# Converts every Fantavision movie in movies/ to an animated SVG in svg-output/.
# Usage: ./convert-movies.sh [options passed to fant2svg.py, e.g. --step 0.04]
set -eu
cd "$(dirname "$0")"
rm -r svg-output 2>/dev/null
mkdir -p svg-output
n=0
for f in movies/*; do
    [ -f "$f" ] || continue
    case "$f" in *.info) continue ;; esac
    name=$(basename "$f")
    python3 fant2svg.py "$f" "svg-output/$name.svg" "$@"
    n=$((n + 1))
done
echo "$n movie(s) converted to svg-output/"
