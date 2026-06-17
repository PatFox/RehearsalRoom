"""Demucs stem separation worker (runs in a QThread).

All audio I/O uses soundfile (not torchaudio) to avoid torchaudio's
backend selection issues in newer versions (2.5+).
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from PySide6.QtCore import QThread, Signal


def _ensure_wav(path: Path) -> Path:
    """Convert any audio format to WAV via ffmpeg. Returns path unchanged if already WAV."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        return path

    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    from core.tempdirs import make_temp_file
    out = make_temp_file(suffix=".wav")
    no_window = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    subprocess.run(
        [str(ffmpeg), "-y", "-i", str(path), "-ac", "2", "-ar", "44100", str(out)],
        check=True, capture_output=True, **no_window,
    )
    return out


def _resample(wav: torch.Tensor, orig_sr: int, target_sr: int) -> torch.Tensor:
    """Resample using torchaudio.functional (math only, no backend needed)."""
    if orig_sr == target_sr:
        return wav
    import torchaudio.functional as F
    return F.resample(wav, orig_sr, target_sr)


class _Cancelled(Exception):
    """Raised internally to unwind run() cleanly when the worker is cancelled."""


def separate_audio(audio_path, model_name="htdemucs", output_dir=None,
                   progress=None, should_cancel=None) -> dict:
    """Split *audio_path* into stems with Demucs. Returns {stem_id: wav_path_str}.

    progress(pct:int, msg:str) — optional callback. should_cancel() -> bool —
    optional cooperative-cancel check (raises _Cancelled when True).
    """
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    def _cc():
        if should_cancel and should_cancel():
            raise _Cancelled

    audio_path = _ensure_wav(Path(audio_path))
    _cc()

    _p(5, "Loading model…")
    model = get_model(model_name)
    model.eval()
    _cc()

    _p(15, "Loading audio…")
    data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T)   # [channels, samples]
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    wav = _resample(wav, sr, model.samplerate)

    # Normalise (Demucs convention). Keep mean/std so the output can be
    # de-normalised back to the original level — without this the stems come
    # out scaled by ~1/std (much too loud / clipping).
    ref = wav.mean(0)
    ref_mean = ref.mean()
    ref_std  = ref.std()
    wav = (wav - ref_mean) / (ref_std + 1e-8)
    wav = wav.unsqueeze(0)

    _p(20, "Separating stems (this may take a few minutes)…")

    # Intercept demucs' internal tqdm to surface progress.
    import tqdm as _tqdm_mod
    _orig_tqdm = _tqdm_mod.tqdm

    class _SignalTqdm(_orig_tqdm):
        def update(self, n=1):
            _cc()   # cooperative cancel point, fired frequently
            result = super().update(n)
            if self.total:
                frac = min(1.0, self.n / self.total)
                _p(20 + int(frac * 70), f"Separating… {int(frac * 100)}%")
            return result

    _tqdm_mod.tqdm = _SignalTqdm
    try:
        with torch.no_grad():
            sources = apply_model(model, wav, progress=True, num_workers=0)
    finally:
        _tqdm_mod.tqdm = _orig_tqdm

    _p(90, "Saving stems…")
    if output_dir is None:
        from core.tempdirs import make_temp_dir
        out_dir = make_temp_dir("sep_")
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    stem_paths: dict[str, str] = {}
    sources = sources[0]                       # [stems, channels, samples]
    sources = sources * ref_std + ref_mean     # de-normalise to original level
    for idx, name in enumerate(model.sources):
        out_path = out_dir / f"{name}.wav"
        sf.write(str(out_path), sources[idx].cpu().numpy().T, model.samplerate)
        stem_paths[name] = str(out_path)

    _cc()
    _p(100, "Done.")
    return stem_paths


class SeparatorWorker(QThread):
    progress = Signal(int, str)   # percent, status message
    finished = Signal(dict)       # stem_id -> Path (WAV)
    error = Signal(str)

    def __init__(self, audio_path: Path, model_name: str = "htdemucs",
                 output_dir: Optional[Path] = None):
        super().__init__()
        self.audio_path = Path(audio_path)
        self.model_name = model_name
        self.output_dir = output_dir

    def run(self):
        try:
            stem_paths = separate_audio(
                self.audio_path, self.model_name, self.output_dir,
                progress=lambda pct, msg: self.progress.emit(pct, msg),
                should_cancel=self.isInterruptionRequested,
            )
            self.finished.emit(stem_paths)

        except _Cancelled:
            return   # cancelled — exit quietly, emit nothing
        except Exception as exc:
            if self.isInterruptionRequested():
                return   # error caused by teardown during cancel — ignore
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
