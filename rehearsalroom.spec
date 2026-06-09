# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rehearsal Room.

Build with:
    pyinstaller rehearsalroom.spec

Output: dist/RehearsalRoom/RehearsalRoom.exe  (plus supporting files)
"""

import sys
import os
from pathlib import Path
import imageio_ffmpeg

block_cipher = None

# Collect entire packages whose internal imports PyInstaller can't trace statically.
from PyInstaller.utils.hooks import collect_all
torch_datas,   torch_binaries,   torch_hiddenimports   = collect_all('torch')
demucs_datas,  demucs_binaries,  demucs_hiddenimports  = collect_all('demucs')
torchaudio_d,  torchaudio_b,     torchaudio_h          = collect_all('torchaudio')
numpy_datas,   numpy_binaries,   numpy_hiddenimports   = collect_all('numpy')

# ── paths ──────────────────────────────────────────────────────────────────
SPEC_DIR   = os.path.dirname(os.path.abspath(SPEC))          # project root
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()                  # bundled ffmpeg

# ── Analysis ───────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(SPEC_DIR, 'main.py')],
    pathex=[SPEC_DIR],

    binaries=[
        # Bundle the imageio-ffmpeg static binary so subprocess calls work
        (FFMPEG_EXE, 'imageio_ffmpeg/binaries'),
        *torch_binaries,
        *demucs_binaries,
        *torchaudio_b,
        *numpy_binaries,
    ],

    datas=[
        *torch_datas,
        *demucs_datas,
        *torchaudio_d,
        *numpy_datas,
        # Application source packages (needed so relative imports resolve)
        (os.path.join(SPEC_DIR, 'core'), 'core'),
        (os.path.join(SPEC_DIR, 'ui'),   'ui'),
        # Assets (icons, fonts) — include if the folder exists
        *([( os.path.join(SPEC_DIR, 'assets'), 'assets')]
          if os.path.isdir(os.path.join(SPEC_DIR, 'assets')) else []),
        # Note: demucs YAML/JSON configs are already collected via collect_all('demucs') above
    ],

    hiddenimports=[
        *torch_hiddenimports,
        *demucs_hiddenimports,
        *torchaudio_h,
        *numpy_hiddenimports,
        # Demucs internals (not always auto-detected)
        'demucs',
        'demucs.apply',
        'demucs.pretrained',
        'demucs.htdemucs',
        'demucs.hdemucs',
        'demucs.states',
        'demucs.utils',
        'demucs.spec',
        'demucs.transformer',
        'demucs.conv',
        'demucs.resample',
        'demucs.repitch',
        'demucs.svd',
        'demucs.diffq',
        # PyTorch
        'torch',
        'torch.nn',
        'torch.nn.functional',
        'torchaudio',
        'torchaudio.functional',
        # Audio I/O
        'soundfile',
        'sounddevice',
        '_sounddevice',
        # yt-dlp (uses lazy imports internally)
        'yt_dlp',
        'yt_dlp.extractor',
        'yt_dlp.extractor.youtube',
        'yt_dlp.postprocessor',
        # Other
        'numpy',
        'scipy',
        'einops',
        'julius',
        'openunmix',
        'tqdm',
        'mutagen',
        'acoustid',
        'imageio_ffmpeg',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        os.path.join(SPEC_DIR, 'hooks', 'rthook_numpy_compat.py'),
    ],

    excludes=[
        # Explicitly exclude things we don't need to keep size down
        'tkinter',
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'setuptools',
        'pip',
        'caffe2',
    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── PYZ archive ────────────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ────────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RehearsalRoom',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress binaries with UPX if available
    console=False,      # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',   # uncomment when you have an icon
)

# ── COLLECT (one-folder build) ─────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RehearsalRoom',
)
