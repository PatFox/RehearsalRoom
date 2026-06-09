# Rehearsal Room

Rehearsal Room is intended as a practice tool for musicians: you can use it to split a music track (either from an audio file, or a YouTube URL) into "stems" -- i.e. separate files for i.e. vocals, drums, bass, and 'other' (e.g. guitar). Once this is done, you can play back the separate files and control each part individually: for example, change the volume of a specific part, mute certain tracks, or play just a single track.

In addition, you can slow the audio down without affecting the pitch, and loop a section of the track. Loops can be saved, so that you can recall them later.

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

- Windows (macOS/Linux support planned...maybe)

## Acknowledgments

First of all, it's important to note that most of the hard work behind the scenes is being done by libraries and utilities that were created by other much smarter people than me.

**UI**
- **[PySide6](https://doc.qt.io/qtforpython/)** — the entire desktop UI: windows, panels, buttons, sliders, waveform canvas, signals/slots

**Stem separation**
- **[demucs](https://github.com/facebookresearch/demucs)** — the AI model that splits a song into vocals, drums, bass, and other stems
- **[torch](https://pytorch.org/)** — PyTorch, the deep learning runtime that runs Demucs
- **[torchaudio](https://pytorch.org/audio/)** — used for audio resampling (matching the source file's sample rate to Demucs' expected rate)

**Audio I/O & playback**
- **[soundfile](https://python-soundfile.readthedocs.io/)** — reads/writes WAV and FLAC audio files; used when loading stems and encoding them
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — plays audio through the system sound card via a low-latency streaming callback
- **[numpy](https://numpy.org/)** — array maths used throughout audio processing (mixing stems, RMS waveform calculation, resampling)
- **[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)** — bundles a static ffmpeg binary; used for Opus encoding, rubberband tempo-stretching, and format conversion

**YouTube download**
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — downloads audio from a YouTube URL

**Metadata**
- **[mutagen](https://mutagen.readthedocs.io/)** — reads embedded tags (title, artist) from local audio files (MP3, FLAC, M4A, etc.)
- **[pyacoustid](https://github.com/beetbox/pyacoustid)** — generates an audio fingerprint and queries the AcoustID/MusicBrainz database to identify a song when no tags are present

**Build / packaging**
- **[pyinstaller](https://pyinstaller.org/)** — packages the app and all its dependencies into a standalone Windows executable