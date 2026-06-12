# Code Review — Rehearsal Room

*Scope: full review of `core/` and `ui/` (~5,800 LOC) focused on stability, performance, and resource management. Every finding below was verified against the source at review time, with file:line references.*

> **Resolution status:** All High (H1–H4), Medium (M1–M7), and Low (L1–L13) findings have been fixed in follow-up commits. Line numbers below refer to the pre-fix code. Only the "Deferred / at-scale" items remain open by design.

---

## Executive summary

The codebase is in good shape architecturally: worker threads are used consistently for heavy work, the recent cooperative-cancellation rework removed the worst threading hazards, and the chunked rubberband playback design in `core/player.py` is genuinely clever. There are **no critical defects** that would corrupt user data or crash the app in normal day-to-day use.

The four highest-impact issues are:

1. **The UI thread freezes during AcoustID lookups** — a network call with **no timeout** runs on the main thread after every separation when an API key is set. Worst realistic case: the app appears hung indefinitely.
2. **Editing track metadata fails if the library is on a different drive than `%TEMP%`** — `update_manifest` builds the new `.stems` in the temp folder and `os.replace`s it across volumes, which raises `OSError` on Windows.
3. **One unhandled exception stalls the whole import queue** — `_on_separation_done` has no try/except; an ffmpeg failure during Opus encoding kills the queue silently (progress stuck, remaining tracks never processed).
4. **Temp files accumulate without bound** — every song open extracts and decodes stems to a new temp dir (~tens of MB each), every YouTube import leaves a download dir, and Windows never cleans `%TEMP%` automatically. Over weeks of use this silently consumes gigabytes.

Overall risk: **medium**. All four top items are small, contained fixes (S effort each except temp cleanup, which is M).

---

## High

### H1. AcoustID fingerprinting blocks the UI thread, with no timeout
- **Where:** `ui/main_window.py:823–827` (called inside `_on_separation_done`, a slot on the main thread); `core/metadata.py:84` (`acoustid.match()` — no timeout parameter).
- **Impact:** When an AcoustID API key is configured and a track has no embedded/YouTube metadata, the app fingerprints the audio (CPU work) and queries the AcoustID web service (network) on the UI thread. Slow or unreachable server → the whole window freezes, potentially forever (no socket timeout).
- **Fix:** Move the metadata-resolution block into the worker chain (e.g. a small QThread, or do it at the end of `SeparatorWorker.run()`), and pass a timeout to the underlying request (pyacoustid honours `urllib` defaults; set `socket.setdefaulttimeout` around the call or use `acoustid.lookup` with a bounded fingerprint step).
- **Effort:** S–M.

### H2. `update_manifest` fails across drives and uses deprecated `mktemp`
- **Where:** `core/project.py:235–242`.
- **Impact:** The rewritten `.stems` is created at `tempfile.mktemp(...)` (defaults to `%TEMP%`, usually `C:`), then `os.replace(tmp, stems_path)` — which **fails with `OSError` on Windows when source and destination are on different volumes**. Anyone whose library lives on `D:` cannot save title/artist/stem-label/loop edits. `mktemp` is also race-prone (deprecated since 2.3).
- **Fix:** Create the temp file *next to the target* (`stems_path.with_suffix(".stems.tmp")` or `tempfile.NamedTemporaryFile(dir=stems_path.parent, delete=False)`), then `os.replace` — same-volume rename is atomic and always works.
- **Effort:** S.

### H3. Unhandled exception in `_on_separation_done` stalls the import queue
- **Where:** `ui/main_window.py:~805–870`. The method calls `save_stems()` (which runs ffmpeg with `check=True` → `CalledProcessError` on failure: `core/project.py:35–39`), filesystem operations, and manifest reads — none wrapped in try/except. `_pending_job` is only cleared at the end (line ~865).
- **Impact:** Any exception (disk full, ffmpeg missing/failed, permission error on the library dir) propagates into the Qt signal dispatcher: the current job never completes, `_process_next_job()` is never called, the progress widget stays at its last percentage forever, and the remaining queued tracks are silently dropped.
- **Fix:** Wrap the body in `try/except Exception` and route failures through the existing `_on_job_error(msg)` path, which already shows a dialog and continues the queue.
- **Effort:** S.

### H4. Unbounded temp-file accumulation (disk leak)
- **Where:**
  - `core/project.py:198` — `load_stems` extracts to a fresh `mkdtemp` on **every song open** and decodes Opus → WAV there (4 stems ≈ 40–160 MB per open). Never deleted.
  - `core/downloader.py:35` — one `mkdtemp` per YouTube import (raw download + WAV). Never deleted.
  - `core/separator.py:32` — `_ensure_wav` writes a converted WAV via `mktemp`; orphaned on failure and never deleted on success either.
  - `ui/main_window.py` `_start_separation` — `mkdtemp` for separation output; stem WAVs persist after packing.
