"""ClearView desktop entry point.

Starts the FastAPI/uvicorn server on a free local port in a background thread,
waits for it to come up, then shows the UI in a native application window
(via pywebview). Falls back to the default web browser if a native webview
isn't available. Set CLEARVIEW_NO_WINDOW=1 to run headless (used by tests/CI).

This is what the packaged Mac/Windows app launches — the user just double-clicks
the app; no terminal, no browser gymnastics, no Python setup.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import urllib.request

import uvicorn

APP_NAME = "ClearView"
_HOST = "127.0.0.1"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((_HOST, 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> None:
    port = int(os.environ.get("CLEARVIEW_PORT", "0")) or _free_port()
    url = f"http://{_HOST}:{port}"

    # The server must import cleanly; import here so a packaged app fails loudly.
    from main import app  # noqa: WPS433 (local import is intentional)

    config = uvicorn.Config(app, host=_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_until_up(f"{url}/api/health"):
        raise SystemExit("ClearView server failed to start.")

    if os.environ.get("CLEARVIEW_NO_WINDOW") == "1":
        print(f"{APP_NAME} running headless at {url} (press Ctrl+C to quit)")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    # Preferred: a real native window. Fall back to the browser if unavailable.
    try:
        import webview  # type: ignore

        webview.create_window(APP_NAME, url, width=1180, height=900, min_size=(900, 640))
        webview.start()
    except Exception:
        import webbrowser

        webbrowser.open(url)
        print(f"{APP_NAME} is running at {url}")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def _log_crash(exc: BaseException) -> None:
    """Write a traceback to a user-visible log so a packaged crash is diagnosable."""
    import traceback

    try:
        from main import _user_data_dir  # reuse the same per-user location

        log_dir = _user_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "clearview-error.log").open("w", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(exc)))
    except Exception:
        pass
    traceback.print_exception(exc)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - last-resort crash reporter
        _log_crash(exc)
        raise
