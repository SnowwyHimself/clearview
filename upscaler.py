"""The ClearView upscaling pipeline.

Given a :class:`jobs.Job`, this module:

1. Probes the upload with ffprobe (resolution / fps / duration / audio) and
   enforces the size and length limits.
2. Extracts every frame to PNG with ffmpeg at the source fps.
3. Upscales each frame with Real-ESRGAN, updating job progress and a rolling
   seconds-per-frame estimate, and emits a before/after preview from the first
   frame so the UI can show the effect early.
4. Re-encodes to H.264 (crf 18, yuv420p, +faststart) at the source fps, scaling
   to the requested factor capped at 4K, and copies the original audio back in.
5. Cleans up the extracted/upscaled frame folders.

The Real-ESRGAN engine is created lazily and cached: GPU (Vulkan, gpuid=0) is
tried first and we fall back to CPU (gpuid=-1) if that fails. Whether we ended
up on CPU is exposed via :func:`running_on_cpu` so the UI can warn.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from jobs import ERROR, Job

# --- External tools ----------------------------------------------------------


def _resolve_tool(name: str) -> str:
    """Locate ffmpeg/ffprobe: prefer a copy bundled inside the frozen app, then
    fall back to whatever is on PATH (normal `uvicorn main:app` runs)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        exe = name + (".exe" if sys.platform == "win32" else "")
        bundled = os.path.join(base, exe)
        if os.path.exists(bundled):
            return bundled
    return shutil.which(name) or name


FFMPEG = _resolve_tool("ffmpeg")
FFPROBE = _resolve_tool("ffprobe")

# --- Limits ------------------------------------------------------------------

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_DURATION_SECONDS = 10 * 60  # 10 minutes
MAX_OUTPUT_W = 3840
MAX_OUTPUT_H = 2160
# Still images always process at full resolution (unlike video, a downscaled
# working res would visibly wreck a photo) — but bound huge uploads so a giant
# source can't exhaust memory. 2160p of input already saturates the 4K output.
IMAGE_MAX_INPUT_H = 2160

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def kind_for(filename: str, content_type: str = "") -> str:
    """Classify an upload as 'image' or 'video' from its type/extension."""
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    return "image" if Path(filename).suffix.lower() in IMAGE_EXTS else "video"

# --- Model selection ---------------------------------------------------------
#
# The realesrgan-ncnn-py package selects a bundled model by integer index; each
# index has a fixed native scale. We map the two user-facing model names plus a
# 2x/4x choice onto those indices. When a native model produces more pixels than
# requested (e.g. x4plus only ships at 4x but the user picked 2x), the final
# ffmpeg pass resizes the already-detailed frames down to the exact target.
#
#   idx 0: realesr-animevideov3-x2   (native 2x)
#   idx 2: realesr-animevideov3-x4   (native 4x)
#   idx 4: realesrgan-x4plus         (native 4x)

MODEL_LABELS = {
    "realesr-animevideov3": "Real-ESRGAN AnimeVideo v3 (fast)",
    "realesrgan-x4plus": "Real-ESRGAN x4plus (max quality)",
}


def _select_engine(model: str, scale: int, capped: bool) -> tuple[int, int]:
    """Return (engine_index, native_scale) for the request.

    When the working resolution is capped below the source, we always use the
    native 4x model: its cost is set by *input* pixels (only ~1.4x the 2x model
    per input pixel) but it produces 4x the detail, so it's the most efficient
    way to enlarge a small working frame before the final resize to target.
    """
    if model == "realesr-animevideov3":
        if capped:
            return 2, 4  # animevideov3 x4
        return (0, 2) if scale == 2 else (2, 4)
    if model == "realesrgan-x4plus":
        return 4, 4  # x4plus is 4x only
    raise ValueError(f"Unknown model {model!r}")


# Working-resolution presets (max input height fed to the AI). Capping the input
# is the dominant speed lever because inference cost scales with input pixels.
SPEED_PRESETS = {
    "fast": 360,      # ~6x faster than full 1080p; ideal for low-quality source
    "balanced": 540,  # ~3x faster
    "full": 0,        # no cap — maximum fidelity
}


