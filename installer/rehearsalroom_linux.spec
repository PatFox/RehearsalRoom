# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rehearsal Room — Linux.

Produces:  dist/RehearsalRoom/  (one-folder build; RehearsalRoom executable
inside). The CI workflow packages this into an AppImage and a .tar.gz.
"""

import os
import re
from pathlib import Path

block_cipher = None

# Collect entire packages whose internals PyInstaller can't trace statically.
from PyInstaller.utils.hooks import collect_all, collect_data_files
torch_d,    torch_b,    torch_h    = collect_all('torch')
demucs_d,   demucs_b,   demucs_h   = collect_all('demucs')
torchaudio_d, torchaudio_b, torchaudio_h = collect_all('torchaudio')
numpy_d,    numpy_b,    numpy_h    = collect_all('numpy')

# certifi's cacert.pem — bundled so HTTPS works without relying on the host's
# trust store layout (see hooks/rthook_ssl_certs.py).
certifi_datas = collect_data_files('certifi')

# ── Size trim #1: drop compile-time-only static libs (*.a) ───────────────────
# collect_all('torch') sweeps in static archives that are link-time artifacts
# and never loaded at runtime (runtime linking uses .so on Linux).
def _drop_static_libs(pairs):
    return [(src, dest) for (src, dest) in pairs
            if not str(src).lower().endswith(('.a', '.lib'))]

torch_b      = _drop_static_libs(torch_b)
torch_d      = _drop_static_libs(torch_d)
torchaudio_b = _drop_static_libs(torchaudio_b)
torchaudio_d = _drop_static_libs(torchaudio_d)
numpy_b      = _drop_static_libs(numpy_b)
numpy_d      = _drop_static_libs(numpy_d)
demucs_b     = _drop_static_libs(demucs_b)
demucs_d     = _drop_static_libs(demucs_d)

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
        *certifi_datas,
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
        'numpy', 'einops', 'julius', 'tqdm',
        'mutagen', 'acoustid', 'imageio_ffmpeg', 'certifi',
    ],

    runtime_hooks=[
        os.path.join(SPEC_DIR, '..', 'hooks', 'rthook_ssl_certs.py'),
        os.path.join(SPEC_DIR, '..', 'hooks', 'rthook_numpy_compat.py'),
    ],

    hookspath=[],
    excludes=[
        'tkinter', 'matplotlib', 'IPython', 'jupyter', 'caffe2',
        # Size trim #2: numba + llvmlite — transitive optional dep, not on the
        # demucs/torchaudio separation path. (~102 MB on Windows.)
        'numba', 'llvmlite',
        # Size trim #4: scipy — only referenced by bundled test files and
        # torch training-only paths; never on the separation path. (~73 MB.)
        'scipy',
        # Size trim #3: Qt modules we don't use (pure QtWidgets app).
        'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.Qt3DCore', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
    ],
    cipher=block_cipher,
    noarchive=False,
)

# ── Size trim #3 (cont.): strip bundled Qt binaries/data we don't use ────────
# Module excludes stop Python imports, but PyInstaller's PySide6 hook still
# copies these libs and the full translations folder. The substring patterns
# match Linux lib names too (e.g. libQt6Quick.so.6).
_QT_DROP = re.compile(
    r'(opengl32sw\.dll'
    r'|Qt6?Quick|Qt6?Qml'
    r'|Qt6?Pdf|Qt6?WebEngine|Qt6?Multimedia'
    r'|Qt6?3D|Qt6?Charts|Qt6?DataVisualization'
    r'|[\\/]translations[\\/])',
    re.IGNORECASE,
)
a.binaries = [b for b in a.binaries if not _QT_DROP.search(b[0])]
a.datas    = [d for d in a.datas    if not _QT_DROP.search(d[0])]

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
    # icon=...,  # Linux uses the .desktop/.png in the AppImage, not an EXE icon
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
