"""Read and write .stems files (ZIP container with FLAC stems + JSON manifest)."""

import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import soundfile as sf


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil as _sh
        return _sh.which("ffmpeg") or "ffmpeg"


def _subprocess_kwargs() -> dict:
    """Extra kwargs for subprocess.run to suppress console windows on Windows."""
    kwargs: dict = {"capture_output": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _encode_opus(wav_path: Path, opus_path: Path, bitrate: str = "128k") -> None:
    """Encode a WAV file to Opus using ffmpeg."""
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(wav_path),
         "-c:a", "libopus", "-b:a", bitrate, str(opus_path)],
        check=True, **_subprocess_kwargs(),
    )


def _encode_flac(src_path: Path, flac_path: Path) -> None:
    """Encode any ffmpeg-readable audio to FLAC (lossless, stereo 44.1 kHz)."""
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(src_path),
         "-ac", "2", "-ar", "44100", "-c:a", "flac", str(flac_path)],
        check=True, **_subprocess_kwargs(),
    )


EXPORT_FORMATS = [("FLAC", ".flac"), ("WAV", ".wav"), ("MP3", ".mp3"),
                  ("OGG", ".ogg"), ("M4A", ".m4a")]

_EXPORT_CODECS = {
    ".flac": ["-c:a", "flac"],
    ".wav":  ["-c:a", "pcm_s16le"],
    ".mp3":  ["-c:a", "libmp3lame", "-q:a", "2"],
    ".ogg":  ["-c:a", "libvorbis", "-q:a", "5"],
    ".m4a":  ["-c:a", "aac", "-b:a", "256k"],
}


def transcode_audio(src_path: Path, dest_path: Path) -> None:
    """Convert any ffmpeg-readable audio to the format implied by dest's suffix."""
    dest_path = Path(dest_path)
    codec = _EXPORT_CODECS.get(dest_path.suffix.lower(), [])
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(src_path), *codec, str(dest_path)],
        check=True, **_subprocess_kwargs(),
    )


def _decode_to_wav(src_path: Path, wav_path: Path) -> None:
    """Decode any ffmpeg-readable audio file to WAV (for soundfile compatibility)."""
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(src_path),
         "-ac", "2", "-ar", "44100", str(wav_path)],
        check=True, **_subprocess_kwargs(),
    )


MANIFEST_VERSION = "1"

STEM_COLORS = {
    "vocals": "#E74C3C",
    "drums":  "#3498DB",
    "bass":   "#2ECC71",
    "other":  "#9B59B6",
    "guitar": "#F39C12",
    "piano":  "#1ABC9C",
}


@dataclass
class StemInfo:
    id: str
    file: str
    label: str
    color: str = ""

    def __post_init__(self):
        if not self.color:
            self.color = STEM_COLORS.get(self.id, "#888888")


@dataclass
class SavedLoop:
    name: str
    start_ms: int
    end_ms: int
    active_stems: list[str]   # stem ids that are audible (not muted)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "active_stems": self.active_stems,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SavedLoop":
        return cls(
            name=d.get("name", ""),
            start_ms=int(d.get("start_ms", 0)),
            end_ms=int(d.get("end_ms", 0)),
            active_stems=list(d.get("active_stems", [])),
        )


@dataclass
class StemsManifest:
    version: str = MANIFEST_VERSION
    title: str = ""
    artist: str = ""
    source_url: str = ""
    duration_ms: int = 0
    original: str = ""   # filename of the embedded original mix, "" if none
    stems: list[StemInfo] = field(default_factory=list)
    loops: list[SavedLoop] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["loops"] = [lp.to_dict() for lp in self.loops]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StemsManifest":
        stems = [StemInfo(**s) for s in d.get("stems", [])]
        loops = [SavedLoop.from_dict(lp) for lp in d.get("loops", [])]
        return cls(
            version=d.get("version", MANIFEST_VERSION),
            title=d.get("title", ""),
            artist=d.get("artist", ""),
            source_url=d.get("source_url", ""),
            duration_ms=d.get("duration_ms", 0),
            original=d.get("original", ""),
            stems=stems,
            loops=loops,
        )


@dataclass
class StemsProject:
    manifest: StemsManifest
    stem_paths: dict[str, Path]  # stem_id -> path to decoded WAV (or FLAC for legacy)
    source_path: Optional[Path] = None  # path to the .stems file


