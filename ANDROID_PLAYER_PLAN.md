# Rehearsal Room — Android Player (implementation plan)

> Separate from the desktop app plan. This describes a **playback-only** Android
> companion that opens `.stems` files produced by the desktop app. **No** stem
> separation, **no** tab editing — play, mix, loop, change speed/pitch, and
> display tabs read-only.

---

## 1. Context & scope

The desktop app exports a self-contained `.stems` file (a ZIP). The Android app
consumes it and reproduces the player experience:

- **Lanes view** — one row per stem (vocals/drums/bass/other) with a waveform,
  volume fader, mute, solo. Plus an "Original" lane if `original.flac` is present
  (muted by default), matching desktop.
- **Transport** — play/pause, seek, time display.
- **Speed** — pitch-preserving tempo 0.25×–4× (desktop range).
- **Pitch** — ±12 semitones, independent of speed.
- **Looping** — set/loop an A–B region; saved loops from the manifest are listed
  and restorable (restoring also restores per-stem mute state).
- **Tabs** — read-only render of each tab track, bars anchored to the audio
  timeline, with a playback highlight. Switch between tab tracks; no editing.

Out of scope: importing audio, separation, tab editing, YouTube, exporting.

### `.stems` format (already produced by desktop — `core/project.py`)
ZIP container, `.stems` extension:
- `manifest.json` — `version`, `title`, `artist`, `source_url`, `duration_ms`,
  `original` (filename or ""), `stems[]` `{id,file,label,color}`, `loops[]`
  `{name,start_ms,end_ms,active_stems[]}`, `tabs[]` (see §4).
- `<id>.opus` per stem (Opus ~128 kbps, 44.1 kHz stereo).
- `original.flac` (optional, full mix).
- `cover.jpg` (optional).

Tab model (`core/tab.py`) is the authoritative schema to port: `TabTrack`
(id, stem_id, name, strings, tuning, capo, def_ts_num/den, bars[]), `Bar`
(ts_num/den, **start_ms/end_ms anchors**, beats[]), `Beat` (pos 0–1 in bar, dur,
dotted, tuplet, rest, notes[]), `Note` (string, fret, techniques[], bend[]).
Beat real-time = `start_ms + pos*(end_ms-start_ms)` (piecewise-linear between
anchors — audio is the clock).

---

## 2. Tech stack

- **Language/UI:** Kotlin + Jetpack Compose (Material 3). Compose `Canvas` for
  waveforms and the tab grid.
- **Audio engine:** native C++ via **Oboe** (low-latency `AAudio`/`OpenSL`),
  with a custom mixer. Pitch/tempo via **SoundTouch** (LGPL, dynamic-linked).
- **Decode:** `MediaCodec` for Opus + FLAC → PCM (no extra deps). Fallback:
  bundle libopus/libFLAC if codec coverage is a problem on older devices.
- **Serialization:** kotlinx.serialization for `manifest.json` + tabs.
- **Min SDK:** 24 (Android 7). Oboe + MediaCodec Opus/FLAC all supported.
- **Modules:** `:app` (UI), `:core` (model + file reading), `:audio` (NDK engine
  via JNI). Keep the audio engine behind a small Kotlin interface so the MVP can
  start without native code (see §6, Phase 1 fallback).

JNI boundary kept tiny: `load(stemPcmPtrs, sampleRate, lengths)`, `play/pause`,
`seek(ms)`, `setGain(stem,g)`, `setMuteSolo(...)`, `setTempo(x)`, `setPitch(semis)`,
`setLoop(startMs,endMs,on)`, `positionMs()`.

---

## 3. Audio engine (the core)

Mirrors the desktop `StemPlayer` but mixes the *whole* track per output callback
rather than chunking.

**Load:** unzip → decode each stem to interleaved PCM (float or 16-bit) at a
common sample rate. Hold per-stem PCM buffers. (Original lane is just another
stem id `"original"`, loaded muted.)

**Mix callback (Oboe, ~stereo, 256–512 frame bursts):**
1. From a shared sample position, read `frames` from each stem.
2. Apply per-stem gain; honour solo (if any soloed, others muted) / mute.
3. Sum to a stereo mix buffer.
4. Feed the mix through one **SoundTouch** instance (tempo + pitch); pull
   stretched output to the device.
5. Advance the *source* position by the consumed input frames; expose
   `positionMs` in original-song time (so UI/tabs map cleanly, like desktop's
   `position_ms`).
6. **Loop:** when source position ≥ loopEnd, jump to loopStart (clear SoundTouch
   latency buffer).

Sync is inherent — all stems share one position; no drift.

**Memory:** 4 stems × ~4 min × 44.1k stereo: ~175 MB float / ~88 MB int16. Use
**int16 PCM** by default; for long tracks or low-RAM devices, stream-decode in
blocks behind the mixer (Phase 4 optimisation). Show a load progress spinner.