# Rough GPU cost per *input* megapixel, in seconds (measured on Apple M-series
# via Vulkan). Used only to show an up-front time estimate so users aren't
# surprised — the live ETA during a job is measured directly. x4plus is ~30x
# slower than AnimeVideo, so a long clip on it takes a very long time.
SPEED_S_PER_INPUT_MP = {
    "realesr-animevideov3|2": 0.5,
    "realesr-animevideov3|4": 0.7,
    "realesrgan-x4plus|2": 20.4,
    "realesrgan-x4plus|4": 20.4,
}


# --- Tool / engine availability ---------------------------------------------


def _tool_ok(path: str, name: str) -> bool:
    # An absolute bundled path just needs to exist; a bare name must be on PATH.
    if os.path.isabs(path):
        return os.path.exists(path)
    return shutil.which(path) is not None or shutil.which(name) is not None


def check_tools() -> list[str]:
    """Return a list of required external tools that are missing."""
    missing = []
    if not _tool_ok(FFMPEG, "ffmpeg"):
        missing.append("ffmpeg")
    if not _tool_ok(FFPROBE, "ffprobe"):
        missing.append("ffprobe")
    return missing


_engines: dict[int, object] = {}
_running_on_cpu: Optional[bool] = None


def running_on_cpu() -> Optional[bool]:
    """True if the engine fell back to CPU, False if on GPU, None if unknown yet."""
    return _running_on_cpu


def _get_engine(index: int):
    """Lazily create (and cache) a Real-ESRGAN engine, GPU first then CPU."""
    global _running_on_cpu
    if index in _engines:
        return _engines[index]

    # Import here so a missing package surfaces at job time, not app import time.
    from realesrgan_ncnn_py import Realesrgan

    last_err: Optional[Exception] = None
    for gpuid in (0, -1):
        try:
            engine = Realesrgan(gpuid=gpuid, model=index)
            _engines[index] = engine
            _running_on_cpu = gpuid == -1
            return engine
        except Exception as exc:  # noqa: BLE001 - try next device
            last_err = exc
    raise RuntimeError(
        "Could not initialize Real-ESRGAN on GPU or CPU: " + str(last_err)
    )


# --- ffprobe -----------------------------------------------------------------


def probe(path: Path) -> dict:
    """Return resolution, fps, duration and audio info for a media file."""
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            "ffprobe could not read this file — is it a valid video? "
            + out.stderr.strip()
        )
    data = json.loads(out.stdout or "{}")

    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video is None:
        raise ValueError("That file has no video stream I can upscale.")
    audio = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None
    )

    width = int(video.get("width", 0))
    height = int(video.get("height", 0))

    # r_frame_rate is a fraction like "30000/1001"; keep the exact string for
    # ffmpeg and a float for display / ETA math.
    rate_str = video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0"
    num, _, den = rate_str.partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if fps <= 0:
        fps = 25.0
        rate_str = "25/1"

    duration = 0.0
    fmt = data.get("format", {})
    if fmt.get("duration"):
        with contextlib.suppress(ValueError):
            duration = float(fmt["duration"])
    if duration == 0.0 and video.get("duration"):
        with contextlib.suppress(ValueError):
            duration = float(video["duration"])

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "fps_str": rate_str,
        "duration": round(duration, 2),
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "size_bytes": int(fmt.get("size", 0) or (path.stat().st_size)),
        "resolution": f"{width}x{height}",
    }


def _target_dimensions(src_w: int, src_h: int, scale: int) -> tuple[int, int]:
    """Requested output size, capped to 4K (aspect-preserving) and made even."""
    w = src_w * scale
    h = src_h * scale
    if w > MAX_OUTPUT_W or h > MAX_OUTPUT_H:
        ratio = min(MAX_OUTPUT_W / w, MAX_OUTPUT_H / h)
        w = int(w * ratio)
        h = int(h * ratio)
    # yuv420p requires even dimensions.
    w -= w % 2
    h -= h % 2
    return max(w, 2), max(h, 2)


@contextlib.contextmanager
def _quiet_native():
    """Silence the native library's per-frame progress prints (it writes the
    running percentage to stderr, fd 2). Real failures still raise Python
    exceptions, so muting this chatter during the tight loop is safe."""
    try:
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
    except OSError:
        yield  # if redirection isn't available, just let it print
        return
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


# --- Main pipeline -----------------------------------------------------------


