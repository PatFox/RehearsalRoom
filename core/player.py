"""Multi-track audio player with chunked, pitch-preserving tempo control.

Architecture
------------
Audio is never stretched in full. Instead the player maintains a rolling queue of
short pre-processed chunks (default 2 s each). The fill thread always keeps a few
chunks ahead of the playhead. When the user changes tempo:

  1. The queue is cleared instantly.
  2. The fill thread re-stretches from the current position — the first chunk is
     ready in ~60 ms (2 s of audio through rubberband at ~34× realtime).
  3. Playback resumes with pitch-correct audio within one chunk boundary.

While the first replacement chunk is being produced (≈ 60 ms) the callback reads
from whatever stretched audio is still in the queue. If the queue empties before
the first new chunk arrives (only possible for very large tempo jumps), silence is
output for ≤ one callback period (~20 ms at 2048-frame blocks).
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

# ── tuneable constants ──────────────────────────────────────────────────────
CHUNK_SAMPLES   = 88_200   # 2 s at 44 100 Hz
CONTEXT_SAMPLES = 4_096    # pre-context fed to rubberband to avoid boundary clicks
QUEUE_DEPTH     = 4        # chunks kept ahead of playhead
# ────────────────────────────────────────────────────────────────────────────


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


def _semitones_to_ratio(semitones: float) -> float:
    return 2.0 ** (semitones / 12.0)


def _rubberband_chunk(data: np.ndarray, sr: int, rate: float,
                      pitch: float = 0.0) -> np.ndarray:
    """Time-stretch by *rate* and pitch-shift by *pitch* semitones (independent)
    using ffmpeg's rubberband filter. *data* is [samples, channels]."""
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV")
    af = f"rubberband=tempo={rate:.6f}:pitch={_semitones_to_ratio(pitch):.6f}"
    _no_window = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    proc = subprocess.run(
        [_ffmpeg_exe(), "-y",
         "-f", "wav", "-i", "pipe:0",
         "-af", af,
         "-f", "wav", "pipe:1"],
        input=buf.getvalue(), capture_output=True, **_no_window,
    )
    if proc.returncode != 0 or not proc.stdout:
        # Fallback: simple resampling for tempo only (pitch shift unavailable
        # without rubberband — shouldn't normally happen).
        n_out = int(len(data) / rate)
        idx   = np.linspace(0, len(data) - 1, n_out)
        i0    = np.clip(idx.astype(np.int32), 0, len(data) - 2)
        frac  = (idx - i0)[:, np.newaxis]
        return (data[i0] * (1.0 - frac) + data[i0 + 1] * frac).astype(np.float32)
    result, _ = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)
    if result.shape[1] == 1:
        result = np.repeat(result, 2, axis=1)
    return result.astype(np.float32)


def _stretch_chunk(
    stems_slice: dict[str, np.ndarray],
    sr: int,
    rate: float,
    context_len: int,
    pitch: float = 0.0,
) -> dict[str, np.ndarray]:
    """Stretch/pitch-shift one chunk for all stems in parallel, then trim the
    context prefix. Pitch doesn't change length, so the trim depends on rate only."""
    if abs(rate - 1.0) < 0.005 and abs(pitch) < 1e-6:
        return {sid: d[context_len:].copy() for sid, d in stems_slice.items()}

    def _one(sid: str, data: np.ndarray) -> tuple[str, np.ndarray]:
        out  = _rubberband_chunk(data, sr, rate, pitch)
        trim = min(int(context_len / rate), max(0, len(out) - 1))
        return sid, out[trim:]

    results: dict[str, np.ndarray] = {}
    with ThreadPoolExecutor(max_workers=len(stems_slice)) as ex:
        futs = {ex.submit(_one, sid, d): sid for sid, d in stems_slice.items()}
        for f in as_completed(futs):
            sid, out = f.result()
            results[sid] = out
    return results


