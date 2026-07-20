"""Job state and a single-worker background queue for ClearView.

One job is processed at a time; the rest wait in a FIFO queue. All job state
lives in memory (this is a local, single-user app), keyed by job id. The worker
thread pulls jobs and hands them to the upscaler pipeline.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Callable, Optional


# ---- Status constants -------------------------------------------------------

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    """Everything we track about a single upscale request."""

    id: str
    dir: Path
    input_path: Path
    filename: str
    model: str
    scale: int
    kind: str = "video"   # "video" or "image"
    max_input_h: int = 0  # cap the AI working resolution (0 = full source); the
    #                       dominant speed lever, since cost scales with input px
    #                       (video only — images always run at full resolution)

    status: str = QUEUED
    error: Optional[str] = None

    # Progress
    total_frames: int = 0
    done_frames: int = 0
    seconds_per_frame: float = 0.0  # rolling average, for ETA

    # Metadata (filled after probing / encoding)
    input_meta: dict = field(default_factory=dict)
    output_meta: dict = field(default_factory=dict)

    # Artifacts
    output_path: Optional[Path] = None
    preview_before: Optional[Path] = None
    preview_after: Optional[Path] = None

    created_at: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        """Completion as a 0-100 percentage of frames upscaled."""
        if self.total_frames <= 0:
            return 0.0
        return round(100.0 * self.done_frames / self.total_frames, 1)

    @property
    def eta_seconds(self) -> Optional[float]:
        """Estimated seconds remaining from the rolling per-frame average."""
        if self.status != PROCESSING or self.seconds_per_frame <= 0:
            return None
        remaining = max(self.total_frames - self.done_frames, 0)
        return round(remaining * self.seconds_per_frame, 1)

    def to_public_dict(self) -> dict:
        """The shape the frontend polls for."""
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "progress": self.progress,
            "done_frames": self.done_frames,
            "total_frames": self.total_frames,
            "eta_seconds": self.eta_seconds,
            "model": self.model,
            "scale": self.scale,
            "kind": self.kind,
            "max_input_h": self.max_input_h,
            "filename": self.filename,
            "input_meta": self.input_meta,
            "output_meta": self.output_meta,
            "has_preview": self.preview_before is not None
            and self.preview_after is not None,
            "has_output": self.output_path is not None and self.status == DONE,
        }


class JobManager:
    """Owns the job registry and the single background worker thread."""

    def __init__(self, jobs_root: Path, process_fn: Callable[[Job], None]):
        self.jobs_root = jobs_root
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._process_fn = process_fn
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "Queue[str]" = Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # -- registry ----------------------------------------------------------

    def create(
        self, filename: str, model: str, scale: int,
        max_input_h: int = 0, kind: str = "video",
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.jobs_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            dir=job_dir,
            input_path=job_dir / ("input" + Path(filename).suffix.lower()),
            filename=filename,
            model=model,
            scale=scale,
            kind=kind,
            max_input_h=max_input_h,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                self._queue.task_done()
                continue
            try:
                job.status = PROCESSING
                self._process_fn(job)
                if job.status != ERROR:
                    job.status = DONE
            except Exception as exc:  # noqa: BLE001 - surface any failure to UI
                job.status = ERROR
                if not job.error:
                    job.error = str(exc)
            finally:
                self._queue.task_done()
