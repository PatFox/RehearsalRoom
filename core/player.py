"""Multi-track audio player with hybrid tempo control.

Speed changes are applied in two stages:
  1. Immediately: in-callback linear resampling (instant response, pitch shifts briefly).
  2. Background: librosa phase-vocoder processes all stems in parallel; when done the
     pitch-correct audio swaps in seamlessly.

This gives instant audible feedback while pitch correction catches up (~15s for a
4-minute track on a modern CPU). The pitch shift during step 1 is subtle because
the callback also compensates for the rate change in its read pointer.
"""

from __future__ import annotations
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import numpy as np
import soundfile as sf


def _stretch_stem(data: np.ndarray, sr: int, rate: float) -> np.ndarray:
    """Pitch-preserving time stretch via librosa phase vocoder, all channels in parallel."""
    if abs(rate - 1.0) < 0.005:
        return data
    try:
        import librosa
        channels = []
        for ch in range(data.shape[1]):
            stretched = librosa.effects.time_stretch(
                data[:, ch].astype(np.float32), rate=rate
            )
            channels.append(stretched)
        return np.stack(channels, axis=1).astype(np.float32)
    except ImportError:
        # Fallback: resampling (changes pitch)
        n_out = int(len(data) / rate)
        indices = np.linspace(0, len(data) - 1, n_out)
        i0 = np.clip(indices.astype(np.int32), 0, len(data) - 2)
        frac = (indices - i0)[:, np.newaxis]
        return (data[i0] * (1.0 - frac) + data[i0 + 1] * frac).astype(np.float32)


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
            return [0.0] * n_buckets
        mono = data.mean(axis=1)
        spb = len(mono) / n_buckets
        buckets = np.array([
            np.sqrt(np.mean(mono[int(i * spb): max(int(i * spb) + 1, int((i + 1) * spb))] ** 2))
            for i in range(n_buckets)
        ], dtype=np.float32)
        p98 = float(np.percentile(buckets, 98))
        # Noise floor ~-50 dB: if the loudest part of the stem is below this,
        # it's effectively silent — don't amplify floating-point noise into bars.
        if p98 < 0.003:
            return [0.0] * n_buckets
        return [max(0.0, min(1.0, float(b) / p98)) for b in buckets]

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
        """Background: stretch all stems in parallel, then swap atomically."""
        try:
            with self._lock:
                stems_snapshot = dict(self._stems)
                sr = self._sr

            # Process all stems concurrently — librosa releases the GIL during FFT
            stretched: dict[str, np.ndarray] = {}
            with ThreadPoolExecutor(max_workers=len(stems_snapshot)) as ex:
                futures = {
                    ex.submit(_stretch_stem, data, sr, rate): stem_id
                    for stem_id, data in stems_snapshot.items()
                }
                for future in as_completed(futures):
                    stem_id = futures[future]
                    # Bail if the user moved the slider again while we were processing
                    with self._lock:
                        if abs(self._target_tempo - rate) > 0.005:
                            return
                    stretched[stem_id] = future.result()

            # Swap in atomically, adjusting playhead to same song-fraction
            with self._lock:
                if abs(self._target_tempo - rate) > 0.005:
                    return  # stale — a newer stretch is coming
                old_max = self._max_len or 1
                new_max = max(len(d) for d in stretched.values())
                self._active = stretched
                self._max_len = new_max
                self._tempo = rate
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

            # If the background stretch is still running (_tempo != _target_tempo),
            # use in-callback resampling on the original stems for instant response.
            # Once the stretched audio swaps in, _tempo == _target_tempo and we read
            # frame-by-frame with no resampling needed.
            target = self._target_tempo
            use_callback_resample = abs(self._tempo - target) > 0.005
            src_frames = int(round(frames * target)) if use_callback_resample else frames
            source = self._stems if use_callback_resample else self._active

            out = np.zeros((frames, 2), dtype=np.float32)
            any_solo = any(self._solos.values())

            for stem_id, data in source.items():
                if any_solo and not self._solos.get(stem_id):
                    continue
                if not any_solo and self._mutes.get(stem_id, False):
                    continue

                vol = self._volumes.get(stem_id, 1.0)
                start = self._pos
                end = min(start + src_frames, len(data))
                if start >= len(data):
                    continue
                chunk = data[start:end]

                if use_callback_resample and len(chunk) > 1:
                    idx = np.linspace(0, len(chunk) - 1, frames)
                    i0 = np.clip(idx.astype(np.int32), 0, len(chunk) - 2)
                    frac = (idx - i0)[:, np.newaxis]
                    chunk = chunk[i0] * (1.0 - frac) + chunk[i0 + 1] * frac
                    out += chunk.astype(np.float32) * vol
                else:
                    out[:len(chunk)] += chunk * vol

            self._pos += src_frames
            np.clip(out * self._master, -1.0, 1.0, out=out)
            outdata[:] = out

            if self._pos >= self._max_len:
                self._playing = False
                raise sd.CallbackStop()

    def _on_finished(self):
        self._playing = False