**Speed/pitch options:**
- **Primary:** SoundTouch on the mix → independent tempo + semitone pitch, good
  quality, matches desktop behaviour (uniform across stems).
- **MVP fallback (no NDK):** mix in Kotlin, output via `AudioTrack` with
  `PlaybackParams.setSpeed()/setPitch()` (API 23 Sonic). Lower quality at large
  shifts but ships fast; swap to SoundTouch later behind the same interface.

---

## 4. Data / file layer (`:core`)

- `StemsArchive.open(uri)`: read ZIP entries lazily; expose `manifest`, stem
  entry streams, optional cover bytes.
- Kotlin models mirroring desktop dataclasses; tolerant parsing
  (`?: default`, ignore unknown keys, key off `manifest.version`).
- `Loop`, `TabTrack/Bar/Beat/Note` + timing helpers (`beatMs`, `activeBarBeat`)
  ported verbatim from `core/tab.py`.
- Waveform peaks computed once from decoded PCM (downsample to ~2–4k buckets per
  stem), cached in memory.

---

## 5. UI (Compose)

- **Library/open screen:** pick a `.stems` via Storage Access Framework
  (`ACTION_OPEN_DOCUMENT`) / share-target; optional recent list. Show cover,
  title, artist.
- **Player screen:**
  - Header: cover, title/artist, back.
  - Lanes column: per stem → label, M/S toggles, volume fader, waveform with a
    playhead; tap waveform to seek; shared horizontal zoom/scroll across lanes
    + ruler (port the desktop `TimelineCoords` fraction↔pixel math).
  - Transport bar: play/pause, time, **Speed** slider (0.25–4×), **Pitch**
    stepper (−12…+12), **Loop** controls; saved-loops list.
  - **Tab panel** (collapsible, below lanes): tab-name + switch dropdown,
    read-only tab `Canvas` sharing the same x-axis zoom/scroll, with bar lines,
    time signatures, bar numbers, fret numbers, technique markers, and a
    synced active-bar/beat highlight. No handles, no editing.
- Drive UI position from a ~60 fps ticker reading `engine.positionMs()` (smooth
  like the desktop eased scroll).

---

## 6. Phased build order

1. **Skeleton + file read.** Project setup, open a `.stems`, parse manifest +
   tabs, list stems. Unit-test parsing against real desktop exports.
2. **MVP audio (all-Kotlin).** Decode stems (MediaCodec), Kotlin mixer →
   `AudioTrack`, play/pause/seek, per-stem volume/mute/solo, looping. Speed/pitch
   via `PlaybackParams`. Proves end-to-end playback fast.
3. **Lanes UI.** Compose lanes with faders/M/S, waveforms, shared zoom/scroll,
   transport, loop UI + saved loops, Original lane (muted default).
4. **Native engine.** Oboe + SoundTouch behind the engine interface; replace the
   MVP path for quality/latency. int16 PCM; optional streaming decode.
5. **Tab rendering.** Read-only Canvas: bars/time-sig/numbers/notes/techniques,
   tab switch, synced highlight, zoom/scroll locked to lanes.
6. **Polish.** Cover art, recents, errors, low-RAM handling, large-file tests,
   release build (R8, NDK ABIs arm64-v8a + armeabi-v7a).

---

## 7. Risks & decisions

- **Pitch/tempo quality vs effort:** SoundTouch (recommended) vs `PlaybackParams`
  MVP. Avoid Rubber Band (GPL/commercial) — the desktop uses it via ffmpeg, but
  it's awkward to ship on Android.
- **Memory:** default int16; stream-decode for long tracks; guard on device RAM.
- **Codec coverage:** MediaCodec Opus/FLAC is standard from API 21/24; bundle
  libopus/libFLAC only if field testing shows gaps.
- **Licensing:** SoundTouch LGPL (dynamic link, document it); Oboe Apache-2.0.
- **Format drift:** parser keyed off `manifest.version`, defaults for missing
  fields. The tab schema is shared with desktop — keep them in sync by treating
  `core/tab.py` as the spec.
- **File transfer:** out of scope here; v1 relies on the user copying the file
  to the device (cloud/USB/share). A companion sync is a later, separate feature.

---

## 8. Verification

- Parse real desktop `.stems` exports (incl. ones with `original.flac`, loops,
  multiple tabs) → assert model matches.
- Clap/timing test: solo drums, confirm stems stay sample-synced through
  seeking, looping, and speed/pitch changes.
- Volume/mute/solo audibly independent; Original lane muted by default and A/B-able.
- Speed changes preserve pitch; pitch changes preserve tempo; both combine.
- Loop boundaries seamless; saved-loop restore re-applies mute state.
- Tab bars line up with the waveform under zoom/scroll; active bar/beat
  highlights in time with playback; tab switching works.
- Run on a low-RAM (2–3 GB) device with a long (>6 min) track.
