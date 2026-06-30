"""Demucs model weight management.

On first run the weights (~80 MB) are not present. This module provides:
  - is_model_cached()   — quick check using a local flag file
  - ModelDownloadWorker — QThread that downloads weights with progress signals
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


# After a successful download we write this flag file so we never need to
# inspect torch's internal cache layout (which varies by platform and version).
_FLAG_DIR  = Path.home() / ".rehearsalroom"
_FLAG_FILE = _FLAG_DIR / "model_ready"


def is_model_cached(model_name: str = "htdemucs") -> bool:
    """Return True if the model has been successfully downloaded before."""
    return (_FLAG_DIR / f"model_ready_{model_name}").exists()


def _mark_model_cached(model_name: str = "htdemucs") -> None:
    _FLAG_DIR.mkdir(parents=True, exist_ok=True)
    (_FLAG_DIR / f"model_ready_{model_name}").touch()


class ModelDownloadWorker(QThread):
    """Download Demucs model weights in a background thread.

    Emits:
        progress(int, str)  — percent (0-100) and status message
        finished()          — weights are ready
        error(str)          — something went wrong
    """

    progress = Signal(int, str)
    finished = Signal()
    error    = Signal(str)

    def __init__(self, model_name: str = "htdemucs"):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            import torch.hub
            import tqdm as _tqdm_mod
            try:
                import tqdm.auto as _tqdm_auto
            except Exception:
                _tqdm_auto = None

            # The weights are fetched by torch.hub.download_url_to_file, which
            # uses the `tqdm` bound in the torch.hub module. torch.hub does
            # `from tqdm import tqdm` AT IMPORT TIME, so torch.hub.tqdm is the
            # real consumer here — patching only tqdm.tqdm (a different binding)
            # leaves the download bar unhooked and the progress bar never moves.
            # Patch every name the download path might use across torch/tqdm
            # versions and restore them afterwards.
            _base = getattr(torch.hub, "tqdm", None) or _tqdm_mod.tqdm
            _emit = lambda pct, msg: self.progress.emit(pct, msg)

            class _ProgressTqdm(_base):
                def update(self, n=1):
                    result = super().update(n)
                    if self.total:
                        frac = min(1.0, self.n / self.total)
                        _emit(int(frac * 95), f"Downloading model… {int(frac * 100)}%")
                    return result

            self.progress.emit(1, "Preparing model download…")
            targets = [(_tqdm_mod, "tqdm"), (torch.hub, "tqdm")]
            if _tqdm_auto is not None:
                targets.append((_tqdm_auto, "tqdm"))
            saved = [(mod, attr, getattr(mod, attr, None)) for mod, attr in targets]
            for mod, attr in targets:
                setattr(mod, attr, _ProgressTqdm)
            try:
                from demucs.pretrained import get_model
                model = get_model(self.model_name)
                _ = model  # ensure fully loaded
            finally:
                for mod, attr, old in saved:
                    if old is not None:
                        setattr(mod, attr, old)

            self.progress.emit(100, "Model ready.")
            _mark_model_cached(self.model_name)
            self.finished.emit()

        except Exception as exc:
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
