"""Read and write .stems files (ZIP container with FLAC stems + JSON manifest)."""

import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import soundfile as sf
import numpy as np


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil as _sh
        return _sh.which("ffmpeg") or "ffmpeg"


def _encode_opus(wav_path: Path, opus_path: Path, bitrate: str = "128k") -> None:
    """Encode a WAV file to Opus using ffmpeg."""
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(wav_path),
         "-c:a", "libopus", "-b:a", bitrate, str(opus_path)],
        check=True, capture_output=True,
    )


def _decode_to_wav(src_path: Path, wav_path: Path) -> None:
    """Decode any ffmpeg-readable audio file to WAV (for soundfile compatibility)."""
    subprocess.run(
        [_ffmpeg(), "-y", "-i", str(src_path),
         "-ac", "2", "-ar", "44100", str(wav_path)],
        check=True, capture_output=True,
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
) -> StemsProject:
    """Encode WAV stem files to Opus and pack into a .stems ZIP archive."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stem_infos: list[StemInfo] = []
    opus_paths: dict[str, Path] = {}
    duration_ms = 0

    tmp_dir = output_path.parent / (output_path.stem + "_tmp")
    tmp_dir.mkdir(exist_ok=True)

    try:
        for stem_id, wav_path in wav_paths.items():
            # Read WAV to get duration
            data, samplerate = sf.read(str(wav_path))
            if duration_ms == 0:
                duration_ms = int(len(data) / samplerate * 1000)

            # Encode to Opus via ffmpeg
            opus_path = tmp_dir / f"{stem_id}.opus"
            _encode_opus(Path(wav_path), opus_path)
            opus_paths[stem_id] = opus_path

            label = (stem_labels or {}).get(stem_id, stem_id.capitalize())
            stem_infos.append(StemInfo(id=stem_id, file=f"{stem_id}.opus", label=label))

        manifest = StemsManifest(
            title=title,
            artist=artist,
            source_url=source_url,
            duration_ms=duration_ms,
            stems=stem_infos,
        )

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))
            for stem_id, opus_path in opus_paths.items():
                zf.write(opus_path, f"{stem_id}.opus")
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
        import tempfile
        extract_dir = Path(tempfile.mkdtemp(prefix="rehearsalroom_"))
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


def read_manifest(stems_path: Path) -> StemsManifest:
    """Read only the manifest from a .stems file without extracting audio."""
    with zipfile.ZipFile(stems_path, "r") as zf:
        with zf.open("manifest.json") as f:
            return StemsManifest.from_dict(json.load(f))


def update_manifest(stems_path: Path, manifest: StemsManifest) -> None:
    """Rewrite the manifest.json inside an existing .stems file."""
    import tempfile, os
    tmp = Path(tempfile.mktemp(suffix=".stems"))
    with zipfile.ZipFile(stems_path, "r") as zin, zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zout:
        for item in zin.infolist():
            if item.filename == "manifest.json":
                zout.writestr("manifest.json", json.dumps(manifest.to_dict(), indent=2))
            else:
                zout.writestr(item, zin.read(item.filename))
    os.replace(tmp, stems_path)
