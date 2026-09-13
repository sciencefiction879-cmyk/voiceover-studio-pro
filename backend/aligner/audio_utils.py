import os
import shutil
import subprocess
import wave
import struct
import sys
from typing import Tuple

_FFMPEG_EXE = None
_FFPROBE_EXE = None


def sys_is_win() -> bool:
    return os.name == 'nt'


def get_ffmpeg_exe() -> str:
    global _FFMPEG_EXE
    if _FFMPEG_EXE and os.path.exists(_FFMPEG_EXE):
        return _FFMPEG_EXE

    # Check system PATH first
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        _FFMPEG_EXE = system_ffmpeg
        return _FFMPEG_EXE

    # Check bundle location if running in frozen PyInstaller .app
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    bundle_candidates = [
        os.path.join(base_dir, 'ffmpeg'),
        os.path.join(os.path.dirname(sys.executable), 'ffmpeg'),
        os.path.join(base_dir, 'Contents', 'Resources', 'ffmpeg'),
        os.path.join(base_dir, 'Contents', 'MacOS', 'ffmpeg')
    ]
    for p in bundle_candidates:
        if os.path.exists(p) and os.access(p, os.X_OK):
            _FFMPEG_EXE = p
            return _FFMPEG_EXE

    # Common platform paths
    common_paths = []
    if sys_is_win():
        common_paths.extend([
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe'),
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe'),
            r'C:\ffmpeg\bin\ffmpeg.exe',
            r'C:\Program Files\ffmpeg\bin\ffmpeg.exe'
        ])
    else:
        # macOS & Linux paths
        common_paths.extend([
            '/opt/homebrew/bin/ffmpeg',
            '/usr/local/bin/ffmpeg',
            '/opt/local/bin/ffmpeg',
            '/usr/bin/ffmpeg'
        ])

    for p in common_paths:
        if os.path.exists(p):
            _FFMPEG_EXE = p
            return _FFMPEG_EXE

    # Fallback to imageio_ffmpeg if installed
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            _FFMPEG_EXE = exe
            return _FFMPEG_EXE
    except Exception:
        pass

    return ''


def get_ffprobe_exe() -> str:
    global _FFPROBE_EXE
    if _FFPROBE_EXE and os.path.exists(_FFPROBE_EXE):
        return _FFPROBE_EXE

    ffmpeg_bin = get_ffmpeg_exe()
    if ffmpeg_bin:
        dir_name = os.path.dirname(ffmpeg_bin)
        probe_name = 'ffprobe.exe' if sys_is_win() else 'ffprobe'
        probe_cand = os.path.join(dir_name, probe_name)
        if os.path.exists(probe_cand):
            _FFPROBE_EXE = probe_cand
            return _FFPROBE_EXE

    system_probe = shutil.which('ffprobe')
    if system_probe:
        _FFPROBE_EXE = system_probe
        return _FFPROBE_EXE

    for p in ['/opt/homebrew/bin/ffprobe', '/usr/local/bin/ffprobe', '/usr/bin/ffprobe']:
        if os.path.exists(p):
            _FFPROBE_EXE = p
            return _FFPROBE_EXE

    return ''


def verify_audio(file_path: str) -> Tuple[bool, float, str]:
    if not os.path.exists(file_path):
        return (False, 0.0, 'File does not exist')

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        return (False, 0.0, 'File is 0 bytes')

    # Try reading as standard WAV first
    try:
        with wave.open(file_path, 'rb') as wf:
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            dur = float(nframes) / framerate if framerate > 0 else 0.0
            return (True, dur, '')
    except Exception:
        pass

    # Use ffprobe for mp3, m4a, flac, ogg, aac
    ffprobe_bin = get_ffprobe_exe()
    if ffprobe_bin:
        cmd = [
            ffprobe_bin,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': True,
            'timeout': 5
        }
        if sys_is_win():
            kwargs['creationflags'] = 134217728  # CREATE_NO_WINDOW on Windows only

        try:
            res = subprocess.run(cmd, **kwargs)
            if res.returncode == 0 and res.stdout and res.stdout.strip():
                try:
                    dur = float(res.stdout.strip())
                    return (True, dur, '')
                except ValueError:
                    return (True, 0.0, '')
        except Exception:
            pass

    return (True, 0.0, '')


def standardize_audio(input_path: str, output_path: str) -> bool:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ffmpeg_bin = get_ffmpeg_exe()

    if ffmpeg_bin:
        cmd = [
            ffmpeg_bin,
            '-y',
            '-i', input_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ac', '1',
            '-ar', '16000',
            output_path
        ]
        kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': True
        }
        if sys_is_win():
            kwargs['creationflags'] = 134217728

        try:
            res = subprocess.run(cmd, **kwargs)
            if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
        except Exception:
            pass

    # Fallback for native WAV files if ffmpeg is missing
    if input_path.lower().endswith('.wav'):
        try:
            with wave.open(input_path, 'rb') as win:
                n_channels = win.getnchannels()
                sampwidth = win.getsampwidth()
                framerate = win.getframerate()
                n_frames = win.getnframes()
                data = win.readframes(n_frames)

            if n_channels == 1 and sampwidth == 2 and framerate == 16000:
                shutil.copyfile(input_path, output_path)
                return True

            with wave.open(output_path, 'wb') as wout:
                wout.setnchannels(1)
                wout.setsampwidth(2)
                wout.setframerate(16000)
                wout.writeframes(data)
            return True
        except Exception as e:
            raise RuntimeError(f"Native WAV standardization error: {e}")

    raise RuntimeError(
        f"FFmpeg is required to convert {os.path.splitext(input_path)[1]} to WAV. "
        "Please ensure FFmpeg is installed."
    )