- **Impact:** Windows does not clean `%TEMP%` automatically. A user who opens 10 songs a day leaks roughly 0.5–1.5 GB/day of WAV data until reboot-cleanup tools or manual deletion intervene.
- **Fix (pragmatic):** Track created temp dirs in a module-level registry and remove them in `MainWindow.closeEvent`; for `load_stems`, reuse a single per-song cache dir keyed by the `.stems` file hash/mtime so repeat opens don't re-extract, and sweep stale entries on startup.
- **Effort:** M.

---

## Medium

### M1. Zero-duration track crashes the player with `ZeroDivisionError`
- **Where:** `ui/player_panel.py:1162` (`song.get("durationMs", 180_000)` — returns **0**, not the default, when the manifest stores 0) → division at `ui/player_panel.py:1296` (`self._time_ms / self._duration`) on every 40 ms tick; also `PlayerPanel._seek` paths.
- **Trigger:** A corrupt manifest, or `save_stems` failing to read duration (it defaults `duration_ms = 0`, `core/project.py:151`).
- **Fix:** `self._duration = max(1, song.get("durationMs") or 180_000)`.
- **Effort:** S.

### M2. `_fill_stop` / `_stop_fill` attribute typo in StemPlayer
- **Where:** `core/player.py:134` defines `self._fill_stop` in `__init__`; everything else (`core/player.py:259, 266, 274, 277, 280`) uses `self._stop_fill`, which is **only created when `_start_fill_thread` runs**.
- **Impact:** Works today by accident of call order (`load()` → `_start_fill_thread()` before any read). Any refactor that touches `_fill_loop`/`_stop_fill_thread` before `load()` raises `AttributeError`. The dead `_fill_stop` misleads readers.
- **Fix:** Rename the `__init__` attribute to `_stop_fill` and delete the dead one.
- **Effort:** S.

### M3. Cross-thread data races in StemPlayer (documented-by-convention only)
- **Where:** `core/player.py` — `_chunk_off` and `_src_pos` are written by the sounddevice callback thread (`:344–349, 355`) and reset by the UI thread inside `seek()`/`set_tempo()` (`:203–207, 231–234`) — the callback reads them **outside** the lock (`:332, 341`). `_tempo`/`_target_tempo` (`:117–118`) rely on an implicit "main thread writes, others read" contract noted only in a comment.
- **Impact:** Benign in practice (worst case: a few wrong samples or a momentarily stale `position_ms()` after a seek race) because Python attribute reads/writes are atomic and numpy slicing clamps. But it's fragile under modification.
- **Fix (pragmatic):** Snapshot `(chunk, chunk_off)` under `_queue_lock` at the top of the callback loop, and add a short "threading contract" docstring on the class. No re-architecture needed.
- **Effort:** M.

### M4. `pause()` leaves the audio stream running
- **Where:** `core/player.py:189–190` — sets `_playing = False` only; the sounddevice callback keeps firing and outputting silence (`:323` loop guard), holding the audio device and burning CPU until `stop()`.
- **Fix:** Call `self._stream.stop()` on pause and `start()` on resume (sounddevice supports restart), or keep as-is intentionally for instant resume — but then document it.
- **Effort:** S.

### M5. Settings writes are not atomic
- **Where:** `core/settings.py:29–32` — `json.dump` straight onto `settings.json`. Crash/power-loss mid-write corrupts the file; `load()` then silently falls back to defaults (`:24–25`), losing **favourites and last-played history** (stored in the same file, `:64–73`).
- **Fix:** Write to `settings.json.tmp` then `os.replace` (same dir, so atomic). Five lines.
- **Effort:** S.

### M6. Stale fill thread can outlive `load()`
- **Where:** `core/player.py:265–270` — `_stop_fill_thread` joins with a 2 s timeout; `_stretch_chunk` (`:94–98`) may be blocked on 4 parallel ffmpeg subprocesses that take longer. `load()` then clears `_stems` (`:148`) and resets `_fill_pos = 0` while the old thread holds references to the old arrays and could append a stale chunk if its captured `fill_pos` happens to equal the new `_fill_pos` (`:305–309`).
- **Impact:** Rare (requires slow stretch + immediate reload), worst case a burst of wrong audio. Memory held by old stems until the thread exits.
- **Fix:** Add a generation counter to the fill loop (same pattern already used for import workers in `main_window.py`), checked before appending.
- **Effort:** S–M.

### M7. Corrupt `.stems` files vanish silently from the library
- **Where:** `core/library.py:58` — bare `except Exception: return None`; no logging anywhere in the app.
- **Impact:** A user whose file is damaged simply sees it missing, with no diagnostic trail. Same pattern across `core/metadata.py:35, 106`, `core/settings.py:24`.
- **Fix (pragmatic):** Add a tiny `logging` setup writing to `~/.rehearsalroom/app.log` and a one-line `log.warning` in these handlers. Optionally surface "N files could not be read" in the library header.
- **Effort:** S.

---

## Low

