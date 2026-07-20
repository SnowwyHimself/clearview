# ClearView — Local AI Video & Image Upscaler

Upload a low-quality **video or image** in your browser and get back an
AI-upscaled version (up to 4K), processed entirely on **your own machine**.
Nothing is sent to the cloud. ClearView uses
[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) via the
`realesrgan-ncnn-py` package, with a FastAPI backend and a single
self-contained web page.

## Features

- **Videos and still images** — drop in an MP4/MOV/WebM… or a PNG/JPG/WebP…
- Drag-and-drop upload with a modern dark UI (no build step, no React)
- Two models: **AnimeVideo v3** (fast, great default for video) and
  **x4plus** (slower, maximum detail)
- **2×** or **4×** scaling, with output capped at 3840×2160 (4K)
- GPU acceleration via Vulkan (NVIDIA / AMD / Intel / Apple), with automatic
  CPU fallback and a clear "running on CPU" warning
- Live before/after comparison slider that appears mid-job, plus final
  side-by-side comparison and a one-click download
- Original audio track is preserved unchanged

## Requirements

- **Python 3.11+**
- **ffmpeg** and **ffprobe** on your `PATH` (a normal ffmpeg install includes
  both). Check with `ffmpeg -version` and `ffprobe -version`.
- For GPU acceleration: up-to-date **Vulkan** drivers. On macOS this works out
  of the box via MoltenVK; on Linux/Windows install your GPU vendor's drivers.
  Without a usable GPU, ClearView still runs on CPU — just much slower.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app
```

Then open <http://127.0.0.1:8000> in your browser.

The first upscale downloads/loads the model into the GPU and may take a moment;
subsequent frames are much faster.

## How it works

1. The upload is saved to `jobs/{job_id}/` and probed with ffprobe. Files over
   **500 MB** or longer than **10 minutes** are rejected with a friendly message.
2. Frames are **streamed as raw pixels** straight from ffmpeg into Real-ESRGAN
   and back into ffmpeg — no intermediate PNG files touch the disk. The decoder
   and the H.264 encoder run **concurrently** with the GPU work, so decoding and
   encoding overlap the upscaling instead of happening one-after-another.
3. Real-ESRGAN upscales each frame; job progress (frames done / total) and a
   rolling ETA are updated as it goes. A before/after preview is emitted from
   the first frame so you see the effect early.
4. ffmpeg encodes to H.264 (`libx264`, `crf 18`, `yuv420p`, `+faststart`) at the
   source fps, scales to the requested factor (capped at 4K), and copies the
   **original audio** back in unchanged.
5. Whole job folders older than 24 hours are deleted on the next startup.

Jobs run **one at a time** on a background worker thread; additional uploads
queue up.

### Images

A still image takes a much simpler path — no ffmpeg, no frame streaming. It's
loaded with Pillow, upscaled once with Real-ESRGAN, resized to the (4K-capped)
target, and saved as a **PNG** for lossless quality. Images always process at
**full source resolution** — the video Speed control is hidden for them, because
downscaling a still before upscaling would visibly degrade it. A 4× upscale of a
typical photo finishes in a second or two.

## Performance — the Speed control is the big lever

Inference cost scales with the **input** resolution, not the output: the network
does its heavy work at the source resolution and the final enlargement is cheap.
So the fastest way to a 4K result is to run the AI at a smaller *working*
resolution and let it upscale from there — which is exactly what Real-ESRGAN is
trained to do, and ideal for the low-quality source this app targets.

The **Speed vs. quality** control does this:

| Preset | AI works at | Speed (1080p source) | Best for |
|--------|-------------|----------------------|----------|
| **Fast** (default) | ~360p | ~7× faster | low-quality clips, quick results |
| **Balanced** | ~540p | ~3× faster | a bit more fidelity |
| **Max** | full source res | baseline (slowest) | already-sharp footage |

Measured on Apple Silicon, a 30-second 1080p clip → 4K takes about **2 min on
Fast, 5 min on Balanced, 15 min on Max**. All three produce a full-resolution
(up to 4K) file; they differ in how much genuine source detail the AI starts from.

Other notes:

- **AnimeVideo v3 (the default model)** is the fast video model. **x4plus is
  ~30× slower** (~5 s/frame) — a max-quality model for **short clips or stills**.
- **Pick the smallest scale that hits your goal.** 1080p → 4K is 2×, not 4×.
- The app shows an **estimated time before you start** and a live ETA during the
  job. Running on CPU instead of GPU is roughly another order of magnitude slower.

The streaming pipeline removes all the I/O overhead; the neural network itself is
the floor, and the Speed control is how you trade a bit of that floor for time.

## Test

End-to-end tests cover both paths: a 2-second 320×180 clip (with audio) run at
2× — asserting the output exists, is 640×360, and still has audio — and a still
image run at 4× — asserting a valid, larger PNG comes out:

```bash
python test_e2e.py
# or, with pytest:
python -m pytest test_e2e.py -s
```

## A note on what AI upscaling can and can't do

AI upscaling **sharpens edges and invents plausible detail** that looks
convincing at higher resolutions. It does **not** recover information that was
never captured in the first place — a blurry face won't become a real,
identifiable one; unreadable text won't become truly legible. Think of it as a
very good, detail-aware enlargement, not a forensic "enhance" button.

## Project layout

```
main.py            FastAPI app + routes, startup tool check & cleanup
upscaler.py        The pipeline: probe → extract → upscale → re-encode
jobs.py            Job state + single-worker background queue
static/index.html  Self-contained dark UI (drag-drop, slider, progress, ETA)
test_e2e.py        End-to-end pipeline test
requirements.txt   Python dependencies
```
