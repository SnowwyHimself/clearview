#!/bin/sh
# Double-click this file in Finder to launch ClearView.
# It starts the local server and opens the app in your browser at the correct
# URL — so you never have to open the raw HTML file (which can't work on its own).
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

URL="http://127.0.0.1:8000"

# Wait for the server to be ready, then open the browser (background job so it
# doesn't block the server).
(
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$URL/api/health" 2>/dev/null; then break; fi
    sleep 0.25
  done
  if command -v open >/dev/null 2>&1; then open "$URL";
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi
) &

echo "Starting ClearView… your browser will open at $URL"
echo "(Keep this window open while you use the app. Press Ctrl+C to stop.)"
exec sh "$DIR/run-dev.sh"
