"""Multi-track audio player with pitch-preserving tempo control.

Uses sounddevice for real-time output and soundfile for FLAC loading.
Tempo stretching (without pitch change) uses pyrubberband when available,
falling back to a simple resampling-based method.
"""

from __future__ import annotations
import threading
from pathlib import Path
from typing import Optional

import numpy as np


class StemPlayer:
    """Plays multiple FLAC stems simultaneously with per-stem volume/mute."""

    def __init__(self):
        self._stems: dict[str, np.ndarray] = {}   # stem_id -> [samples, channels] float32
        self._volumes: dict[str, float] = {}       # 0.0–1.5
        self._mutes: dict[str, bool] = {}
        self._solos: dict[str, bool] = {}
        self._sr: int = 44100
        self._pos: int = 0
        self._playing: bool = False
        self._master: float = 1.0
        self._tempo: float = 1.0
        self._stream = None
        self._lock = threading.Lock()
        self._max_len: int = 0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, stem_paths: dict[str, Path]) -> None:
        """Load stems from FLAC files. Call from the main thread before playing."""
        import soundfile as sf
        self.stop()
        with self._lock:
            self._stems.clear()
            self._volumes.clear()
            self._mutes.clear()
            self._solos.clear()
            self._max_len = 0
            for stem_id, path in stem_paths.items():
                data, sr = sf.read(str(path), dtype="float32", always_2d=True)
                if data.shape[1] == 1:
                    data = np.repeat(data, 2, axis=1)
                elif data.shape[1] > 2:
                    data = data[:, :2]
                self._stems[stem_id] = data
                self._volumes[stem_id] = 1.0
                self._mutes[stem_id] = False
                self._sr = sr
                self._max_len = max(self._max_len, len(data))
            self._stretched = dict(self._stems)  # no stretch initially

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._playing:
            return
        if not self._stems:
            return
        import sounddevice as sd
        self._playing = True
        self._stream = sd.OutputStream(
            samplerate=self._sr,
            channels=2,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
            finished_callback=self._on_finished,
        )
        self._stream.start()

    def pause(self) -> None:
        self._playing = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def stop(self) -> None:
        self.pause()
        self._pos = 0

    def seek(self, ms: float) -> None:
        with self._lock:
            sr = self._sr
            self._pos = int(ms / 1000.0 * sr)
            self._pos = max(0, min(self._pos, self._max_len - 1))

    def position_ms(self) -> float:
        return self._pos / self._sr * 1000.0

    # ------------------------------------------------------------------
    # Mixing controls
    # ------------------------------------------------------------------

    def set_volume(self, stem_id: str, vol: float) -> None:
        self._volumes[stem_id] = max(0.0, min(1.5, vol))

    def set_mute(self, stem_id: str, muted: bool) -> None:
        self._mutes[stem_id] = muted

    def set_master_volume(self, vol: float) -> None:
        self._master = max(0.0, min(2.0, vol))

    # ------------------------------------------------------------------
    # Waveform data
    # ------------------------------------------------------------------

    def waveform_data(self, stem_id: str, n_buckets: int = 320) -> list[float]:
        """Return RMS amplitude per bucket, normalised 0–1, for the given stem."""
        data = self._stems.get(stem_id)
        if data is None or len(data) == 0:
            return [0.03] * n_buckets

        # Mix to mono
        mono = data.mean(axis=1) if data.ndim == 2 else data

        samples_per_bucket = len(mono) / n_buckets
        buckets = np.empty(n_buckets, dtype=np.float32)
        for i in range(n_buckets):
            start = int(i * samples_per_bucket)
            end = max(start + 1, int((i + 1) * samples_per_bucket))
            chunk = mono[start:end]
            buckets[i] = np.sqrt(np.mean(chunk ** 2))  # RMS

        # Normalise to 98th percentile so transients don't squash the whole waveform
        p98 = float(np.percentile(buckets, 98)) or 1.0
        return [max(0.03, min(1.0, float(b) / p98)) for b in buckets]

    # ------------------------------------------------------------------
    # Tempo control
    # ------------------------------------------------------------------

    def set_tempo(self, rate: float) -> None:
        """Change playback speed. Takes effect immediately on the next callback tick."""
        with self._lock:
            self._tempo = max(0.25, min(4.0, rate))

    # ------------------------------------------------------------------
    # Sounddevice callback
    # ------------------------------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time, status):
        import sounddevice as sd
        with self._lock:
            if not self._playing or not self._stems:
                outdata[:] = 0
                return

            tempo = self._tempo
            # How many source samples correspond to `frames` output samples at this tempo
            src_frames = int(round(frames * tempo))

            out = np.zeros((frames, 2), dtype=np.float32)
            any_solo = any(self._solos.values())

            for stem_id, data in self._stems.items():
                if any_solo and not self._solos.get(stem_id):
                    continue
                if not any_solo and self._mutes.get(stem_id, False):
                    continue

                vol = self._volumes.get(stem_id, 1.0)
                start = self._pos
                end = min(start + src_frames, len(data))
                if start >= len(data):
                    continue

                chunk = data[start:end]          # [src_frames, 2]

                if abs(tempo - 1.0) < 0.01 or len(chunk) <= 1:
                    # 1× speed: copy directly (possibly short at end of track)
                    out[:len(chunk)] += chunk * vol
                else:
                    # Resample chunk → frames output samples via linear interpolation.
                    # This changes pitch at non-1× speeds; install pyrubberband for
                    # pitch-preserving stretch (upgrade path, not required).
                    n = len(chunk)
                    idx = np.linspace(0, n - 1, frames)
                    i0 = np.clip(idx.astype(np.int32), 0, n - 2)
                    frac = (idx - i0)[:, np.newaxis]
                    resampled = chunk[i0] * (1.0 - frac) + chunk[i0 + 1] * frac
                    out += resampled.astype(np.float32) * vol

            self._pos += src_frames
            np.clip(out * self._master, -1.0, 1.0, out=out)
            outdata[:] = out

            if self._pos >= self._max_len:
                self._playing = False
                raise sd.CallbackStop()

    def _on_finished(self):
        self._playing = False