def _process_image(job: Job) -> None:
    """Upscale a single still image. No ffmpeg needed — just PIL + Real-ESRGAN.

    Images always run at full source resolution (the video Speed cap is skipped),
    since a still would visibly degrade if the AI worked from a shrunken input.
    """
    job.total_frames = 1
    try:
        with Image.open(job.input_path) as im:
            src = im.convert("RGB")
            src.load()
    except Exception as exc:  # noqa: BLE001
        _fail(job, "That image couldn't be read — is it a valid image file?", str(exc))
        return

    sw, sh = src.size
    if sw == 0 or sh == 0:
        _fail(job, "I couldn't determine the image's dimensions.")
        return
    job.input_meta = {
        "width": sw, "height": sh, "resolution": f"{sw}x{sh}",
        "size_bytes": job.input_path.stat().st_size, "has_audio": False,
        "kind": "image",
    }

    # Bound pathologically large uploads; otherwise process at full resolution.
    work = src
    if sh > IMAGE_MAX_INPUT_H:
        ww = round(sw * IMAGE_MAX_INPUT_H / sh)
        work = src.resize((ww, IMAGE_MAX_INPUT_H), Image.LANCZOS)

    engine_idx, _ = _select_engine(job.model, job.scale, capped=False)
    engine = _get_engine(engine_idx)
    tw, th = _target_dimensions(sw, sh, job.scale)
    output_path = job.dir / "output.png"

    start = time.time()
    with _quiet_native():
        upscaled = engine.process_pil(work)  # PIL RGB in, PIL RGB out
    # Resize the AI result to the exact (4K-capped) target.
    if upscaled.size != (tw, th):
        upscaled = upscaled.resize((tw, th), Image.LANCZOS)
    upscaled.save(output_path, "PNG")

    job.done_frames = 1
    job.seconds_per_frame = time.time() - start
    _write_preview(job, src, upscaled)

    job.output_meta = {
        "width": tw, "height": th, "resolution": f"{tw}x{th}",
        "size_bytes": output_path.stat().st_size, "has_audio": False,
        "kind": "image", "fps": None,
    }
    job.output_path = output_path


