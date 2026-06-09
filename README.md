# Rehearsal Room

Rehearsal Room is intended as a practice tool for musicians: you can use it to split a music track (either from an audio file, or a YouTube URL) into "stems" -- i.e. separate files for i.e. vocals, drums, bass, and 'other' (e.g. guitar). Once this is done, you can play back the separate files simultaneously while controlling each part individually: for example, you can change the volume of a specific part, mute certain parts, or play just a single part (solo).

In addition, you can slow the audio down without affecting the pitch, and loop a section of the track. Loops can be saved, so that you can recall them later.

Bonus: fully supports Vidami footswitch for play/pause, forward/back, loop start/end/clear, and adjust playback speed.

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

## This project was created using AI (LLM) tools!

While I am a programmer by trade, I did not write a single line of code in this project. I have wanted a tool like this for quite a while, but I've never had the spare time to even contemplate taking it on. 

However when I became aware of just how potentially powerful modern AI development tools were becoming, I decided to let Anthropic's Claude Code (specifically, Sonnet 4.6) loose on the idea and see what it could do. The result is what you see here.

I guided development with a few nudges here and there. Mostly it was cosmetic fixes -- Claude seems to be a bit hit-and-miss when it comes to the visual end of things so while the real heavy lifting in the code was mostly smooth sailing, it took a bit of effort to make it look nice.

## Acknowledgments

It's important to note that most of the hard work behind the scenes is being done by libraries and utilities that were created by other much smarter people than me.

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

## Is this legal?

That's a good question, to which I don't have a definitive answer. The following is not legal advice! 

Certainly, ripping audio from YouTube is a legal grey area. However, my gut feeling is that if you use this tool for its intended purpose -- i.e. as a practice tool -- then there should be no problem.

I would advise against distributing the .stem files unless you're confident that the source material is not under copyright.

Bottom line: use it at your own risk.

## Licence

The code for this project is offered under the MIT licence (short version: do what you want with it, don't blame me if it breaks).

However, as noted above it makes use of multiple third-party libraries, so if you intend to do something other than just use it for personal applications then you should be sure to familiarise yourself with the licences for each project.