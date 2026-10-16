#!/bin/sh
# Convertit tous les films Fantavision de movies/ en SVG animés dans svg-output/.
# Usage : ./convert-movies.sh [options passées à fant2svg.py, ex. --step 0.04]
set -eu
cd "$(dirname "$0")"
mkdir -p svg-output
n=0
for f in movies/*; do
    [ -f "$f" ] || continue
    case "$f" in *.info) continue ;; esac
    name=$(basename "$f")
    python3 fant2svg.py "$f" "svg-output/$name.svg" "$@"
    n=$((n + 1))
done
echo "$n film(s) converti(s) dans svg-output/"
