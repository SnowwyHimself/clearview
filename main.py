"""ClearView — local AI video upscaling web app.

Run with:  uvicorn main:app --reload
Then open: http://127.0.0.1:8000
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import upscaler
from jobs import DONE, ERROR, JobManager

BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
STATIC_DIR = BASE_DIR / "static"

VALID_MODELS = set(upscaler.MODEL_LABELS)
VALID_SCALES = {2, 4}
JOB_TTL_SECONDS = 24 * 60 * 60  # delete job folders older than 24h on startup

app = FastAPI(title="ClearView")

# The startup tool check result, surfaced to the UI so it can show a clear error.
_missing_tools: list[str] = []


def _cleanup_old_jobs() -> None:
    """Delete whole job folders older than 24h (best-effort)."""
    if not JOBS_DIR.exists():
        return
    cutoff = time.time() - JOB_TTL_SECONDS
    for child in JOBS_DIR.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


@app.on_event("startup")
def _on_startup() -> None:
    global _missing_tools
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _missing_tools = upscaler.check_tools()
    if _missing_tools:
        print(
            "[ClearView] WARNING: missing required tools: "
            + ", ".join(_missing_tools)
            + " — install ffmpeg (which includes ffprobe) to process videos."
        )
    _cleanup_old_jobs()


# A single manager owns the queue + worker thread.
manager = JobManager(JOBS_DIR, upscaler.process)


# --- Routes ------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    """Report tool availability and whether we're running on CPU."""
    return {
        "ok": not _missing_tools,
        "missing_tools": _missing_tools,
        "running_on_cpu": upscaler.running_on_cpu(),
        "models": upscaler.MODEL_LABELS,
        # Seconds of GPU time per input megapixel, keyed "model|scale", for the
        # UI's up-front time estimate.
        "speed": upscaler.SPEED_S_PER_INPUT_MP,
        # Working-resolution presets (max input height): the main speed control.
        "speed_presets": upscaler.SPEED_PRESETS,
    }


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    model: str = Form(...),
    scale: int = Form(...),
    speed: str = Form("fast"),
) -> JSONResponse:
    if _missing_tools:
        raise HTTPException(
            status_code=503,
            detail="ffmpeg/ffprobe not found on the server. "
            "Install ffmpeg and restart ClearView.",
        )
    if model not in VALID_MODELS:
        raise HTTPException(status_code=400, detail="Unknown upscaling model.")
    if scale not in VALID_SCALES:
        raise HTTPException(status_code=400, detail="Scale must be 2 or 4.")
    if speed not in upscaler.SPEED_PRESETS:
        raise HTTPException(status_code=400, detail="Unknown speed preset.")
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file was uploaded.")

    kind = upscaler.kind_for(file.filename, file.content_type or "")
    job = manager.create(
        file.filename, model, scale, upscaler.SPEED_PRESETS[speed], kind
    )

    # Stream the upload to disk, enforcing the size cap as we go.
    size = 0
    try:
        with job.input_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > upscaler.MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(job.dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail="That file is over 500 MB. Please upload something smaller.",
                    )
                out.write(chunk)
    finally:
        await file.close()

    if size == 0:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file was empty.")

    manager.enqueue(job.id)
    return JSONResponse({"job_id": job.id})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_public_dict()


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != DONE or not job.output_path or not job.output_path.exists():
        raise HTTPException(status_code=409, detail="The output isn't ready yet.")
    stem = Path(job.filename).stem
    suffix = job.output_path.suffix  # .mp4 for video, .png for image
    media_type = "image/png" if suffix == ".png" else "video/mp4"
    return FileResponse(
        job.output_path,
        media_type=media_type,
        filename=f"{stem}_clearview_{job.scale}x{suffix}",
    )


@app.get("/api/jobs/{job_id}/preview")
def preview(job_id: str, which: str = "after") -> FileResponse:
    """Serve the before/after preview JPEG (?which=before|after)."""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    path = job.preview_before if which == "before" else job.preview_after
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Preview not ready yet.")
    return FileResponse(path, media_type="image/jpeg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
