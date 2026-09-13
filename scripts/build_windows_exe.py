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
RELEASE_NAME = "VoiceoverStudio-Windows-v0.2.0"

FFMPEG_WIN_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

def ensure_windows_ffmpeg():
    os.makedirs(BIN_DIR, exist_ok=True)
    ffmpeg_exe = os.path.join(BIN_DIR, "ffmpeg.exe")
    ffprobe_exe = os.path.join(BIN_DIR, "ffprobe.exe")

    if os.path.exists(ffmpeg_exe) and os.path.exists(ffprobe_exe):
        print("✓ Windows FFmpeg and FFprobe binaries are already present in bin/")
        return

    print("⬇️  Downloading Windows FFmpeg build...")
    zip_tmp = os.path.join(BIN_DIR, "ffmpeg_win.zip")
    try:
        urllib.request.urlretrieve(FFMPEG_WIN_URL, zip_tmp)
        print("📦 Extracting ffmpeg.exe and ffprobe.exe...")
        with zipfile.ZipFile(zip_tmp, "r") as z:
            for member in z.namelist():
                if member.endswith("ffmpeg.exe"):
                    with z.open(member) as src, open(ffmpeg_exe, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                elif member.endswith("ffprobe.exe"):
                    with z.open(member) as src, open(ffprobe_exe, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        if os.path.exists(zip_tmp):
            os.remove(zip_tmp)
        print("✓ Windows FFmpeg successfully installed into bin/")
    except Exception as e:
        print(f"⚠️ Could not auto-download FFmpeg: {e}. Please ensure ffmpeg.exe and ffprobe.exe are in bin/ or PATH.")

def run_pyinstaller():
    print("\n🔨 Building VoiceoverStudio.exe with PyInstaller...")
    spec_file = os.path.join(PROJECT_ROOT, "VoiceoverStudio.spec")
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", spec_file], cwd=PROJECT_ROOT)
    print("✅ PyInstaller build complete.")

def package_zip():
    print(f"\n📦 Packaging {RELEASE_NAME}.zip...")
    out_dir = os.path.join(DIST_DIR, "VoiceoverStudio")
    zip_path = os.path.join(DIST_DIR, f"{RELEASE_NAME}.zip")

    if not os.path.exists(out_dir):
        raise FileNotFoundError(f"Expected output directory {out_dir} does not exist.")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(out_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, DIST_DIR)
                z.write(full_path, rel_path)

    print(f"🎉 Successfully created Windows release package:\n   Path: {zip_path}\n   Size: {os.path.getsize(zip_path)/(1024*1024):.2f} MB")
    return zip_path

def main():
    print("=" * 60)
    print("Voiceover Studio Pro - Windows Executable Build Pipeline")
    print("=" * 60)
    ensure_windows_ffmpeg()
    run_pyinstaller()
    package_zip()

if __name__ == "__main__":
    main()