class StemPlayer:
    """Plays multiple stems simultaneously with per-stem volume/mute/solo
    and near-instant pitch-preserving tempo control via chunked rubberband.

    Threading contract
    ------------------
    Three threads touch this object:
      * UI thread        — all public methods (load/play/seek/set_*…)
      * fill thread      — `_fill_loop` (produces stretched chunks)
      * audio callback   — `_callback` (sounddevice's thread, consumes chunks)

    `_queue_lock` guards: `_queue`, `_chunk_off`, `_src_pos`, `_fill_pos`,
    `_fill_gen`. The callback snapshots state under the lock and re-validates
    before advancing, so a seek/tempo-change mid-callback is safe.
    `_volumes`/`_mutes`/`_solos`/`_master`/`_playing` are read without the
    lock — single writer (UI thread), atomic reads, momentary staleness is
    acceptable for mixing decisions."""

    def __init__(self):
        self._stems:   dict[str, np.ndarray] = {}   # original loaded audio [samples, ch]
        self._sr:      int   = 44_100
        self._max_src: int   = 0                    # length of original audio in samples
        self._volumes: dict[str, float] = {}
        self._mutes:   dict[str, bool]  = {}
        self._solos:   dict[str, bool]  = {}
        self._master:  float = 1.0
        self._playing: bool  = False

        # Tempo — only write from main thread; read from both callback and fill thread
        self._tempo:        float = 1.0   # rate in use by current queue contents
        self._target_tempo: float = 1.0   # rate requested by UI
        # Pitch shift in semitones (independent of tempo). Same threading rules.
        self._pitch:        float = 0.0
        self._target_pitch: float = 0.0

        # Source position (in original-audio samples)
        #   _src_pos   — position the callback is currently at
        #   _fill_pos  — position the fill thread will start the next chunk from
        self._src_pos:  int = 0
        self._fill_pos: int = 0

        # Chunk queue: each entry is (src_start, {stem_id: np.ndarray})
        # src_start is the original-audio position at the start of that chunk.
        self._queue:      deque = deque()
        self._queue_lock: threading.Lock = threading.Lock()
        self._chunk_off:  int = 0    # playback offset inside the front chunk (stretched samples)
        # Generation counter — bumped on load/seek/tempo-change so a fill
        # thread that was mid-stretch can't append a stale chunk afterwards.
        self._fill_gen:   int = 0

        # Fill thread
        self._fill_event: threading.Event = threading.Event()
        self._stop_fill:  bool  = False
        self._fill_thread: Optional[threading.Thread] = None

        self._stream = None

        # UI callbacks
        self.on_stretch_started: Optional[Callable] = None
        self.on_stretch_done:    Optional[Callable] = None

    # ──────────────────────────────────────────────────────────── public API ──

    def load(self, stem_paths: dict[str, Path]) -> None:
        """Load stem FLAC/WAV files and start the fill thread."""
        self.stop()
        self._stems.clear()
        self._volumes.clear()
        self._mutes.clear()
        self._solos.clear()

        for stem_id, path in stem_paths.items():
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            self._stems[stem_id] = data
            self._sr      = sr
            self._volumes[stem_id] = 1.0
            self._mutes[stem_id]   = False
            self._solos[stem_id]   = False

        self._max_src = max((len(d) for d in self._stems.values()), default=0)
        self._src_pos  = 0
        self._fill_pos = 0
        self._chunk_off = 0
        self._tempo        = 1.0
        self._target_tempo = 1.0
        self._pitch        = 0.0
        self._target_pitch = 0.0
        with self._queue_lock:
            self._queue.clear()
            self._fill_gen += 1
        self._start_fill_thread()

    def play(self) -> None:
        import sounddevice as sd
        if self._playing or not self._stems:
            return
        # A stream that finished (CallbackStop at end-of-track) can't be
        # reliably restarted with start() on all backends — on Windows it
        # returns without error yet produces no audio. So always (re)create a
        # fresh stream whenever there isn't an active one.
        if self._stream is not None and not self._stream.active:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=2,
                dtype="float32",
                blocksize=1024,
                callback=self._callback,
                finished_callback=self._on_finished,
            )
        self._playing = True
        self._stream.start()

    def pause(self) -> None:
        self._playing = False
        # Stop the stream so the callback isn't burning CPU outputting
        # silence and the audio device is released while paused.
        if self._stream and self._stream.active:
            self._stream.stop()

    def stop(self) -> None:
        self._playing = False
        if self._stream and self._stream.active:
            self._stream.stop()
            self._stream.close()
        self._stream = None
        self._stop_fill_thread()

    def seek(self, ms: float) -> None:
        """Seek to *ms* milliseconds in the original song timeline."""
        target_src = max(0, min(int(ms / 1000.0 * self._sr), self._max_src - 1))
        with self._queue_lock:
            self._queue.clear()
            self._src_pos  = target_src
            self._fill_pos = target_src
            self._chunk_off = 0
            self._fill_gen += 1
        self._fill_event.set()

    def position_ms(self) -> float:
        """Current playback position in song-time milliseconds."""
        return self._src_pos / max(1, self._sr) * 1000.0

    def set_volume(self, stem_id: str, vol: float) -> None:
        self._volumes[stem_id] = max(0.0, min(1.5, vol))

    def set_mute(self, stem_id: str, muted: bool) -> None:
        self._mutes[stem_id] = muted

    def set_solo(self, stem_id: str, solo: bool) -> None:
        self._solos[stem_id] = solo

    def set_master_volume(self, vol: float) -> None:
        self._master = max(0.0, min(2.0, vol))

    def set_tempo(self, rate: float) -> None:
        """Change playback speed.  Takes effect within ~60 ms (one chunk)."""
        rate = max(0.25, min(4.0, rate))
        self._target_tempo = rate
        # Clear queue so fill thread immediately starts producing chunks at new rate
        with self._queue_lock:
            self._fill_pos = self._src_pos   # re-fill from current position
            self._queue.clear()
            self._chunk_off = 0
            self._fill_gen += 1
        self._fill_event.set()
        if self.on_stretch_started:
            self.on_stretch_started()

    def set_pitch(self, semitones: float) -> None:
        """Shift pitch by *semitones* without changing speed. Takes effect
        within ~one chunk, just like set_tempo()."""
        semitones = max(-12.0, min(12.0, semitones))
        self._target_pitch = semitones
        with self._queue_lock:
            self._fill_pos = self._src_pos   # re-fill from current position
            self._queue.clear()
            self._chunk_off = 0
            self._fill_gen += 1
        self._fill_event.set()
        if self.on_stretch_started:
            self.on_stretch_started()

    def has_audio(self) -> bool:
        return bool(self._stems)

    def stem_ids(self) -> list:
        return list(self._stems.keys())

    def render_current_mix(self, out_path) -> None:
        """Render the current mix (volumes, mute/solo, master, tempo) to WAV.

        Mirrors the mixing the realtime callback does, applied to the whole
        track in one pass — used by the 'Export · Current' action.
        """
        from pathlib import Path
        if not self._stems:
            raise RuntimeError("No audio loaded.")

        # The 'current mix' is the separated stems only — never the embedded
        # original, regardless of its mute/solo state in the UI.
        stems = {sid: d for sid, d in self._stems.items() if sid != "original"}
        any_solo = any(self._solos.get(sid) for sid in stems)
        mix = np.zeros((self._max_src, 2), dtype=np.float32)
        for sid, data in stems.items():
            if any_solo and not self._solos.get(sid):
                continue
            if not any_solo and self._mutes.get(sid, False):
                continue
            vol = self._volumes.get(sid, 1.0)
            mix[: len(data)] += data * vol

        mix *= self._master
        np.clip(mix, -1.0, 1.0, out=mix)

        # Apply the current speed and pitch in one rubberband pass.
        rate  = self._target_tempo
        pitch = self._target_pitch
        if abs(rate - 1.0) > 0.005 or abs(pitch) > 1e-6:
            mix = _rubberband_chunk(mix, self._sr, rate, pitch)
            np.clip(mix, -1.0, 1.0, out=mix)

        sf.write(str(Path(out_path)), mix, self._sr)

    # ────────────────────────────────────────────────── waveform / metadata ──

    def waveform_data(self, stem_id: str, n_buckets: int = 320) -> list[float]:
        data = self._stems.get(stem_id)
        if data is None or len(data) == 0:
            return [0.0] * n_buckets
        mono = data.mean(axis=1)
        spb  = len(mono) / n_buckets
        buckets = np.array([
            np.sqrt(np.mean(mono[int(i * spb): max(int(i * spb) + 1, int((i + 1) * spb))] ** 2))
            for i in range(n_buckets)
        ], dtype=np.float32)
        p98 = float(np.percentile(buckets, 98))
        if p98 < 0.003:
            return [0.0] * n_buckets
        return [max(0.0, min(1.0, float(b) / p98)) for b in buckets]

    # ────────────────────────────────────────────────────── fill thread ──────

    def _start_fill_thread(self) -> None:
        self._stop_fill = False
        self._fill_event.set()
        t = threading.Thread(target=self._fill_loop, daemon=True, name="chunk-fill")
        self._fill_thread = t
        t.start()

    def _stop_fill_thread(self) -> None:
        self._stop_fill = True
        self._fill_event.set()
        if self._fill_thread and self._fill_thread.is_alive():
            self._fill_thread.join(timeout=2.0)
        self._fill_thread = None

    def _fill_loop(self) -> None:
        """Continuously keeps the chunk queue filled ahead of the playhead."""
        me = threading.current_thread()
        # `self._fill_thread is me` lets a zombie thread from a previous
        # load() (whose join timed out) exit instead of competing with the
        # replacement thread.
        while not self._stop_fill and self._fill_thread is me:
            self._fill_event.wait()
            self._fill_event.clear()
            if self._stop_fill or self._fill_thread is not me:
                break

            while not self._stop_fill and self._fill_thread is me:
                with self._queue_lock:
                    queue_depth = len(self._queue)
                    fill_pos    = self._fill_pos
                    rate        = self._target_tempo
                    pitch       = self._target_pitch
                    gen         = self._fill_gen

                if queue_depth >= QUEUE_DEPTH:
                    break   # queue is full — wait for callback to consume a chunk
                if fill_pos >= self._max_src:
                    break   # reached end of audio

                # Slice with context prefix
                ctx_start = max(0, fill_pos - CONTEXT_SAMPLES)
                ctx_len   = fill_pos - ctx_start
                end       = min(fill_pos + CHUNK_SAMPLES, self._max_src)

                stems_slice = {
                    sid: data[ctx_start:end]
                    for sid, data in self._stems.items()
                }

                # Stretch / pitch-shift
                stretched = _stretch_chunk(stems_slice, self._sr, rate, ctx_len, pitch)

                # Check if a load/seek/tempo/pitch-change invalidated this work
                with self._queue_lock:
                    if self._fill_gen == gen and self._fill_pos == fill_pos:
                        self._queue.append((fill_pos, stretched))
                        self._fill_pos = end
                        self._tempo    = rate
                        self._pitch    = pitch

                # Signal UI that the first chunk is ready (hides the spinner)
                if self.on_stretch_done:
                    self.on_stretch_done()

    # ─────────────────────────────────────────────── sounddevice callback ───

    def _callback(self, outdata: np.ndarray, frames: int, time, status) -> None:
        import sounddevice as sd
        out      = np.zeros((frames, 2), dtype=np.float32)
        any_solo = any(self._solos.values())
        written  = 0

        while written < frames and self._playing:
            # Snapshot the front chunk and our offset under the lock so a
            # concurrent seek/tempo-change can't shift state mid-read.
            with self._queue_lock:
                if not self._queue:
                    # Queue empty — fill thread is catching up; output silence
                    break
                src_start, chunks = self._queue[0]
                chunk_off = self._chunk_off

            # Determine how many frames from this chunk we can use
            chunk_len   = len(next(iter(chunks.values())))
            remaining   = chunk_len - chunk_off
            to_read     = min(frames - written, remaining)

            for stem_id, chunk in chunks.items():
                if any_solo and not self._solos.get(stem_id):
                    continue
                if not any_solo and self._mutes.get(stem_id, False):
                    continue
                vol = self._volumes.get(stem_id, 1.0)
                sl  = chunk[chunk_off: chunk_off + to_read]
                out[written: written + len(sl)] += sl * vol

            written += to_read

            # Commit the advance only if no seek replaced the front chunk
            # while we were mixing; otherwise restart from the new state.
            rate = self._tempo if abs(self._tempo) > 1e-6 else 1.0
            with self._queue_lock:
                if not (self._queue and self._queue[0][0] == src_start):
                    break   # seek/tempo-change happened mid-mix
                self._chunk_off = chunk_off + to_read
                self._src_pos   = src_start + int(self._chunk_off * rate)
                if self._chunk_off >= chunk_len:
                    self._queue.popleft()
                    self._chunk_off = 0
                    self._fill_event.set()   # wake fill thread

        np.clip(out * self._master, -1.0, 1.0, out=out)
        outdata[:] = out

        if self._src_pos >= self._max_src:
            self._playing = False
            raise sd.CallbackStop()

    def _on_finished(self) -> None:
        self._playing = False
