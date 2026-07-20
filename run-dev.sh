#!/bin/sh
# Dev launcher for this machine: makes the local static ffprobe (in ./bin) and
# the venv visible, then starts uvicorn. On a normal install with ffmpeg/ffprobe
# already on PATH you can just run:  uvicorn main:app
#
# Then open http://127.0.0.1:8000 in your browser — do NOT open
# static/index.html directly (a file-opened page has no server to talk to).
# Tip: double-click "Start ClearView.command" to launch and open the browser
# for you.
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$DIR/bin:$DIR/.venv/bin:$PATH"
cd "$DIR"
exec "$DIR/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000