def _read_exactly(stream, n: int) -> bytes:
    """Read exactly ``n`` bytes from a pipe, or fewer only at end-of-stream."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def process(job: Job) -> None:
    """Dispatch a job to the image or video pipeline."""
    size_bytes = job.input_path.stat().st_size
    if size_bytes > MAX_UPLOAD_BYTES:
        _fail(job, "That file is over 500 MB. Please upload something smaller.")
        return
    if job.kind == "image":
        _process_image(job)
    else:
        _process_video(job)


def _process_video(job: Job) -> None:
    """Upscale a video. Raises on unrecoverable error.

    Frames are streamed as raw pixels straight from ffmpeg into Real-ESRGAN and
    back into ffmpeg — no intermediate PNG files. The two ffmpeg processes run
    concurrently with the GPU work, so decoding and H.264 encoding overlap the
    upscaling instead of happening serially. This roughly halves wall-clock time
    for the fast model (where PNG encoding used to cost as much as inference) and
    avoids writing thousands of large frames to disk.
    """
    # 1. Probe + validate ----------------------------------------------------
    meta = probe(job.input_path)
    job.input_meta = meta

    if meta["duration"] > MAX_DURATION_SECONDS:
        _fail(
            job,
            "That clip is longer than 10 minutes. Please trim it and try again.",
        )
        return
    sw, sh = meta["width"], meta["height"]
    if sw == 0 or sh == 0:
        _fail(job, "I couldn't determine the video's resolution.")
        return

    # Estimate total frames for the progress bar (exact count would require a
    # full decode pass; duration x fps is close enough and corrected at the end).
    job.total_frames = max(int(round(meta["duration"] * meta["fps"])), 1)

    # Working resolution: the AI runs on this, not the full source. Capping it is
    # the big speed lever, since inference cost scales with input pixels.
    cap = job.max_input_h
    capped = 0 < cap < sh
    if capped:
        ww = sw * cap // sh
        ww -= ww % 2  # even dimensions for the raw pipe / yuv420p
        wh = cap - (cap % 2)
    else:
        ww, wh = sw, sh

    engine_idx, native = _select_engine(job.model, job.scale, capped)
    engine = _get_engine(engine_idx)
    nw, nh = ww * native, wh * native
    tw, th = _target_dimensions(sw, sh, job.scale)  # output based on true source
    output_path = job.dir / "output.mp4"
    frame_bytes = ww * wh * 3

    # 2. Decoder: raw BGR frames at the working size, streamed on stdout ------
    dec_cmd = [FFMPEG, "-v", "error", "-i", str(job.input_path)]
    if capped:
        dec_cmd += ["-vf", f"scale={ww}:{wh}:flags=area"]
    dec_cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    decoder = subprocess.Popen(
        dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    # 3. Encoder: raw BGR frames at native size on stdin -> H.264, scaled to
    #    the 4K-capped target, with the original audio muxed back in ----------
    enc_cmd = [
        FFMPEG, "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{nw}x{nh}",
        "-framerate", meta["fps_str"], "-i", "-",   # frames from stdin
        "-i", str(job.input_path),                  # source, for its audio
    ]
    if meta["has_audio"]:
        enc_cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
    else:
        enc_cmd += ["-map", "0:v:0"]
    enc_cmd += [
        "-vf", f"scale={tw}:{th}:flags=lanczos",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-r", meta["fps_str"],
        str(output_path),
    ]
    encoder = subprocess.Popen(
        enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # 4. Stream frames: decode -> upscale -> encode --------------------------
    recent = deque(maxlen=30)  # rolling per-frame durations for ETA
    done = 0
    try:
        with _quiet_native():
            while True:
                start = time.time()
                raw = _read_exactly(decoder.stdout, frame_bytes)
                if len(raw) < frame_bytes:
                    break  # end of stream
                frame = np.frombuffer(raw, np.uint8).reshape(wh, ww, 3)
                upscaled = engine.process_cv2(frame)
                encoder.stdin.write(upscaled.tobytes())

                done += 1
                job.done_frames = done
                if done >= job.total_frames:
                    job.total_frames = done  # correct a low estimate
                recent.append(time.time() - start)
                job.seconds_per_frame = sum(recent) / len(recent)

                if done == 1:  # emit the before/after preview early (BGR -> RGB)
                    _write_preview(
                        job,
                        Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1])),
                        Image.fromarray(np.ascontiguousarray(upscaled[:, :, ::-1])),
                    )
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        decoder.stdout.close()
        decoder.wait()

    enc_err = encoder.stderr.read().decode(errors="replace")
    if encoder.wait() != 0:
        _fail(job, "ffmpeg failed while encoding the video.", enc_err)
        return
    if done == 0:
        _fail(job, "No frames could be read from that video.")
        return

    job.total_frames = done
    job.done_frames = done

    # 5. Metadata ------------------------------------------------------------
    out_meta = probe(output_path)
    out_meta["size_bytes"] = output_path.stat().st_size
    out_meta["work_resolution"] = f"{ww}x{wh}"  # what the AI actually processed
    job.output_meta = out_meta
    job.output_path = output_path


PREVIEW_MAX_SIDE = 1920  # cap preview size so 4K frames stay snappy in the slider


def _write_preview(job: Job, before_img: "Image.Image", after_img: "Image.Image") -> None:
    """Save a before/after JPEG pair (from PIL RGB images) for the slider."""
    try:
        def _downsized(img):
            longest = max(img.size)
            if longest > PREVIEW_MAX_SIDE:
                r = PREVIEW_MAX_SIDE / longest
                img = img.resize((round(img.width * r), round(img.height * r)),
                                 Image.LANCZOS)
            return img
        before_jpg = job.dir / "preview_before.jpg"
        after_jpg = job.dir / "preview_after.jpg"
        _downsized(before_img).save(before_jpg, "JPEG", quality=90)
        _downsized(after_img).save(after_jpg, "JPEG", quality=90)
        job.preview_before = before_jpg
        job.preview_after = after_jpg
    except Exception:  # noqa: BLE001 - preview is best-effort, never fatal
        pass


def _fail(job: Job, message: str, detail: str = "") -> None:
    job.status = ERROR
    job.error = message
    if detail:
        # Keep a trimmed detail for the server log; UI shows the friendly message.
        print(f"[job {job.id}] {message}\n{detail[-1500:]}")
