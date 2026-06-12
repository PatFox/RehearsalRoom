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
            from demucs.pretrained import get_model
            from demucs.apply import apply_model

            # Convert to WAV if needed (handles mp3, m4a, ogg, etc.)
            audio_path = _ensure_wav(self.audio_path)
            if self.isInterruptionRequested():
                raise _Cancelled

            self.progress.emit(5, "Loading model…")
            model = get_model(self.model_name)
            model.eval()
            if self.isInterruptionRequested():
                raise _Cancelled

            self.progress.emit(15, "Loading audio…")
            data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
            # soundfile → [samples, channels]; Demucs needs [channels, samples]
            wav = torch.from_numpy(data.T)

            # Ensure stereo
            if wav.shape[0] == 1:
                wav = wav.repeat(2, 1)
            elif wav.shape[0] > 2:
                wav = wav[:2]

            # Resample to model sample rate if needed
            wav = _resample(wav, sr, model.samplerate)

            # Normalise (Demucs convention)
            ref = wav.mean(0)
            wav = (wav - ref.mean()) / (ref.std() + 1e-8)
            wav = wav.unsqueeze(0)  # add batch dim → [1, channels, samples]

            self.progress.emit(20, "Separating stems (this may take a few minutes)…")

            # Demucs' apply_model accepts progress=bool and uses tqdm internally.
            # We intercept by temporarily replacing tqdm.tqdm with a subclass that
            # calls our signal on every iteration tick.
            import tqdm as _tqdm_mod
            _orig_tqdm = _tqdm_mod.tqdm
            _emit = lambda pct, msg: self.progress.emit(pct, msg)
            worker = self

            class _SignalTqdm(_orig_tqdm):
                def update(self, n=1):
                    # Cooperative cancel point — fires frequently during the
                    # (slow) separation loop, so a skip/abort takes effect within
                    # a chunk instead of after the whole track.
                    if worker.isInterruptionRequested():
                        raise _Cancelled
                    result = super().update(n)
                    if self.total:
                        frac = min(1.0, self.n / self.total)
                        _emit(20 + int(frac * 70), f"Separating… {int(frac * 100)}%")
                    return result

            _tqdm_mod.tqdm = _SignalTqdm
            try:
                with torch.no_grad():
                    sources = apply_model(model, wav, progress=True, num_workers=0)
            finally:
                _tqdm_mod.tqdm = _orig_tqdm

            self.progress.emit(90, "Saving stems…")

            if self.output_dir is None:
                from core.tempdirs import make_temp_dir
                out_dir = make_temp_dir("sep_")
            else:
                out_dir = Path(self.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)

            stem_paths: dict[str, Path] = {}
            sources = sources[0]  # remove batch dim → [stems, channels, samples]
            for idx, name in enumerate(model.sources):
                stem_wav = sources[idx]  # [channels, samples]
                out_path = out_dir / f"{name}.wav"
                # Write via soundfile — no torchaudio backend involved
                audio_np = stem_wav.cpu().numpy().T  # → [samples, channels]
                sf.write(str(out_path), audio_np, model.samplerate)
                stem_paths[name] = out_path

            if self.isInterruptionRequested():
                raise _Cancelled

            self.progress.emit(100, "Done.")
            self.finished.emit({k: str(v) for k, v in stem_paths.items()})

        except _Cancelled:
            return   # cancelled — exit quietly, emit nothing
        except Exception as exc:
            if self.isInterruptionRequested():
                return   # error caused by teardown during cancel — ignore
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
