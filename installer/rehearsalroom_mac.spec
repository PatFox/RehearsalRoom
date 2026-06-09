# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rehearsal Room — macOS.

Produces:  dist/RehearsalRoom.app
"""

import os
from pathlib import Path

block_cipher = None

# Collect entire packages whose internals PyInstaller can't trace statically.
from PyInstaller.utils.hooks import collect_all
torch_d,    torch_b,    torch_h    = collect_all('torch')
demucs_d,   demucs_b,   demucs_h   = collect_all('demucs')
torchaudio_d, torchaudio_b, torchaudio_h = collect_all('torchaudio')
numpy_d,    numpy_b,    numpy_h    = collect_all('numpy')

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

import imageio_ffmpeg
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    [os.path.join(SPEC_DIR, '..', 'main.py')],
    pathex=[os.path.join(SPEC_DIR, '..')],

    binaries=[
        (FFMPEG_EXE, 'imageio_ffmpeg/binaries'),
        *torch_b,
        *demucs_b,
        *torchaudio_b,
        *numpy_b,
    ],

    datas=[
        *torch_d,
        *demucs_d,
        *torchaudio_d,
        *numpy_d,
        (os.path.join(SPEC_DIR, '..', 'core'), 'core'),
        (os.path.join(SPEC_DIR, '..', 'ui'),   'ui'),
        *([( os.path.join(SPEC_DIR, '..', 'assets'), 'assets')]
          if os.path.isdir(os.path.join(SPEC_DIR, '..', 'assets')) else []),
    ],

    hiddenimports=[
        *torch_h, *demucs_h, *torchaudio_h, *numpy_h,
        'demucs', 'demucs.apply', 'demucs.pretrained', 'demucs.htdemucs',
        'demucs.hdemucs', 'demucs.states', 'demucs.utils', 'demucs.spec',
        'demucs.transformer', 'demucs.conv', 'demucs.resample',
        'torch', 'torch.nn', 'torch.nn.functional',
        'torchaudio', 'torchaudio.functional',
        'soundfile', 'sounddevice', '_sounddevice',
        'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.extractor.youtube',
        'yt_dlp.postprocessor',
        'numpy', 'scipy', 'einops', 'julius', 'tqdm',
        'mutagen', 'acoustid', 'imageio_ffmpeg',
    ],

    runtime_hooks=[
        os.path.join(SPEC_DIR, '..', 'hooks', 'rthook_numpy_compat.py'),
    ],

    hookspath=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'jupyter', 'caffe2'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RehearsalRoom',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    # icon='../assets/icons/app.icns',  # uncomment when you have a .icns icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='RehearsalRoom',
)

# macOS .app bundle
app = BUNDLE(
    coll,
    name='RehearsalRoom.app',
    # icon='../assets/icons/app.icns',  # uncomment when you have a .icns icon
    bundle_identifier='com.patfox.rehearsalroom',
    info_plist={
        'CFBundleName':               'Rehearsal Room',
        'CFBundleDisplayName':        'Rehearsal Room',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion':            '1.0.0',
        'NSHighResolutionCapable':    True,
        'NSRequiresAquaSystemAppearance': False,  # supports dark mode
        # Audio playback doesn't require mic; this silences a macOS warning
        'NSMicrophoneUsageDescription': '',
    },
)
