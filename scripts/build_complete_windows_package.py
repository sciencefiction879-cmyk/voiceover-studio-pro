#!/usr/bin/env python3
"""
Voiceover Studio Pro - Complete Standalone Windows Distribution Builder
Bundles:
- Embedded Windows Python 3.11 runtime (python.exe, DLLs)
- Complete pre-installed site-packages (Flask, NumPy, pywebview, pythonnet, docx, etc.)
- Windows FFmpeg & FFprobe binaries (ffmpeg.exe, ffprobe.exe)
- Full backend, frontend, assets
- One-click launcher (.bat) and self-extracting Standalone Executable (.exe)
"""

import os
import sys
import shutil
import zipfile
import subprocess
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(DIST_DIR, "build_win_standalone")
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
SFX_BIN = os.path.join(ASSETS_DIR, "7zS.sfx")
VERSION = "0.4.3"

def build():
    print("=" * 65)
    print(f"🚀 Assembling Complete Standalone Windows Software (v{VERSION})")
    print("=" * 65)

    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)

    pkg_root = os.path.join(BUILD_DIR, f"VoiceoverStudio-Windows-v{VERSION}")
    os.makedirs(pkg_root, exist_ok=True)

    py_dir = os.path.join(pkg_root, "python")
    site_packages = os.path.join(py_dir, "Lib", "site-packages")
    bin_dir = os.path.join(pkg_root, "bin")

    os.makedirs(py_dir, exist_ok=True)
    os.makedirs(site_packages, exist_ok=True)
    os.makedirs(bin_dir, exist_ok=True)

    # 1. Extract embedded Python 3.11
    embed_zip_url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    cache_embed = "/tmp/python-3.11.9-embed-amd64.zip"
    if not os.path.exists(cache_embed):
        print("📥 Downloading Embedded Python 3.11...")
        urllib.request.urlretrieve(embed_zip_url, cache_embed)
    
    print("📦 Unpacking Embedded Python into python/...")
    with zipfile.ZipFile(cache_embed, 'r') as z:
        z.extractall(py_dir)

    # Configure python311._pth to enable Lib/site-packages and imports
    pth_file = os.path.join(py_dir, "python311._pth")
    with open(pth_file, "w", encoding="utf-8") as f:
        f.write("python311.zip\n.\nLib/site-packages\nimport site\n")

    # 2. Extract all pre-downloaded Windows wheels into Lib/site-packages
    wheel_dir = "/tmp/test_win_wheels"
    print(f"📦 Unpacking Windows packages into python/Lib/site-packages/...")
    for whl in os.listdir(wheel_dir):
        if whl.endswith(".whl"):
            whl_path = os.path.join(wheel_dir, whl)
            with zipfile.ZipFile(whl_path, 'r') as z:
                z.extractall(site_packages)

    # 3. Copy Windows FFmpeg and FFprobe into bin/ and root
    print("📦 Installing Windows FFmpeg & FFprobe binaries...")
    ffmpeg_src = "/tmp/test_win_ffmpeg/ffmpeg.exe"
    ffprobe_src = "/tmp/test_win_ffmpeg/ffprobe.exe"
    shutil.copy2(ffmpeg_src, os.path.join(bin_dir, "ffmpeg.exe"))
    shutil.copy2(ffprobe_src, os.path.join(bin_dir, "ffprobe.exe"))

    # 4. Copy backend, frontend, assets
    print("📦 Copying application source and interface...")
    shutil.copytree(os.path.join(PROJECT_ROOT, "backend"), os.path.join(pkg_root, "backend"),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    shutil.copytree(os.path.join(PROJECT_ROOT, "frontend"), os.path.join(pkg_root, "frontend"),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    shutil.copytree(os.path.join(PROJECT_ROOT, "assets"), os.path.join(pkg_root, "assets"),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    shutil.copy2(os.path.join(PROJECT_ROOT, "README.md"), os.path.join(pkg_root, "README.md"))

    # 5. Create Standalone Launchers (.bat)
    launcher_bat = os.path.join(pkg_root, "VoiceoverStudio.bat")
    with open(launcher_bat, "w", encoding="utf-8") as f:
        f.write("""@echo off
title Voiceover Studio Pro
cd /d "%~dp0"
echo ===================================================
echo   Starting Voiceover Studio Pro
echo ===================================================
set "PATH=%~dp0bin;%~dp0python;%PATH%"
"%~dp0python\\python.exe" "%~dp0backend\\desktop_app.py"
if %errorlevel% neq 0 (
    echo.
    echo Application exited.
    pause
)
""")

    # Also keep run_windows.bat as identical alias
    shutil.copy2(launcher_bat, os.path.join(pkg_root, "run_windows.bat"))

    # 6. Create Complete Standalone ZIP
    print("📦 Compressing Complete Standalone Portable ZIP...")
    zip_out = os.path.join(DIST_DIR, f"VoiceoverStudio-Windows-Portable-v{VERSION}.zip")
    if os.path.exists(zip_out):
        os.remove(zip_out)

    seven_zip = shutil.which("7zz") or shutil.which("7z") or "/opt/homebrew/bin/7zz"
    subprocess.check_call([seven_zip, "a", "-mx=7", zip_out, f"{BUILD_DIR}/*"])
    print(f"✅ Created Standalone ZIP: {zip_out} ({os.path.getsize(zip_out)/(1024*1024):.2f} MB)")

    # 7. Create Self-Extracting Standalone Executable (.exe)
    print("📦 Creating Self-Extracting Standalone Windows Executable (.exe)...")
    payload_7z = os.path.join(BUILD_DIR, "payload.7z")
    subprocess.check_call([seven_zip, "a", "-mx=7", payload_7z, f"{pkg_root}/*"])

    sfx_cfg = os.path.join(BUILD_DIR, "sfx_config.txt")
    with open(sfx_cfg, "wb") as f:
        f.write(f';!@Install@!UTF-8!\r\nTitle="Voiceover Studio Pro v{VERSION}"\r\nRunProgram="VoiceoverStudio.bat"\r\n;!@InstallEnd@!\r\n'.encode('utf-8'))

    exe_out = os.path.join(DIST_DIR, f"VoiceoverStudio-v{VERSION}-Windows.exe")
    with open(exe_out, "wb") as dst:
        with open(SFX_BIN, "rb") as s:
            dst.write(s.read())
        with open(sfx_cfg, "rb") as s:
            dst.write(s.read())
        with open(payload_7z, "rb") as s:
            dst.write(s.read())

    print(f"✅ Created Standalone Executable: {exe_out} ({os.path.getsize(exe_out)/(1024*1024):.2f} MB)")

    # Cleanup temporary build folder
    shutil.rmtree(BUILD_DIR)
    print("🎉 All complete Windows standalone packages successfully built!")

if __name__ == "__main__":
    build()
