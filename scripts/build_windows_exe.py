#!/usr/bin/env python3
"""
Voiceover Studio Pro - Windows Executable & Portable Release Builder
Downloads Windows FFmpeg/FFprobe binaries if missing, runs PyInstaller, and packages VoiceoverStudio.exe into a clean release ZIP.
"""

import os
import sys
import shutil
import zipfile
import subprocess
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_DIR = os.path.join(PROJECT_ROOT, "bin")
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
VERSION = "0.4.3"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
SFX_BIN = os.path.join(ASSETS_DIR, "7zS.sfx")


def build_windows_packages(version=VERSION):
    os.makedirs(DIST_DIR, exist_ok=True)
    print("=" * 60)
    print(f"📦 Packaging Windows Release for Voiceover Studio Pro v{version}")
    print("=" * 60)

    include_files = ['run_windows.bat', 'requirements.txt', 'README.md', 'VoiceoverStudio.spec', 'start.sh']
    include_dirs = ['backend', 'frontend', 'scripts', 'assets']

    # 1. Windows Portable ZIP
    zip_name = f"VoiceoverStudio-Windows-Portable-v{version}.zip"
    zip_path = os.path.join(DIST_DIR, zip_name)
    folder_prefix = f"VoiceoverStudio-Windows-v{version}"

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in include_files:
            src = os.path.join(PROJECT_ROOT, f)
            if os.path.exists(src):
                z.write(src, os.path.join(folder_prefix, f))
        for d in include_dirs:
            src_d = os.path.join(PROJECT_ROOT, d)
            for root, dirs, files in os.walk(src_d):
                for file in files:
                    if '__pycache__' in root or file.endswith('.pyc') or file == '.DS_Store':
                        continue
                    p = os.path.join(root, file)
                    rel = os.path.relpath(p, PROJECT_ROOT)
                    z.write(p, os.path.join(folder_prefix, rel))

    print(f"✓ Created Windows Portable ZIP: {zip_path} ({os.path.getsize(zip_path)/(1024*1024):.2f} MB)")

    # 2. Windows Standalone Executable (.exe)
    temp_app_dir = os.path.join(DIST_DIR, f"temp_win_payload_v{version}")
    if os.path.exists(temp_app_dir):
        shutil.rmtree(temp_app_dir)
    os.makedirs(temp_app_dir, exist_ok=True)

    for f in include_files:
        src = os.path.join(PROJECT_ROOT, f)
        if os.path.exists(src):
            shutil.copy2(src, temp_app_dir)

    for d in include_dirs:
        src_d = os.path.join(PROJECT_ROOT, d)
        shutil.copytree(src_d, os.path.join(temp_app_dir, d), ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))

    payload_7z = os.path.join(DIST_DIR, f"payload_v{version}.7z")
    if os.path.exists(payload_7z):
        os.remove(payload_7z)

    seven_zip_cmd = shutil.which("7zz") or shutil.which("7z") or "/opt/homebrew/bin/7zz"
    if not os.path.exists(seven_zip_cmd):
        raise FileNotFoundError("7-Zip binary (7zz or 7z) not found.")

    subprocess.check_call([seven_zip_cmd, "a", "-mx=9", payload_7z, f"{temp_app_dir}/*"])

    config_path = os.path.join(DIST_DIR, f"sfx_config_v{version}.txt")
    with open(config_path, "wb") as f:
        f.write(f';!@Install@!UTF-8!\r\nTitle="Voiceover Studio Pro v{version}"\r\nRunProgram="run_windows.bat"\r\n;!@InstallEnd@!\r\n'.encode('utf-8'))

    exe_name = f"VoiceoverStudio-v{version}-Windows.exe"
    out_exe = os.path.join(DIST_DIR, exe_name)

    with open(out_exe, "wb") as dst:
        with open(SFX_BIN, "rb") as src:
            dst.write(src.read())
        with open(config_path, "rb") as src:
            dst.write(src.read())
        with open(payload_7z, "rb") as src:
            dst.write(src.read())

    # Cleanup temporary staging
    if os.path.exists(temp_app_dir):
        shutil.rmtree(temp_app_dir)
    if os.path.exists(payload_7z):
        os.remove(payload_7z)
    if os.path.exists(config_path):
        os.remove(config_path)

    print(f"✓ Created Windows Standalone Executable: {out_exe} ({os.path.getsize(out_exe)/(1024*1024):.2f} MB)")
    return zip_path, out_exe


if __name__ == "__main__":
    ver = sys.argv[1] if len(sys.argv) > 1 else VERSION
    build_windows_packages(ver)