| # | Finding | Where | Suggested fix |
|---|---------|-------|---------------|
| L1 | `save_stems` reads each **entire WAV into RAM** just to get its duration | `core/project.py:159` | `sf.info(str(wav_path)).duration` — no data read |
| L2 | `QGraphicsOpacityEffect` recreated on every mute/solo toggle | `ui/player_panel.py:383–389` | Create once per lane, toggle `setEnabled` |
| L3 | Waveform bars recomputed in a Python loop every paint (≈680 iterations × 4 lanes × 25 fps) | `ui/widgets.py:302–311` | Cache the `bars` list keyed on `(data id, width, zoom, scroll)`; only alpha depends on progress |
| L4 | Unthrottled `seeked` emit per mouse-move while scrubbing; at tempo ≠ 1.0 each seek clears the chunk queue and triggers a 4-process ffmpeg restretch | `ui/widgets.py:378–379` → `core/player.py:200–208` | Throttle scrub-seeks to ~50 ms, or seek on release when tempo ≠ 1.0 |
| L5 | Import queue doesn't dedup paths/URLs across batches — importing the same file twice double-processes it | `ui/main_window.py:718` (`extend`) | Skip jobs whose path/URL is already queued or in-flight |
| L6 | Skip decrements the batch total but an **error** doesn't, so counts behave differently for the two "track didn't import" cases | `ui/main_window.py` `_cancel_current_job` vs `_on_job_error` | Pick one convention; likely decrement on error too |
| L7 | Modal error dialog (`_on_job_error` → `.exec()`) pauses the queue until dismissed — next track won't start while the user is away | `ui/main_window.py:~905` | Non-modal dialog, or collect errors and show a summary at batch end |
| L8 | Dead code: `ProcessingDialog`/`StemCard` (superseded by `ImportProgressWidget`) still defined, imported, and `_proc_dlg` still initialised | `ui/import_dialog.py:794+`, `ui/main_window.py:14, 537, 731` | Delete the class and the `_proc_dlg` remnants |
| L9 | Dead state in `ImportDialog`: `_file_paths`/`_yt_urls` lists are never used (lists live in the `_ItemListWidget`s now) | `ui/import_dialog.py:317–318` | Delete |
| L10 | Artist grouping does `O(n²)` `next(...)` scans inside its loops | `ui/library_panel.py` `_rebuild_rows_by_artist` | Build a `{lower: canonical}` dict once |
| L11 | tqdm is monkey-patched at module level from worker threads (separator, model_cache); safe only because jobs are strictly sequential | `core/separator.py:104–121`, `core/model_cache.py:54–70` | Comment the invariant; if parallel jobs ever land, switch to demucs' callback API |
| L12 | Unused imports flagged by AST sweep: `QUrl`, `QMimeData`, `QColor` (`ui/import_dialog.py:4–5`), `QColor/QPalette/QFont/QSizePolicy/STEM_IDS` (`ui/main_window.py:4–11`), `QBrush/QPen/QFont/QApplication/Optional` (`ui/widgets.py:5–9`), `np` (`core/project.py:13`), several `QRectF/QPainterPath/QLinearGradient/QSizePolicy` (`ui/player_panel.py:10–12`) | — | Remove |
| L13 | `main.py:10–13` devnull handles intentionally never closed (needed for app lifetime) — fine, but a brief comment saying so would prevent "fixing" it | `main.py:10–13` | Comment only |

---

## Cheap wins (do these first)

1. **H2** — write `update_manifest` temp file next to the target (fixes a real user-facing save failure; ~3 lines).
2. **H3** — try/except around `_on_separation_done` body routing to `_on_job_error` (~4 lines).
3. **M1** — `max(1, …)` duration guard (~1 line).
4. **M5** — atomic settings write (~5 lines).
5. **M2** — `_fill_stop` rename (~2 lines).
6. **L1** — `sf.info` for duration (~1 line).
7. **L8/L9/L12** — dead-code and import cleanup.

## Deferred / at-scale (document, don't redesign now)

- **Full stems in RAM** (`core/player.py:153–157`): ~175 MB per 4-minute song across 4 stems. Fine for typical tracks; a 2-hour recording would use ~1 GB+. If ever needed: memory-map or stream from disk. Until then, an upfront size check with a friendly warning would be enough.
- **Full row rebuild in the library** (`ui/library_panel.py` `_rebuild_rows`, also triggered ×3 on startup via `set_songs`/`set_favourites`/`set_last_viewed`): O(n) widget churn per filter/sort/favourite change. Negligible below ~500 tracks; consider batching the three startup calls into one, and only virtualise if libraries get huge.
- **Paint load**: 4 lanes × 25 fps custom painting is fine on any modern machine; L3's bar caching is the only worthwhile tweak.
- **Per-chunk ffmpeg processes** for tempo stretch (4 subprocess spawns per 2 s chunk at tempo ≠ 1.0): measurable process-spawn overhead on Windows but works well in practice; a persistent rubberband pipe would be the upgrade path if scrubbing at tempo ever feels sluggish.

---

*Static checks: no syntax errors in any module (AST parse clean). No `bare except:` clauses; broad `except Exception` handlers are listed in M7/L12 context. Two `tempfile.mktemp` uses (H2, H4). pyflakes/ruff are not installed in this environment — the unused-import list (L12) came from an AST-based sweep and is worth re-verifying with ruff before bulk-deleting.*
