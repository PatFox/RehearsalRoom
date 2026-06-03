# Rehearsal Room

A desktop application that splits music tracks into individual stems (vocals, drums, bass, other) using AI, with a DAW-style mixer for playback.

## Features

- Import audio files (MP3, WAV, FLAC, M4A, OGG) or paste a YouTube URL
- AI-powered stem separation via [Demucs v4](https://github.com/facebookresearch/demucs)
- DAW-style mixer with per-stem volume, mute, and solo
- Pitch-preserving playback speed control (50–200%)
- Real waveform visualisation per stem
- Auto-populated metadata from file tags, YouTube info, or AcoustID fingerprinting
- Songs saved as portable `.stems` files (ZIP + JSON manifest)
- Light and dark themes

## Requirements

- Python 3.11+
- Windows (macOS/Linux support planned)

## Setup

```bash
pip install -r requirements.txt
```

### Optional: ffmpeg (for YouTube downloads)

```bash
pip install imageio-ffmpeg   # bundled binary — recommended
```

Or place `ffmpeg.exe` and `ffprobe.exe` in the `bin/` folder.

### Optional: fpcalc (for AcoustID audio fingerprinting)

Download `fpcalc.exe` from [acoustid.org/chromaprint](https://acoustid.org/chromaprint) and place it in `bin/`.  
Then add your free API key in Settings (⚙).

## Running

```bash
python main.py
```

## Building a standalone executable

```bash
pip install pyinstaller
python build.py
```

Output is in `dist/RehearsalRoom/`. Wrap with [Inno Setup](https://jrsoftware.org/isinfo.php) for a distributable installer.

## Technology

| Component | Library |
|---|---|
| GUI | PySide6 (Qt) |
| Stem separation | Demucs v4 (htdemucs model) |
| Audio playback | sounddevice + numpy |
| YouTube download | yt-dlp |
| File format | ZIP (.stems) + JSON manifest |
| Metadata | mutagen, pyacoustid |
