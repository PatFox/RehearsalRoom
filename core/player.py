"""Multi-track audio player with pitch-preserving tempo control.

Tempo changes are processed in a background thread using ffmpeg's `atempo`
filter (SoundTouch-based), so pitch is always preserved. The audio continues
playing at the previous speed while the new version is being computed, then
switches over seamlessly.
"""

from __future__ import annotations
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        found = shutil.which("ffmpeg")
        if found:
            return found
        raise FileNotFoundError("ffmpeg not found. Run: pip install imageio-ffmpeg")


def _stretch_stem(data: np.ndarray, sr: int, rate: float) -> np.ndarray:
    """Pitch-preserving time stretch via ffmpeg atempo filter.
    atempo supports 0.5–2.0; values outside that range are chained automatically.
    """
    if abs(rate - 1.0) < 0.005:
        return data

    # Build atempo filter chain (each stage limited to 0.5–2.0)
    filters = []
    r = rate
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r *= 2.0
    filters.append(f"atempo={r:.6f}")
    filter_str = ",".join(filters)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fin, \
         tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fout:
        in_path, out_path = fin.name, fout.name

    try:
        sf.write(in_path, data, sr)
        subprocess.run(
            [_ffmpeg_exe(), "-y", "-i", in_path,
             "-filter:a", filter_str, out_path],
            check=True, capture_output=True,
        )
        result, _ = sf.read(out_path, dtype="float32", always_2d=True)
        if result.shape[1] == 1:
            result = np.repeat(result, 2, axis=1)
        return result
    finally:
        for p in (in_path, out_path):
            try:
                Path(p).unlink()
            except Exception:
                pass


class StemPlayer:
    """Plays multiple FLAC stems simultaneously with per-stem volume/mute/solo."""

    def __init__(self):
        self._stems: dict[str, np.ndarray] = {}     # original loaded audio
        self._active: dict[str, np.ndarray] = {}    # currently playing (original or stretched)
        self._volumes: dict[str, float] = {}
        self._mutes: dict[str, bool] = {}
        self._solos: dict[str, bool] = {}
        self._sr: int = 44100
        self._pos: int = 0
        self._playing: bool = False
        self._master: float = 1.0
        self._tempo: float = 1.0
        self._target_tempo: float = 1.0             # latest requested rate
        self._stream = None
        self._lock = threading.Lock()
        self._max_len: int = 0
        self._stretch_thread: Optional[threading.Thread] = None

        # Optional callbacks for UI feedback
        self.on_stretch_started: Optional[Callable] = None
        self.on_stretch_done: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, stem_paths: dict[str, Path]) -> None:
        self.stop()
        with self._lock:
            self._stems.clear()
            self._active.clear()
            self._volumes.clear()
            self._mutes.clear()
            self._solos.clear()
            self._max_len = 0
            self._tempo = 1.0
            self._target_tempo = 1.0

            for stem_id, path in stem_paths.items():
                data, sr = sf.read(str(path), dtype="float32", always_2d=True)
                if data.shape[1] == 1:
                    data = np.repeat(data, 2, axis=1)
                elif data.shape[1] > 2:
                    data = data[:, :2]
                self._stems[stem_id] = data
                self._active[stem_id] = data
                self._volumes[stem_id] = 1.0
                self._mutes[stem_id] = False
                self._solos[stem_id] = False
                self._sr = sr
                self._max_len = max(self._max_len, len(data))

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._playing or not self._stems:
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
            # Convert song-time ms → fraction → sample position in active (possibly
            # stretched) audio. Using raw `ms * sr` is only correct for 1× speed;
            # at other speeds the active audio has a different length than the original.
            original_len = max((len(d) for d in self._stems.values()), default=1)
            song_dur_ms = original_len / max(self._sr, 1) * 1000.0
            frac = ms / max(song_dur_ms, 1.0)
            self._pos = max(0, min(int(frac * self._max_len), self._max_len - 1))

    def position_ms(self) -> float:
        # Convert pos in active (possibly stretched) audio back to song-time ms
        active_len = self._max_len or 1
        stem_len = max((len(d) for d in self._stems.values()), default=1)
        frac = self._pos / active_len
        return frac * stem_len / self._sr * 1000.0

    # ------------------------------------------------------------------
    # Mixing
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
        data = self._stems.get(stem_id)
        if data is None or len(data) == 0:
            return [0.03] * n_buckets
        mono = data.mean(axis=1)
        spb = len(mono) / n_buckets
        buckets = np.array([
            np.sqrt(np.mean(mono[int(i * spb): max(int(i * spb) + 1, int((i + 1) * spb))] ** 2))
            for i in range(n_buckets)
        ], dtype=np.float32)
        p98 = float(np.percentile(buckets, 98)) or 1.0
        return [max(0.03, min(1.0, float(b) / p98)) for b in buckets]

    # ------------------------------------------------------------------
    # Tempo — pitch-preserving via ffmpeg atempo, runs in background
    # ------------------------------------------------------------------

    def set_tempo(self, rate: float) -> None:
        rate = max(0.5, min(2.0, rate))
        with self._lock:
            self._target_tempo = rate

        # Cancel any in-progress stretch (it will notice target changed and exit early)
        t = threading.Thread(target=self._stretch_worker, args=(rate,), daemon=True)
        self._stretch_thread = t
        if self.on_stretch_started:
            self.on_stretch_started()
        t.start()

    def _stretch_worker(self, rate: float) -> None:
        """Background: stretch all stems, then swap atomically if rate is still current."""
        try:
            with self._lock:
                stems_snapshot = dict(self._stems)
                sr = self._sr

            stretched: dict[str, np.ndarray] = {}
            for stem_id, data in stems_snapshot.items():
                # Bail early if the user moved the slider again
                with self._lock:
                    if abs(self._target_tempo - rate) > 0.005:
                        return
                stretched[stem_id] = _stretch_stem(data, sr, rate)

            # Swap in atomically, adjusting playhead to same song-fraction
            with self._lock:
                if abs(self._target_tempo - rate) > 0.005:
                    return  # stale — a newer stretch is coming
                old_max = self._max_len or 1
                new_max = max(len(d) for d in stretched.values())
                self._active = stretched
                self._max_len = new_max
                self._tempo = rate
                # Keep same fractional position through the song
                self._pos = int(self._pos * new_max / old_max)

        finally:
            if self.on_stretch_done:
                self.on_stretch_done()

    # ------------------------------------------------------------------
    # Sounddevice callback
    # ------------------------------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time, status):
        import sounddevice as sd
        with self._lock:
            if not self._playing or not self._active:
                outdata[:] = 0
                return

            out = np.zeros((frames, 2), dtype=np.float32)
            any_solo = any(self._solos.values())

            for stem_id, data in self._active.items():
                if any_solo and not self._solos.get(stem_id):
                    continue
                if not any_solo and self._mutes.get(stem_id, False):
                    continue
                vol = self._volumes.get(stem_id, 1.0)
                start = self._pos
                end = min(start + frames, len(data))
                if start >= len(data):
                    continue
                chunk = data[start:end]
                out[:len(chunk)] += chunk * vol

            self._pos += frames
            np.clip(out * self._master, -1.0, 1.0, out=out)
            outdata[:] = out

            if self._pos >= self._max_len:
                self._playing = False
                raise sd.CallbackStop()

    def _on_finished(self):
        self._playing = False
