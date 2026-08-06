"""ClearView desktop entry point.

Starts the FastAPI/uvicorn server on a free local port in a background thread,
waits for it to come up, then shows the UI:

- macOS/Linux: a native application window via pywebview (falls back to the
  browser if a webview backend isn't available).
- Windows: the default browser (pywebview + .NET/pythonnet is fragile under
  PyInstaller, so we don't depend on it there) plus a small console window that
  shows status and keeps the app running.

Set CLEARVIEW_NO_WINDOW=1 to run headless (used by tests/CI). Every launch writes
a log to the per-user data dir so a packaged crash is always diagnosable.

This is what the packaged Mac/Windows app runs — the user just launches it; no
terminal gymnastics, no Python setup.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
import urllib.request

import uvicorn

APP_NAME = "ClearView"
_HOST = "127.0.0.1"


def _log_path():
    try:
        from main import _user_data_dir

        d = _user_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "clearview.log"
    except Exception:
        return None


def _log(msg: str) -> None:
    """Append a timestamped line to the log file and echo to stdout."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    p = _log_path()
    if p is not None:
        try:
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


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


def _serve_forever(thread: threading.Thread) -> None:
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def _show_browser(url: str, thread: threading.Thread) -> None:
    import webbrowser

    _log(f"opening browser at {url}")
    webbrowser.open(url)
    print(f"\n  {APP_NAME} is running at {url}")
    print("  Leave this window open while you use the app. Close it to quit.\n", flush=True)
    _serve_forever(thread)


def _show_native_window(url: str, thread: threading.Thread) -> None:
    try:
        import webview  # type: ignore

        _log("opening native window (pywebview)")
        webview.create_window(APP_NAME, url, width=1180, height=900, min_size=(900, 640))
        webview.start()
    except Exception as exc:  # noqa: BLE001 - fall back to the browser
        _log(f"native window unavailable ({exc!r}); falling back to browser")
        _show_browser(url, thread)


def main() -> None:
    _log(f"{APP_NAME} launch (frozen={getattr(sys, 'frozen', False)}, platform={sys.platform})")
    port = int(os.environ.get("CLEARVIEW_PORT", "0")) or _free_port()
    url = f"http://{_HOST}:{port}"

    _log("importing server app")
    from main import app  # noqa: WPS433 - import here so packaging errors are logged

    _log(f"starting server on {url}")
    config = uvicorn.Config(app, host=_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_until_up(f"{url}/api/health"):
        raise SystemExit("ClearView server failed to start.")
    _log("server is up")

    if os.environ.get("CLEARVIEW_NO_WINDOW") == "1":
        _log("headless mode")
        _serve_forever(thread)
    elif sys.platform == "win32":
        # Robust path for Windows: browser UI + a visible console window.
        _show_browser(url, thread)
    else:
        _show_native_window(url, thread)


def _log_crash(exc: BaseException) -> None:
    """Write a traceback to a user-visible log so a packaged crash is diagnosable."""
    import traceback

    text = "".join(traceback.format_exception(exc))
    try:
        from main import _user_data_dir

        log_dir = _user_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "clearview-error.log").write_text(text, encoding="utf-8")
    except Exception:
        pass
    print(text, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - last-resort crash reporter
        _log_crash(exc)
        # On a visible console, give the user a moment to read the error.
        if sys.platform == "win32" and sys.stdout and sys.stdout.isatty():
            try:
                input("\nClearView hit an error (see above). Press Enter to close…")
            except Exception:
                pass
        raise
