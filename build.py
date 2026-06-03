"""
Build helper — generates rehearsalroom.spec and runs PyInstaller.

Usage:
    python build.py

Requirements:
    pip install pyinstaller
    pip install -r requirements.txt   (all deps must be installed first)

Output:
    dist/rehearsalroom/      <- the distributable folder (zip or wrap in Inno Setup)
"""

import subprocess
import sys
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent


def find_demucs_models() -> list[tuple[str, str]]:
    """Return (src_path, dest_in_bundle) pairs for downloaded Demucs model weights."""
    import torch
    cache = Path(torch.hub.get_dir())
    pairs = []
    # Demucs stores weights under torch hub checkpoints dir
    for pattern in ["checkpoints/", "hub/checkpoints/"]:
        p = cache / pattern
        if p.exists():
            pairs.append((str(p), "torch_cache/checkpoints"))
    # Also check the demucs-specific pretrained cache
    try:
        from demucs.pretrained import SOURCES
    except Exception:
        pass
    xdg = Path.home() / ".cache" / "demucs"
    if xdg.exists():
        pairs.append((str(xdg), "demucs_cache"))
    return pairs


def main():
    # ---- step 1: collect data files ----------------------------------------
    datas: list[str] = []

    # Assets folder
    assets = ROOT / "assets"
    if assets.exists():
        datas.append(f"--add-data={assets}{os.pathsep}assets")

    # Demucs models (only if already downloaded — first run will download them)
    for src, dest in find_demucs_models():
        datas.append(f"--add-data={src}{os.pathsep}{dest}")

    # FFmpeg binary (place ffmpeg.exe in bin/ next to main.py first)
    ffmpeg = ROOT / "bin" / "ffmpeg.exe"
    if ffmpeg.exists():
        datas.append(f"--add-data={ffmpeg}{os.pathsep}bin")
    else:
        print("WARNING: bin/ffmpeg.exe not found. yt-dlp downloads won't work in the built exe.")
        print("  Download from https://github.com/BtbN/FFmpeg-Builds/releases and place ffmpeg.exe in bin/")

    # ---- step 2: hidden imports ---------------------------------------------
    hidden = [
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=soundfile",
        "--hidden-import=sounddevice",
        "--hidden-import=numpy",
        "--hidden-import=demucs",
        "--hidden-import=demucs.pretrained",
        "--hidden-import=demucs.apply",
        "--hidden-import=torchaudio",
        "--hidden-import=yt_dlp",
        "--hidden-import=librosa",
    ]

    # ---- step 3: build command ----------------------------------------------
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=RehearsalRoom",
        "--onedir",                  # folder (not single file) — faster startup with ML models
        "--windowed",                # no console window on Windows
        "--noconfirm",               # overwrite dist/ without asking
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build'}",
        f"--specpath={ROOT}",
        "--collect-all=demucs",      # collect all demucs submodules + data
        "--collect-all=torchaudio",
        "--collect-all=yt_dlp",
        *datas,
        *hidden,
        str(ROOT / "main.py"),
    ]

    print("Running PyInstaller…")
    print(" ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\nBuild FAILED.")
        sys.exit(1)

    print("\n✓ Build complete: dist/RehearsalRoom/RehearsalRoom.exe")
    print("\nTo distribute:")
    print("  Option A: zip the dist/RehearsalRoom/ folder and share it.")
    print("  Option B: wrap it in Inno Setup for a proper installer.")
    print("            See https://jrsoftware.org/isinfo.php")


if __name__ == "__main__":
    main()
