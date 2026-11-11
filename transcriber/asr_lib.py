#!/usr/bin/env python3
"""GLM-ASR transcription via Zhipu BigModel (adapted from Allen's HAL lane).

API contract (docs.bigmodel.cn): multipart POST, chat-completion-style
response with the transcript in choices[0].message.content. Per-call caps:
file <= 25 MB, audio <= 30 s — longer audio is re-encoded to 16k mono wav and
chunked into 29 s segments via ffmpeg (also handles opus-in-ogg voice notes).

Cost: ~0.06 yuan per minute, billed to the BigModel wallet behind
ZHIPU_API_KEY. Estimates append to output/cost_YYYY-MM-DD.log.

Setup: copy .env.example to .env and set ZHIPU_API_KEY. ffmpeg must be on
PATH or placed in the ffmpeg/ folder next to this file (needed for anything
that is not a short wav).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

ASR_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
ASR_MODEL_DEFAULT = "glm-asr-2512"
ASR_PRICE_PER_MIN = 0.06  # yuan; billed to the BigModel wallet
ASR_CHUNK_SECONDS = 29
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
# Optional bundled ffmpeg: drop ffmpeg.exe + ffprobe.exe into ./ffmpeg/ if
# you don't want to install ffmpeg system-wide. PATH is the fallback.
FFMPEG_DIR = Path(__file__).resolve().parent / "ffmpeg"
_WHICH_PATH = (
    os.pathsep.join([str(FFMPEG_DIR), os.environ.get("PATH", "")])
    if FFMPEG_DIR.is_dir() else None
)


def _which_ff(binary: str):
    if _WHICH_PATH:
        found = shutil.which(binary, path=_WHICH_PATH)
        if found:
            return found
    return shutil.which(binary)


def audio_duration(path: Path) -> float | None:
    """Audio length in seconds: wave module for wav, ffprobe otherwise."""
    if path.suffix.lower() == ".wav":
        try:
            import wave
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except Exception:
            return None
    ffprobe = _which_ff("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _log_cost(duration: float, segments: int, cost: float, lane: str) -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_path = OUTPUT_DIR / f"cost_{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now().isoformat()} | {lane} | {duration:.0f}s/{segments}seg | "
                     f"est {cost:.4f}yuan\n")
    except OSError:
        pass


def asr_segment(seg_path: Path, model: str, api_key: str) -> str:
    """Push one <= 30 s segment through the transcriptions endpoint."""
    import requests
    with open(seg_path, "rb") as f:
        r = requests.post(
            ASR_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": model, "stream": "false"},
            files={"file": (seg_path.name, f)},
            timeout=45,
        )
    if r.status_code != 200:
        raise RuntimeError(f"ASR HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    try:
        return (j["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return (j.get("text") or "").strip()


def transcribe(path: str | Path, model: str = ASR_MODEL_DEFAULT,
               max_segments: int | None = None, api_key: str | None = None,
               lane: str = "asr") -> dict:
    """Transcribe an audio file via GLM-ASR. Returns
    {transcript, duration_s, segments, cost_yuan_est, model}.

    max_segments caps the chunk count; None = no cap. Non-wav input is
    re-encoded to 16k mono wav and segmented (29 s) via ffmpeg; a short
    non-wav is single-re-encoded for codec safety. Without ffmpeg, only a
    short wav can go through — the API's own 30 s cap produces a clean error
    otherwise.
    """
    import time

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    key = api_key or os.getenv("ZHIPU_API_KEY")
    if not key:
        raise RuntimeError(
            "ZHIPU_API_KEY not set — copy .env.example to .env and paste your key"
        )

    duration = audio_duration(file_path) or 0.0
    is_short_wav = (
        file_path.suffix.lower() == ".wav"
        and 0 < duration <= ASR_CHUNK_SECONDS
    )
    seg_paths: list[Path] = [file_path]  # default: send the file as-is
    if not is_short_wav:
        ff = _which_ff("ffmpeg")
        if ff:
            tdir = Path(tempfile.mkdtemp(prefix="asr_"))
            try:
                if 0 < duration <= ASR_CHUNK_SECONDS:
                    # short non-wav: single re-encode for codec safety
                    seg = tdir / "seg_000.wav"
                    subprocess.run(
                        [ff, "-y", "-i", str(file_path), "-ac", "1", "-ar", "16000", str(seg)],
                        capture_output=True, timeout=120,
                    )
                    seg_paths = [seg]
                else:
                    subprocess.run(
                        [ff, "-y", "-i", str(file_path), "-ac", "1", "-ar", "16000",
                         "-f", "segment", "-segment_time", str(ASR_CHUNK_SECONDS),
                         str(tdir / "seg_%03d.wav")],
                        capture_output=True, timeout=600,
                    )
                    seg_paths = sorted(tdir.glob("seg_*.wav"))
            except subprocess.TimeoutExpired:
                shutil.rmtree(tdir, ignore_errors=True)
                raise RuntimeError("ffmpeg re-encode timed out")
        # no ffmpeg and not short-wav: send as-is; the API's own 30 s cap
        # produces a clean error if the audio is too long.

    if max_segments is not None and len(seg_paths) > max_segments:
        for seg in seg_paths:
            if seg != file_path:
                shutil.rmtree(seg.parent, ignore_errors=True)
                break
        raise RuntimeError(
            f"audio too long: {len(seg_paths)} chunks > "
            f"{max_segments * ASR_CHUNK_SECONDS}s cap per call — split the file first"
        )

    texts: list[str] = []
    try:
        for i, seg in enumerate(seg_paths):
            if i:
                time.sleep(0.2)
            texts.append(asr_segment(seg, model, key))
    finally:
        for seg in seg_paths:
            if seg != file_path:
                shutil.rmtree(seg.parent, ignore_errors=True)
                break

    transcript = " ".join(t for t in texts if t)
    billed_min = (duration / 60.0) if duration else (len(seg_paths) * ASR_CHUNK_SECONDS / 60.0)
    cost = billed_min * ASR_PRICE_PER_MIN
    _log_cost(duration, len(seg_paths), cost, lane)

    return {
        "transcript": transcript,
        "duration_s": round(duration, 1),
        "segments": len(seg_paths),
        "cost_yuan_est": round(cost, 4),
        "model": model,
    }
