"""End-to-end test for the ClearView pipeline.

Generates a short synthetic clip *with an audio track* using ffmpeg, runs it
through the real upscaling pipeline at 2x with the fast model, and asserts that
the output exists, has the expected (doubled) resolution, and still carries an
audio stream.

Run with:  python -m pytest test_e2e.py -s      (or)   python test_e2e.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import upscaler
from jobs import DONE, Job

SRC_W, SRC_H = 320, 180
DURATION = 2  # seconds
FPS = 24


def _make_test_clip(path: Path) -> None:
    """A 2s 320x180 test video with a synthetic audio tone."""
    cmd = [
        upscaler.FFMPEG,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={SRC_W}x{SRC_H}:rate={FPS}:duration={DURATION}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={DURATION}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"failed to build test clip:\n{res.stderr}"


def test_pipeline_2x_fast_model():
    # Preflight: the pipeline needs ffmpeg + ffprobe.
    missing = upscaler.check_tools()
    assert not missing, f"required tools missing: {missing}"

    work = Path(tempfile.mkdtemp(prefix="clearview_test_"))
    try:
        job_dir = work / "job"
        job_dir.mkdir()
        input_path = job_dir / "input.mp4"
        _make_test_clip(input_path)

        job = Job(
            id="test",
            dir=job_dir,
            input_path=input_path,
            filename="input.mp4",
            model="realesr-animevideov3",
            scale=2,
        )

        upscaler.process(job)

        # Pipeline succeeded.
        assert job.status != "error", f"pipeline errored: {job.error}"
        assert job.output_path is not None and job.output_path.exists(), "no output file"

        # Output metadata: resolution is doubled and audio survived.
        out = upscaler.probe(job.output_path)
        assert out["width"] == SRC_W * 2, f"expected width {SRC_W*2}, got {out['width']}"
        assert out["height"] == SRC_H * 2, f"expected height {SRC_H*2}, got {out['height']}"
        assert out["has_audio"], "output lost its audio track"

        # Preview pair was produced mid-job.
        assert job.preview_before and job.preview_before.exists(), "missing before preview"
        assert job.preview_after and job.preview_after.exists(), "missing after preview"

        # Frame folders were cleaned up.
        assert not (job_dir / "frames_in").exists(), "frames_in not cleaned up"
        assert not (job_dir / "frames_out").exists(), "frames_out not cleaned up"

        print(
            f"\nOK: {out['resolution']} output, "
            f"{out['size_bytes']/1024:.0f} KB, audio={out['audio_codec']}, "
            f"cpu={upscaler.running_on_cpu()}"
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_image_pipeline_4x():
    """Upscale a still image 4x and assert the output is a valid, larger PNG."""
    from PIL import Image

    work = Path(tempfile.mkdtemp(prefix="clearview_img_test_"))
    try:
        job_dir = work / "job"
        job_dir.mkdir()
        input_path = job_dir / "input.png"
        Image.new("RGB", (160, 120), (90, 140, 200)).save(input_path)

        job = Job(
            id="imgtest",
            dir=job_dir,
            input_path=input_path,
            filename="input.png",
            model="realesr-animevideov3",
            scale=4,
            kind="image",
        )

        upscaler.process(job)

        assert job.status != "error", f"image pipeline errored: {job.error}"
        assert job.output_path and job.output_path.exists(), "no output image"
        assert job.output_path.suffix == ".png", "output should be a PNG"
        with Image.open(job.output_path) as out:
            assert out.size == (640, 480), f"expected 640x480, got {out.size}"
        assert job.preview_before and job.preview_before.exists(), "missing before preview"
        assert job.preview_after and job.preview_after.exists(), "missing after preview"
        print(f"\nOK (image): {job.output_meta['resolution']} PNG, "
              f"{job.output_meta['size_bytes']/1024:.0f} KB")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    test_pipeline_2x_fast_model()
    test_image_pipeline_4x()
    print("PASSED")