def save_stems(
    wav_paths: dict[str, Path],
    output_path: Path,
    title: str = "",
    artist: str = "",
    source_url: str = "",
    stem_labels: Optional[dict[str, str]] = None,
    cover: Optional[bytes] = None,
    original_path: Optional[Path] = None,
) -> StemsProject:
    """Encode WAV stem files to Opus and pack into a .stems ZIP archive.

    If *original_path* is given, the source mix is embedded losslessly (FLAC)
    as ``original.flac`` so the full track can always be recovered.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stem_infos: list[StemInfo] = []
    opus_paths: dict[str, Path] = {}
    duration_ms = 0

    tmp_dir = output_path.parent / (output_path.stem + "_tmp")
    tmp_dir.mkdir(exist_ok=True)

    try:
        for stem_id, wav_path in wav_paths.items():
            if duration_ms == 0:
                info = sf.info(str(wav_path))
                duration_ms = int(info.frames / info.samplerate * 1000)

            # Encode to Opus via ffmpeg
            opus_path = tmp_dir / f"{stem_id}.opus"
            _encode_opus(Path(wav_path), opus_path)
            opus_paths[stem_id] = opus_path

            label = (stem_labels or {}).get(stem_id, stem_id.capitalize())
            stem_infos.append(StemInfo(id=stem_id, file=f"{stem_id}.opus", label=label))

        # Embed the original mix (FLAC — lossless, ~half the size of WAV)
        original_arc = ""
        original_flac: Optional[Path] = None
        if original_path and Path(original_path).exists():
            original_flac = tmp_dir / "original.flac"
            try:
                _encode_flac(Path(original_path), original_flac)
                original_arc = "original.flac"
            except Exception:
                original_flac = None   # don't fail the whole save over the copy

        manifest = StemsManifest(
            title=title,
            artist=artist,
            source_url=source_url,
            duration_ms=duration_ms,
            original=original_arc,
            stems=stem_infos,
        )

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))
            for stem_id, opus_path in opus_paths.items():
                zf.write(opus_path, f"{stem_id}.opus")
            if cover:
                zf.writestr("cover.jpg", cover)
            if original_flac and original_flac.exists():
                zf.write(original_flac, original_arc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return StemsProject(manifest=manifest, stem_paths=opus_paths, source_path=output_path)


def load_stems(stems_path: Path, extract_dir: Optional[Path] = None) -> StemsProject:
    """Extract a .stems file and return a StemsProject with paths to WAV files.

    Opus (and any other format soundfile can't read natively) is decoded to WAV
    via ffmpeg. FLAC files from older .stems files are returned as-is.
    """
    stems_path = Path(stems_path)
    if extract_dir is None:
        from core.tempdirs import make_temp_dir
        extract_dir = make_temp_dir("stems_")
    else:
        extract_dir = Path(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(stems_path, "r") as zf:
        zf.extractall(extract_dir)

    manifest_path = extract_dir / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        manifest = StemsManifest.from_dict(json.load(f))

    # Soundfile can read WAV and FLAC natively; Opus needs decoding via ffmpeg.
    _SF_NATIVE = {".wav", ".flac", ".ogg", ".aiff", ".aif"}
    stem_paths: dict[str, Path] = {}
    for s in manifest.stems:
        src = extract_dir / s.file
        if src.suffix.lower() not in _SF_NATIVE:
            wav = src.with_suffix(".wav")
            _decode_to_wav(src, wav)
            stem_paths[s.id] = wav
        else:
            stem_paths[s.id] = src

    return StemsProject(manifest=manifest, stem_paths=stem_paths, source_path=stems_path)


def set_original(stems_path: Path, audio_path: Path) -> None:
    """Embed *audio_path* (FLAC-encoded) as the original mix in an existing
    .stems file, updating the manifest. Preserves the file's mtime so the
    library ordering/"added" label doesn't shift. Best-effort — raises on
    hard failures so callers can ignore them.
    """
    import os
    from core.tempdirs import make_temp_dir
    stems_path = Path(stems_path)
    manifest = read_manifest(stems_path)
    manifest.original = "original.flac"

    tmp = make_temp_dir("setorig_")
    flac = tmp / "original.flac"
    _encode_flac(Path(audio_path), flac)

    orig_stat = stems_path.stat()
    tmp_zip = stems_path.with_suffix(".stems.tmp")
    try:
        with zipfile.ZipFile(stems_path, "r") as zin, \
             zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zout:
            for item in zin.infolist():
                if item.filename in ("manifest.json", "original.flac"):
                    continue   # rewritten / replaced below
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))
            zout.write(flac, "original.flac")
        os.replace(tmp_zip, stems_path)
        os.utime(stems_path, (orig_stat.st_atime, orig_stat.st_mtime))
    finally:
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)


def extract_original(stems_path: Path, dest_dir: Path) -> Optional[Path]:
    """Extract the embedded original mix to *dest_dir*, or None if absent."""
    stems_path = Path(stems_path)
    manifest = read_manifest(stems_path)
    if not manifest.original:
        return None
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(stems_path, "r") as zf:
        if manifest.original not in zf.namelist():
            return None
        out = dest_dir / manifest.original
        with zf.open(manifest.original) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return out


def read_manifest(stems_path: Path) -> StemsManifest:
    """Read only the manifest from a .stems file without extracting audio."""
    with zipfile.ZipFile(stems_path, "r") as zf:
        with zf.open("manifest.json") as f:
            return StemsManifest.from_dict(json.load(f))


def read_cover(stems_path: Path) -> Optional[bytes]:
    """Return embedded cover.jpg bytes from a .stems file, or None if absent."""
    try:
        with zipfile.ZipFile(stems_path, "r") as zf:
            if "cover.jpg" in zf.namelist():
                return zf.read("cover.jpg")
    except Exception:
        pass
    return None


def update_manifest(stems_path: Path, manifest: StemsManifest) -> None:
    """Rewrite the manifest.json inside an existing .stems file."""
    import os
    stems_path = Path(stems_path)
    # Build the replacement next to the target: os.replace() is only atomic —
    # and on Windows only *possible* — within a single volume, so a temp file
    # in %TEMP% would fail for libraries on another drive.
    tmp = stems_path.with_suffix(".stems.tmp")
    try:
        with zipfile.ZipFile(stems_path, "r") as zin, zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zout:
            for item in zin.infolist():
                if item.filename == "manifest.json":
                    zout.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))
                else:
                    zout.writestr(item, zin.read(item.filename))
        os.replace(tmp, stems_path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
