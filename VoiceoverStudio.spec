# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

added_files = [
    ('frontend', 'frontend'),
    ('backend', 'backend'),
]

# If bin folder has ffmpeg, include it
if os.path.exists('bin'):
    added_files.append(('bin', 'bin'))

a = Analysis(
    ['backend/desktop_app.py'],
    pathex=['backend', '.'],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        'flask',
        'flask_cors',
        'webview',
        'numpy',
        'werkzeug',
        'backend.server',
        'backend.audio_engine',
        'backend.srt_engine',
        'backend.error_diagnostics',
        'backend.aligner.pipeline',
        'backend.aligner.engine',
        'backend.aligner.validator',
        'backend.aligner.srt_builder',
        'backend.aligner.audio_utils',
        'backend.aligner.checkpoint',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceoverStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/AppIcon.ico' if os.path.exists('assets/AppIcon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoiceoverStudio',
)
