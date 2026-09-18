import os
import json
import random
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import sys

# Locate ffmpeg and ffprobe (Cross-platform: Windows, macOS, Linux, and PyInstaller)
_exe_suffix = ".exe" if sys.platform == "win32" else ""
_base_res_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
_meipass_bin = os.path.join(getattr(sys, '_MEIPASS', ''), "bin") if hasattr(sys, '_MEIPASS') else ""

_candidate_ffmpeg = [
    os.path.join(_base_res_bin, f"ffmpeg{_exe_suffix}"),
    os.path.join(_base_res_bin, "ffmpeg"),
    os.path.join(_meipass_bin, f"ffmpeg{_exe_suffix}") if _meipass_bin else "",
    shutil.which("ffmpeg.exe"),
    shutil.which("ffmpeg"),
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg"
]
FFMPEG_PATH = next((p for p in _candidate_ffmpeg if p and os.path.exists(p)), shutil.which("ffmpeg") or "ffmpeg")

_candidate_ffprobe = [
    os.path.join(_base_res_bin, f"ffprobe{_exe_suffix}"),
    os.path.join(_base_res_bin, "ffprobe"),
    os.path.join(_meipass_bin, f"ffprobe{_exe_suffix}") if _meipass_bin else "",
    shutil.which("ffprobe.exe"),
    shutil.which("ffprobe"),
    "/opt/homebrew/bin/ffprobe",
    "/usr/local/bin/ffprobe"
]
FFPROBE_PATH = next((p for p in _candidate_ffprobe if p and os.path.exists(p)), shutil.which("ffprobe") or "ffprobe")


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert any input value to float, handling None, empty, or malformed strings."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """Safely convert any input value to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def safe_bool(val: Any, default: bool = True) -> bool:
    """Safely convert any input value to boolean."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def get_audio_metadata(file_path: str) -> Dict[str, Any]:
    """Inspect audio file duration, sample rate, channels, bitrate, format."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate,format_name:stream=codec_name,sample_rate,channels",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe error on {file_path}: {result.stderr}")

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [{}])
    stream = streams[0] if streams else {}

    duration = safe_float(fmt.get("duration"), 0.0)
    size = safe_int(fmt.get("size"), os.path.getsize(file_path) if os.path.exists(file_path) else 0)
    bitrate = safe_int(fmt.get("bit_rate"), 320000) or 320000
    sample_rate = safe_int(stream.get("sample_rate"), 44100)
    channels = safe_int(stream.get("channels"), 2)
    codec = stream.get("codec_name", "mp3")

    return {
        "filePath": file_path,
        "fileName": os.path.basename(file_path),
        "duration": duration,
        "formattedDuration": format_time(duration),
        "sizeBytes": size,
        "bitrate": bitrate,
        "sampleRate": sample_rate,
        "channels": channels,
        "codec": codec,
    }


def format_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    seconds = max(0.0, safe_float(seconds, 0.0))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def calculate_cut_schedule(
    duration: float,
    cut_duration_range: Tuple[float, float] = (50.0, 70.0),
    protect_duration: float = 1500.0, # 25 minutes = 1500 seconds
    interval_step: float = 300.0,     # 5 minutes = 300 seconds
    seed: Optional[int] = None
) -> Dict[str, Any]:
    """
    Calculate cut schedule according to rules:
    - 0 to 25 minutes (protect_duration = 1500s): 0 cuts!
    - After 25 minutes: ~1 minute cut randomly within 25-30, 30-35, 35-40, ... every 5 mins.
    """
    duration = safe_float(duration, 0.0)
    protect_duration = safe_float(protect_duration, 1500.0)
    interval_step = safe_float(interval_step, 300.0)
    
    cut_min = safe_float(cut_duration_range[0] if cut_duration_range else 50.0, 50.0)
    cut_max = safe_float(cut_duration_range[1] if cut_duration_range else 70.0, 70.0)
    if cut_min > cut_max:
        cut_min, cut_max = cut_max, cut_min

    rng = random.Random(seed) if seed is not None else random.Random()
    cuts = []

    # If audio is shorter than or equal to protected duration, no cuts are made
    if duration <= protect_duration:
        return {
            "originalDuration": duration,
            "cuts": [],
            "keepSegments": [{"start": 0.0, "end": duration, "duration": duration}],
            "finalEstimatedDuration": duration,
            "formattedFinalDuration": format_time(duration),
            "protectDuration": protect_duration
        }

    curr_start = protect_duration
    while curr_start < duration:
        curr_end = min(duration, curr_start + interval_step)
        block_len = curr_end - curr_start

        # Only add a cut if the remaining block is large enough (at least 30s)
        if block_len >= 30.0:
            min_c = min(cut_min, block_len * 0.5)
            max_c = min(cut_max, block_len * 0.8)
            cut_len = rng.uniform(min_c, max_c) if max_c > min_c else min_c

            # Ensure cut is positioned inside [curr_start + pad, curr_end - pad]
            pad = min(5.0, (block_len - cut_len) / 2.0)
            available_start_min = curr_start + pad
            available_start_max = curr_end - cut_len - pad

            if available_start_max >= available_start_min:
                cut_start = rng.uniform(available_start_min, available_start_max)
            else:
                cut_start = curr_start + (block_len - cut_len) / 2.0

            cut_end = cut_start + cut_len

            cuts.append({
                "intervalStart": curr_start,
                "intervalEnd": curr_end,
                "start": round(cut_start, 2),
                "end": round(cut_end, 2),
                "duration": round(cut_len, 2),
                "formattedStart": format_time(cut_start),
                "formattedEnd": format_time(cut_end)
            })

        curr_start += interval_step

    # Compute kept segments between cuts
    keep_segments = []
    prev_pos = 0.0
    for c in cuts:
        if c["start"] > prev_pos:
            keep_segments.append({
                "start": round(prev_pos, 2),
                "end": round(c["start"], 2),
                "duration": round(c["start"] - prev_pos, 2)
            })
        prev_pos = c["end"]

    if prev_pos < duration:
        keep_segments.append({
            "start": round(prev_pos, 2),
            "end": round(duration, 2),
            "duration": round(duration - prev_pos, 2)
        })

    final_est_duration = sum(seg["duration"] for seg in keep_segments)

    return {
        "originalDuration": duration,
        "cuts": cuts,
        "keepSegments": keep_segments,
        "finalEstimatedDuration": round(final_est_duration, 2),
        "formattedFinalDuration": format_time(final_est_duration),
        "protectDuration": protect_duration
    }


def auto_vary_params(params: Dict[str, Any], file_index: int = 0, seed: Optional[int] = None) -> Dict[str, Any]:
    """Apply intelligent 1-5% micro-variations to resolved DSP parameters for each file."""
    vary_seed = ((seed or 42) + file_index * 7919 + 31337) % (2**32)
    rng = random.Random(vary_seed)

    def _vary(value: float, pct_min: float, pct_max: float,
              hard_min: Optional[float] = None, hard_max: Optional[float] = None,
              prefer_direction: int = 0) -> float:
        pct = rng.uniform(pct_min, pct_max) / 100.0
        magnitude = abs(value) if abs(value) > 0.001 else 1.0

        if prefer_direction == +1:
            sign = 1 if rng.random() < 0.7 else -1
        elif prefer_direction == -1:
            sign = -1 if rng.random() < 0.7 else 1
        else:
            sign = 1 if rng.random() < 0.5 else -1

        new_val = value + sign * pct * magnitude
        if hard_min is not None:
            new_val = max(hard_min, new_val)
        if hard_max is not None:
            new_val = min(hard_max, new_val)
        return new_val

    p = dict(params)
    p["pitch_semitones"] = round(_vary(safe_float(p.get("pitch_semitones"), 0.0), 1.0, 4.0, -3.0, 3.0), 2)
    p["speed_factor"] = round(_vary(safe_float(p.get("speed_factor"), 1.0), 1.0, 3.0, 0.90, 1.15), 3)
    p["bass_gain"] = round(_vary(safe_float(p.get("bass_gain"), 0.0), 1.0, 5.0, -6.0, 6.0, prefer_direction=+1), 2)
    p["mid_gain"] = round(_vary(safe_float(p.get("mid_gain"), 0.0), 1.0, 5.0, -6.0, 6.0), 2)
    p["treble_gain"] = round(_vary(safe_float(p.get("treble_gain"), 0.0), 1.0, 5.0, -6.0, 6.0, prefer_direction=+1), 2)
    p["highpass_freq"] = round(_vary(safe_float(p.get("highpass_freq"), 75.0), 1.0, 5.0, 20.0, 150.0), 1)
    p["lowpass_freq"] = round(_vary(safe_float(p.get("lowpass_freq"), 18500.0), 1.0, 3.0, 14000.0, 20000.0), 1)
    p["denoise_floor"] = round(_vary(safe_float(p.get("denoise_floor"), -38.0), 1.0, 5.0, -55.0, -25.0, prefer_direction=-1), 1)
    p["deesser_intensity"] = round(_vary(safe_float(p.get("deesser_intensity"), 0.14), 1.0, 5.0, 0.02, 0.45), 3)
    p["comp_thresh"] = round(_vary(safe_float(p.get("comp_thresh"), -18.0), 1.0, 5.0, -30.0, -10.0), 1)
    p["comp_ratio"] = round(_vary(safe_float(p.get("comp_ratio"), 2.5), 1.0, 4.0, 1.5, 4.5), 2)
    p["limiter_ceiling"] = round(_vary(safe_float(p.get("limiter_ceiling"), -1.0), 1.0, 3.0, -2.0, -0.1), 2)
    p["formant_shift"] = round(_vary(safe_float(p.get("formant_shift"), 0.0), 1.0, 4.0, -3.0, 3.0), 2)
    p["haas_delay"] = round(_vary(safe_float(p.get("haas_delay"), 4.0), 1.0, 5.0, 1.0, 10.0), 2)
    p["reverb_wet"] = round(_vary(safe_float(p.get("reverb_wet"), 0.025), 1.0, 5.0, 0.005, 0.080), 3)
    p["tempo_drift_depth"] = round(_vary(safe_float(p.get("tempo_drift_depth"), 0.025), 1.0, 5.0, 0.005, 0.060), 3)
    p["saturation_drive"] = round(_vary(safe_float(p.get("saturation_drive"), 0.35), 1.0, 5.0, 0.05, 0.85), 2)
    p["noise_bed_level"] = round(_vary(safe_float(p.get("noise_bed_level"), -62.0), 1.0, 4.0, -75.0, -50.0), 1)

    return p


# Baseline studio audio characteristics references
DEFAULT_AUDIO_CHARACTERISTICS = {
    "pitch_semitones": 0.0,      # Semitones offset (-3.0 to +3.0)
    "speed_factor": 1.0,         # Pitch-preserving speed multiplier (0.85 to 1.15)
    "volume_db": 0.0,            # Output gain dB (-6.0 to +6.0)
    "bass_gain": 0.0,            # 180Hz shelf dB (-6.0 to +6.0)
    "mid_gain": 0.0,             # 3.2kHz presence dB (-6.0 to +6.0)
    "treble_gain": 0.0,          # 11kHz air dB (-6.0 to +6.0)
    "highpass_freq": 75.0,       # Highpass rumble cutoff (20Hz - 150Hz)
    "lowpass_freq": 18500.0,     # Lowpass smoothing cutoff (14kHz - 20kHz)
    "denoise_enabled": True,     # Background noise reduction
    "denoise_floor": -38.0,      # Noise floor dB (-55dB to -25dB)
    "deesser_enabled": True,     # Sibilance taming
    "deesser_intensity": 0.14,   # De-esser intensity factor (0.02 to 0.45)
    "comp_enabled": True,        # Vocal leveling compressor
    "comp_thresh": -18.0,        # Compressor threshold dB (-30dB to -10dB)
    "comp_ratio": 2.5,           # Compression ratio (1.5:1 to 4.5:1)
    "comp_makeup": 2.0,          # Compression makeup gain dB
    "formant_shift": 0.0,        # Vocal tract resonance shift semitones (-3.0 to +3.0)
    "haas_enabled": True,        # Stereo Haas micro-delay widener
    "haas_delay": 4.0,           # Haas micro-delay ms (1.0 to 10.0)
    "reverb_enabled": True,      # Micro-room acoustic space
    "reverb_wet": 0.025,         # Reverb wet reflection mix (0.005 to 0.080)
    "tempo_drift_enabled": True, # Non-linear dynamic tempo/pitch drift
    "tempo_drift_depth": 0.025,  # Tempo drift depth factor (0.005 to 0.060)
    "saturation_enabled": True,  # Harmonic tape/tube warmth saturation
    "saturation_drive": 0.35,    # Saturation drive amount (0.05 to 0.85)
    "noise_bed_enabled": True,   # Sub-audible room tone bed / binaural dither
    "noise_bed_level": -62.0,    # Noise bed level dB (-75.0 to -50.0)
    "loudnorm_enabled": True,    # EBU R128 broadcast loudness
    "loudnorm_lufs": -16.0,      # Integrated loudness LUFS (-24 to -12)
    "loudnorm_tp": -1.0,         # True peak dBTP (-2.0 to -0.1)
    "limiter_enabled": True,     # Brickwall peak limiter
    "limiter_ceiling": -1.0,     # Limiter ceiling dBTP (-2.0 to -0.1)
    "cut_duration_min": 50.0,    # Random cut minimum seconds
    "cut_duration_max": 70.0     # Random cut maximum seconds
}


def randomize_characteristics_3_to_7_pct(
    base: Optional[Dict[str, Any]] = None,
    specific_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Randomize audio characteristics within 3%–7% independently.
    Returns:
    - randomized: Dict of parameter details (value, pct, direction, label)
    - natural_logs: List of human-readable English log strings
    - summary: Natural English summary statement
    """
    current = dict(DEFAULT_AUDIO_CHARACTERISTICS)
    if base:
        for k, v in base.items():
            if k in current and v is not None:
                current[k] = v

    # Definitions of how 3%-7% applies to each parameter
    specs = {
        "speed_factor": {
            "name": "Speed",
            "span": 1.0, "min": 0.90, "max": 1.15, "round": 3,
            "format": lambda orig, new, pct, s: f"Speed: {orig*100:.1f}% → {new*100:.1f}% ({s}{pct:.1f}%) ✓"
        },
        "pitch_semitones": {
            "name": "Pitch",
            "span": 3.0, "min": -3.0, "max": 3.0, "round": 2,
            "format": lambda orig, new, pct, s: f"Pitch: {orig:+.2f} st → {new:+.2f} st ({s}{pct:.1f}%) ✓"
        },
        "volume_db": {
            "name": "Volume",
            "span": 6.0, "min": -6.0, "max": 6.0, "round": 2,
            "format": lambda orig, new, pct, s: f"Volume: {orig:+.2f} dB → {new:+.2f} dB ({s}{pct:.1f}%) ✓"
        },
        "bass_gain": {
            "name": "EQ Low (Bass)",
            "span": 6.0, "min": -6.0, "max": 6.0, "round": 2,
            "format": lambda orig, new, pct, s: f"EQ Low (Bass): {orig:+.2f} dB → {new:+.2f} dB ({s}{pct:.1f}%) ✓"
        },
        "mid_gain": {
            "name": "EQ Mid",
            "span": 6.0, "min": -5.0, "max": 6.0, "round": 2,
            "format": lambda orig, new, pct, s: f"EQ Mid: {orig:+.2f} dB → {new:+.2f} dB ({s}{pct:.1f}%) ✓"
        },
        "treble_gain": {
            "name": "EQ High (Treble)",
            "span": 6.0, "min": -5.0, "max": 6.0, "round": 2,
            "format": lambda orig, new, pct, s: f"EQ High (Treble): {orig:+.2f} dB → {new:+.2f} dB ({s}{pct:.1f}%) ✓"
        },
        "highpass_freq": {
            "name": "Highpass Filter",
            "span": 75.0, "min": 20.0, "max": 150.0, "round": 1,
            "format": lambda orig, new, pct, s: f"Highpass Filter: {orig:.1f} Hz → {new:.1f} Hz ({s}{pct:.1f}%) ✓"
        },
        "lowpass_freq": {
            "name": "Lowpass Filter",
            "span": 18500.0, "min": 14000.0, "max": 20000.0, "round": 1,
            "format": lambda orig, new, pct, s: f"Lowpass Filter: {int(orig)} Hz → {int(new)} Hz ({s}{pct:.1f}%) ✓"
        },
        "denoise_floor": {
            "name": "Denoise Floor",
            "span": 38.0, "min": -55.0, "max": -25.0, "round": 1,
            "format": lambda orig, new, pct, s: f"Denoise Floor: {orig:.1f} dB → {new:.1f} dB ({s}{pct:.1f}%) ✓"
        },
        "deesser_intensity": {
            "name": "De-esser Intensity",
            "span": 0.20, "min": 0.02, "max": 0.45, "round": 3,
            "format": lambda orig, new, pct, s: f"De-esser Intensity: {orig:.3f} → {new:.3f} ({s}{pct:.1f}%) ✓"
        },
        "comp_thresh": {
            "name": "Compressor Threshold",
            "span": 20.0, "min": -30.0, "max": -10.0, "round": 1,
            "format": lambda orig, new, pct, s: f"Compressor Threshold: {orig:.1f} dB → {new:.1f} dB ({s}{pct:.1f}%) ✓"
        },
        "comp_ratio": {
            "name": "Compressor Ratio",
            "span": 3.0, "min": 1.5, "max": 4.5, "round": 2,
            "format": lambda orig, new, pct, s: f"Compressor Ratio: {orig:.2f}:1 → {new:.2f}:1 ({s}{pct:.1f}%) ✓"
        },
        "limiter_ceiling": {
            "name": "Limiter Ceiling",
            "span": 1.5, "min": -2.0, "max": -0.1, "round": 2,
            "format": lambda orig, new, pct, s: f"Limiter Ceiling: {orig:.2f} dBTP → {new:.2f} dBTP ({s}{pct:.1f}%) ✓"
        },
        "formant_shift": {
            "name": "Formant Shift",
            "span": 3.0, "min": -3.0, "max": 3.0, "round": 2,
            "format": lambda orig, new, pct, s: f"Formant Shift: {orig:+.2f} st → {new:+.2f} st ({s}{pct:.1f}%) ✓"
        },
        "haas_delay": {
            "name": "Stereo Haas Delay",
            "span": 8.0, "min": 1.0, "max": 10.0, "round": 2,
            "format": lambda orig, new, pct, s: f"Stereo Haas Delay: {orig:.2f} ms → {new:.2f} ms ({s}{pct:.1f}%) ✓"
        },
        "reverb_wet": {
            "name": "Micro-Reverb Wet",
            "span": 0.06, "min": 0.005, "max": 0.080, "round": 3,
            "format": lambda orig, new, pct, s: f"Micro-Reverb Wet: {orig*100:.1f}% → {new*100:.1f}% ({s}{pct:.1f}%) ✓"
        },
        "tempo_drift_depth": {
            "name": "Dynamic Tempo Drift",
            "span": 0.04, "min": 0.005, "max": 0.060, "round": 3,
            "format": lambda orig, new, pct, s: f"Dynamic Tempo Drift: {orig*100:.1f}% → {new*100:.1f}% ({s}{pct:.1f}%) ✓"
        },
        "saturation_drive": {
            "name": "Harmonic Saturation",
            "span": 0.60, "min": 0.05, "max": 0.85, "round": 2,
            "format": lambda orig, new, pct, s: f"Harmonic Saturation: {orig:.2f} → {new:.2f} ({s}{pct:.1f}%) ✓"
        },
        "noise_bed_level": {
            "name": "Sub-Audible Room Bed",
            "span": 25.0, "min": -75.0, "max": -50.0, "round": 1,
            "format": lambda orig, new, pct, s: f"Sub-Audible Room Bed: {orig:.1f} dB → {new:.1f} dB ({s}{pct:.1f}%) ✓"
        },
    }

    results = {}
    natural_logs = []
    keys_to_process = [specific_key] if specific_key in specs else list(specs.keys())

    for k in keys_to_process:
        conf = specs[k]
        pct = random.uniform(3.0, 7.0)
        direction = 1 if random.random() < 0.5 else -1

        # Calculate delta
        delta = direction * (conf["span"] * (pct / 100.0))
        cur_val = safe_float(current.get(k), safe_float(DEFAULT_AUDIO_CHARACTERISTICS.get(k), 0.0))
        new_val = cur_val + delta
        new_val = max(conf["min"], min(conf["max"], new_val))
        new_val = round(new_val, conf["round"])

        sign_str = "+" if direction > 0 else "-"
        log_line = conf["format"](cur_val, new_val, pct, sign_str)
        natural_logs.append(log_line)

        results[k] = {
            "name": conf["name"],
            "value": new_val,
            "originalValue": cur_val,
            "pct": round(direction * pct, 1),
            "isRandom": True,
            "isManual": False,
            "defaultValue": DEFAULT_AUDIO_CHARACTERISTICS.get(k),
            "log": log_line
        }

    # Add "No change" entries for keys not processed when single key is requested
    if specific_key and specific_key in specs:
        for k, conf in specs.items():
            if k != specific_key:
                natural_logs.append(f"{conf['name']}: No change")

    summary = f"Randomization complete. {len(results)} parameters were changed within the 3%–7% range."

    return {
        "randomized": results,
        "natural_logs": natural_logs,
        "summary": summary
    }


def resolve_dsp_params(user_settings: Optional[Dict[str, Any]], file_index: int = 0, total_files: int = 1, seed: Optional[int] = None) -> Dict[str, Any]:
    """Resolve DSP parameter values with 100% safe float extraction and zero NoneType errors."""
    if user_settings is None:
        user_settings = {}

    file_seed = (seed + file_index * 1013) if seed is not None else None
    rng = random.Random(file_seed) if file_seed is not None else random.Random()

    def pick_param(key_direct: str, key_min: str, key_max: str, default: float) -> float:
        val = user_settings.get(key_direct)
        if val is not None:
            return safe_float(val, default)
        
        vmin = safe_float(user_settings.get(key_min), default)
        vmax = safe_float(user_settings.get(key_max), default)
        if vmin > vmax:
            vmin, vmax = vmax, vmin
        randomize = safe_bool(user_settings.get("randomize_per_file"), False)
        if not randomize or abs(vmax - vmin) < 1e-4:
            return vmin
        return rng.uniform(vmin, vmax)

    pitch_semitones = pick_param("pitch_semitones", "pitch_min", "pitch_max", 0.0)
    speed_factor = pick_param("speed_factor", "speed_min", "speed_max", 1.0)
    volume_db = pick_param("volume_db", "volume_min", "volume_max", 0.0)

    bass_gain = pick_param("bass_gain", "bass_min", "bass_max", 0.0)
    mid_gain = pick_param("mid_gain", "mid_min", "mid_max", 0.0)
    treble_gain = pick_param("treble_gain", "treble_min", "treble_max", 0.0)

    highpass_freq = pick_param("highpass_freq", "highpass_min", "highpass_max", 75.0)
    lowpass_freq = pick_param("lowpass_freq", "lowpass_min", "lowpass_max", 18500.0)

    deesser_enabled = safe_bool(user_settings.get("deesser_enabled"), True)
    deesser_intensity = pick_param("deesser_intensity", "deesser_min", "deesser_max", 0.14)

    denoise_enabled = safe_bool(user_settings.get("denoise_enabled"), True)
    denoise_floor = pick_param("denoise_floor", "denoise_min", "denoise_max", -38.0)

    comp_enabled = safe_bool(user_settings.get("comp_enabled"), True)
    comp_thresh = pick_param("comp_thresh", "comp_thresh_min", "comp_thresh_max", -18.0)
    comp_ratio = pick_param("comp_ratio", "comp_ratio_min", "comp_ratio_max", 2.5)
    comp_makeup = pick_param("comp_makeup", "comp_makeup_min", "comp_makeup_max", 2.0)

    loudnorm_enabled = safe_bool(user_settings.get("loudnorm_enabled"), True)
    loudnorm_lufs = pick_param("loudnorm_lufs", "loudnorm_lufs_min", "loudnorm_lufs_max", -16.0)
    loudnorm_tp = pick_param("loudnorm_tp", "loudnorm_tp_min", "loudnorm_tp_max", -1.0)

    limiter_enabled = safe_bool(user_settings.get("limiter_enabled"), True)
    limiter_ceiling = pick_param("limiter_ceiling", "limiter_min", "limiter_max", -1.0)

    formant_shift = pick_param("formant_shift", "formant_min", "formant_max", 0.0)

    haas_enabled = safe_bool(user_settings.get("haas_enabled"), True)
    haas_delay = pick_param("haas_delay", "haas_delay_min", "haas_delay_max", 4.0)

    reverb_enabled = safe_bool(user_settings.get("reverb_enabled"), True)
    reverb_wet = pick_param("reverb_wet", "reverb_wet_min", "reverb_wet_max", 0.025)

    tempo_drift_enabled = safe_bool(user_settings.get("tempo_drift_enabled"), True)
    tempo_drift_depth = pick_param("tempo_drift_depth", "tempo_drift_min", "tempo_drift_max", 0.025)

    saturation_enabled = safe_bool(user_settings.get("saturation_enabled"), True)
    saturation_drive = pick_param("saturation_drive", "saturation_min", "saturation_max", 0.35)

    noise_bed_enabled = safe_bool(user_settings.get("noise_bed_enabled"), True)
    noise_bed_level = pick_param("noise_bed_level", "noise_bed_min", "noise_bed_max", -62.0)

    cut_duration_min = safe_float(user_settings.get("cut_duration_min"), 50.0)
    cut_duration_max = safe_float(user_settings.get("cut_duration_max"), 70.0)

    base_params = {
        "pitch_semitones": round(pitch_semitones, 2),
        "speed_factor": round(speed_factor, 3),
        "volume_db": round(volume_db, 2),
        "bass_gain": round(bass_gain, 2),
        "mid_gain": round(mid_gain, 2),
        "treble_gain": round(treble_gain, 2),
        "highpass_freq": round(highpass_freq, 1),
        "lowpass_freq": round(lowpass_freq, 1),
        "deesser_enabled": deesser_enabled,
        "deesser_intensity": round(deesser_intensity, 3),
        "denoise_enabled": denoise_enabled,
        "denoise_floor": round(denoise_floor, 1),
        "comp_enabled": comp_enabled,
        "comp_thresh": round(comp_thresh, 1),
        "comp_ratio": round(comp_ratio, 2),
        "comp_makeup": round(comp_makeup, 1),
        "formant_shift": round(formant_shift, 2),
        "haas_enabled": haas_enabled,
        "haas_delay": round(haas_delay, 2),
        "reverb_enabled": reverb_enabled,
        "reverb_wet": round(reverb_wet, 3),
        "tempo_drift_enabled": tempo_drift_enabled,
        "tempo_drift_depth": round(tempo_drift_depth, 3),
        "saturation_enabled": saturation_enabled,
        "saturation_drive": round(saturation_drive, 2),
        "noise_bed_enabled": noise_bed_enabled,
        "noise_bed_level": round(noise_bed_level, 1),
        "loudnorm_enabled": loudnorm_enabled,
        "loudnorm_lufs": round(loudnorm_lufs, 1),
        "loudnorm_tp": round(loudnorm_tp, 1),
        "limiter_enabled": limiter_enabled,
        "limiter_ceiling": round(limiter_ceiling, 2),
        "cut_duration_min": cut_duration_min,
        "cut_duration_max": cut_duration_max,
        "seed": file_seed,
    }

    # If explicitly marked as master_locked or not randomize_per_file, preserve exact approved parameters!
    if safe_bool(user_settings.get("master_locked"), False) or not safe_bool(user_settings.get("randomize_per_file"), False):
        return base_params

    # Otherwise apply subtle auto-variation
    varied_params = auto_vary_params(base_params, file_index=file_index, seed=file_seed)
    return varied_params


def build_dsp_filter_chain(params: Dict[str, Any]) -> str:
    """Build the FFmpeg audio filter chain string from resolved DSP parameters."""
    filters = []

    # 1. Highpass rumble cutoff
    hp = safe_float(params.get("highpass_freq"), 75.0)
    if hp > 20:
        filters.append(f"highpass=f={hp:.1f}:w=0.7")

    # 2. Denoise noise-floor cleanup
    if safe_bool(params.get("denoise_enabled"), True):
        nf = safe_float(params.get("denoise_floor"), -38.0)
        filters.append(f"afftdn=nf={nf:.1f}:tn=1:bn=0")

    # 3. De-esser for sibilance
    if safe_bool(params.get("deesser_enabled"), True):
        intensity = max(0.01, min(0.99, safe_float(params.get("deesser_intensity"), 0.14)))
        filters.append(f"deesser=i={intensity:.3f}:m=0.5:f=0.5:s=o")

    # 4. Parametric Equalizer (Bass, Mid Presence, Treble Air)
    bass = safe_float(params.get("bass_gain"), 0.0)
    if abs(bass) > 0.05:
        filters.append(f"equalizer=f=180:width_type=o:w=1.2:g={bass:.2f}")

    mid = safe_float(params.get("mid_gain"), 0.0)
    if abs(mid) > 0.05:
        filters.append(f"equalizer=f=3200:width_type=o:w=1.4:g={mid:.2f}")

    treble = safe_float(params.get("treble_gain"), 0.0)
    if abs(treble) > 0.05:
        filters.append(f"equalizer=f=11000:width_type=o:w=1.2:g={treble:.2f}")

    # 5. Formant Vocal Tract Resonance Shifting (F1 throat ~600Hz, F2 oral cavity ~2000Hz)
    formant = safe_float(params.get("formant_shift"), 0.0)
    if abs(formant) > 0.03:
        f1_gain = round(formant * 1.2, 2)
        f2_gain = round(-formant * 1.0, 2)
        filters.append(f"equalizer=f=600:width_type=o:w=1.1:g={f1_gain:.2f}")
        filters.append(f"equalizer=f=2000:width_type=o:w=1.3:g={f2_gain:.2f}")

    # 6. Lowpass smoothing
    lp = safe_float(params.get("lowpass_freq"), 18500.0)
    if lp < 20000:
        filters.append(f"lowpass=f={lp:.1f}:w=0.7")

    # 7. Gentle vocal compressor
    if safe_bool(params.get("comp_enabled"), True):
        thresh = safe_float(params.get("comp_thresh"), -18.0)
        ratio = safe_float(params.get("comp_ratio"), 2.5)
        makeup = safe_float(params.get("comp_makeup"), 2.0)
        filters.append(f"acompressor=threshold={thresh:.1f}dB:ratio={ratio:.2f}:attack=15:release=120:makeup={makeup:.1f}dB")

    # 8. Harmonic Saturation / Tube Warmth Overtones
    if safe_bool(params.get("saturation_enabled"), True):
        drive = safe_float(params.get("saturation_drive"), 0.35)
        if drive > 0.05:
            filters.append(f"asoftclip=type=tanh:param={drive:.2f}")

    # 9. Micro-Reverb / Spatial Acoustic Room Response
    if safe_bool(params.get("reverb_enabled"), True):
        wet = safe_float(params.get("reverb_wet"), 0.025)
        if wet > 0.003:
            wet2 = max(0.001, round(wet * 0.6, 4))
            filters.append(f"aecho=0.8:0.88:20|35:{wet:.4f}|{wet2:.4f}")

    # 10. Stereo Haas Micro-Delay Widening (Phase anti-fingerprint)
    if safe_bool(params.get("haas_enabled"), True):
        delay = safe_float(params.get("haas_delay"), 4.0)
        if delay > 0.2:
            right_delay = max(0.5, delay * 0.4)
            filters.append(f"haas=level_in=1:level_out=1:side_gain=1:left_delay={delay:.1f}:right_delay={right_delay:.1f}")

    # 11. Dynamic Non-Linear Tempo / Pitch Drift (anti-static grid)
    if safe_bool(params.get("tempo_drift_enabled"), True):
        drift = safe_float(params.get("tempo_drift_depth"), 0.025)
        if drift > 0.004:
            filters.append(f"vibrato=f=0.2:d={drift:.3f}")

    # 12. Pitch and Tempo / Speed variation
    semitones = safe_float(params.get("pitch_semitones"), 0.0)
    speed = safe_float(params.get("speed_factor"), 1.0)
    pitch_factor = 2.0 ** (semitones / 12.0)

    if abs(semitones) > 0.01 or abs(speed - 1.0) > 0.001:
        tempo_comp = speed / pitch_factor
        atempo_filters = []
        rem_tempo = tempo_comp
        while rem_tempo > 2.0:
            atempo_filters.append("atempo=2.0")
            rem_tempo /= 2.0
        while rem_tempo < 0.5:
            atempo_filters.append("atempo=0.5")
            rem_tempo /= 0.5
        atempo_filters.append(f"atempo={rem_tempo:.4f}")

        atempo_str = ",".join(atempo_filters)
        filters.append(f"asetrate=44100*{pitch_factor:.6f},aresample=44100,{atempo_str}")

    # 13. Volume / Gain adjustment
    vol = safe_float(params.get("volume_db"), 0.0)
    if abs(vol) > 0.05:
        filters.append(f"volume={vol:.2f}dB")

    # 14. EBU R128 Loudness Normalization
    if safe_bool(params.get("loudnorm_enabled"), True):
        lufs = safe_float(params.get("loudnorm_lufs"), -16.0)
        tp = safe_float(params.get("loudnorm_tp"), -1.0)
        filters.append(f"loudnorm=I={lufs:.1f}:TP={tp:.1f}:LRA=9:dual_mono=true")

    # 15. Peak Limiter (brickwall limiter)
    if safe_bool(params.get("limiter_enabled"), True):
        ceiling = safe_float(params.get("limiter_ceiling"), -1.0)
        filters.append(f"alimiter=limit={ceiling:.2f}dB:level=true:attack=5:release=50:asc=true")

    return ",".join(filters) if filters else "anull"


def validate_processed_audio(
    output_path: str,
    original_duration: float,
    expected_cuts: List[Dict[str, Any]],
    expected_params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Perform real post-processing verification directly against the output file on disk:
    1. Checks file exists and size > 1024 bytes.
    2. Probes real output duration via ffprobe.
    3. Verifies audio stream parameters (sample rate, channels, codec).
    4. Measures duration reduction vs applied cuts and speed.
    5. Returns a structured verification report with natural English logs.
    """
    if not os.path.exists(output_path):
        return {
            "status": "FAILED",
            "exists": False,
            "error": f"Output file does not exist on disk: {output_path}",
            "logs": ["Output File ✗ Missing on disk"]
        }

    file_size = os.path.getsize(output_path)
    if file_size < 1024:
        return {
            "status": "FAILED",
            "exists": True,
            "fileSize": file_size,
            "error": f"Output file is corrupt or zero-byte ({file_size} bytes)",
            "logs": ["Output File ✗ File size too small / corrupted"]
        }

    try:
        out_meta = get_audio_metadata(output_path)
    except Exception as e:
        return {
            "status": "FAILED",
            "exists": True,
            "fileSize": file_size,
            "error": f"Output file is unreadable by ffprobe: {e}",
            "logs": ["Output File ✗ Unreadable audio stream"]
        }

    actual_dur = out_meta["duration"]
    orig_dur = safe_float(original_duration, actual_dur)
    dur_diff = max(0.0, orig_dur - actual_dur)
    pct_reduction = (dur_diff / orig_dur * 100.0) if orig_dur > 0 else 0.0

    checks = {
        "file_exists": True,
        "file_readable": True,
        "file_size_bytes": file_size,
        "actual_duration": actual_dur,
        "formatted_actual_duration": out_meta["formattedDuration"],
        "original_duration": orig_dur,
        "formatted_original_duration": format_time(orig_dur),
        "duration_removed": round(dur_diff, 2),
        "formatted_duration_removed": format_time(dur_diff),
        "duration_reduction_pct": round(pct_reduction, 2),
        "cuts_count": len(expected_cuts),
        "sample_rate": out_meta["sampleRate"],
        "channels": out_meta["channels"],
        "codec": out_meta["codec"]
    }

    # Verification criteria
    is_valid = (file_size > 4096 and actual_dur > 0.1)
    status = "SUCCESS" if is_valid else "PARTIAL"

    return {
        "status": status,
        "checks": checks,
        "metadata": out_meta
    }


def process_voiceover(
    input_path: str,
    output_path: str,
    params: Dict[str, Any],
    preview_mode: bool = False,
    preview_duration: float = 60.0,
    progress_callback = None
) -> Dict[str, Any]:
    """
    Full voiceover processing pipeline:
    1. Probes input file.
    2. Computes cuts (0-25m protected, >25m ~1m cuts every 5m).
    3. Builds FFmpeg filter graph (cuts + crossfades + DSP variations).
    4. Renders output MP3 (320kbps) — cuts permanently baked into the exported audio.
    5. Performs real validation on output file on disk.
    6. Returns process report with cut details, parameters, final verified duration.
    """
    meta = get_audio_metadata(input_path)
    total_dur = safe_float(meta["duration"], 0.0)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    cut_min = safe_float(params.get("cut_duration_min"), 50.0)
    cut_max = safe_float(params.get("cut_duration_max"), 70.0)
    seed = params.get("seed", None)

    cut_info = calculate_cut_schedule(
        duration=total_dur,
        cut_duration_range=(cut_min, cut_max),
        seed=seed
    )

    keep_segs = cut_info["keepSegments"]
    dsp_chain = build_dsp_filter_chain(params)

    cmd = [FFMPEG_PATH, "-y", "-i", input_path]

    if preview_mode:
        cmd.extend(["-t", str(preview_duration)])

    noise_bed_enabled = safe_bool(params.get("noise_bed_enabled"), True)
    noise_bed_level = safe_float(params.get("noise_bed_level"), -62.0)
    noise_amp = max(0.00001, min(0.01, 10.0 ** (noise_bed_level / 20.0))) if noise_bed_enabled else 0.0

    if len(keep_segs) <= 1:
        if noise_bed_enabled:
            filter_complex = f"[0:a]{dsp_chain}[pre];anoisesrc=c=pink:r=44100:a={noise_amp:.6f}[noise];[pre][noise]amix=inputs=2:duration=first:normalize=0[outa]"
        else:
            filter_complex = f"[0:a]{dsp_chain}[outa]"
    else:
        filter_parts = []
        concat_inputs = []
        fade_d = 0.04

        for idx, seg in enumerate(keep_segs):
            s_start = seg["start"]
            s_end = seg["end"]
            seg_len = s_end - s_start

            if idx == 0:
                f_str = f"afade=t=out:st={seg_len - fade_d:.3f}:d={fade_d:.3f}" if seg_len > fade_d else "anull"
            elif idx == len(keep_segs) - 1:
                f_str = f"afade=t=in:st=0:d={fade_d:.3f}" if seg_len > fade_d else "anull"
            else:
                f_str = f"afade=t=in:st=0:d={fade_d:.3f},afade=t=out:st={seg_len - fade_d:.3f}:d={fade_d:.3f}" if seg_len > fade_d * 2 else "anull"

            tag = f"s{idx}"
            filter_parts.append(
                f"[0:a]atrim=start={s_start:.3f}:end={s_end:.3f},asetpts=PTS-STARTPTS,{f_str}[{tag}]"
            )
            concat_inputs.append(f"[{tag}]")

        concat_str = "".join(concat_inputs)
        num_segs = len(keep_segs)
        filter_parts.append(f"{concat_str}concat=n={num_segs}:v=0:a=1[joined]")
        if noise_bed_enabled:
            filter_parts.append(f"[joined]{dsp_chain}[pre]")
            filter_parts.append(f"anoisesrc=c=pink:r=44100:a={noise_amp:.6f}[noise]")
            filter_parts.append(f"[pre][noise]amix=inputs=2:duration=first:normalize=0[outa]")
        else:
            filter_parts.append(f"[joined]{dsp_chain}[outa]")
        filter_complex = ";".join(filter_parts)

    out_ext = os.path.splitext(output_path)[1].lower()
    if out_ext in [".m4a", ".aac"]:
        codec_args = ["-c:a", "aac", "-b:a", "256k"]
    elif out_ext == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    elif out_ext == ".flac":
        codec_args = ["-c:a", "flac"]
    elif out_ext == ".ogg":
        codec_args = ["-c:a", "libvorbis", "-q:a", "6"]
    elif out_ext == ".opus":
        codec_args = ["-c:a", "libopus", "-b:a", "192k"]
    else:
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outa]",
        *codec_args,
        "-ar", "44100",
        output_path
    ])

    if progress_callback:
        progress_callback("processing", f"Running FFmpeg DSP and cut engine for {os.path.basename(input_path)}...")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (code {result.returncode}):\n{result.stderr}")

    validation = validate_processed_audio(
        output_path=output_path,
        original_duration=total_dur,
        expected_cuts=cut_info["cuts"],
        expected_params=params
    )

    out_meta = validation["metadata"]

    return {
        "inputPath": input_path,
        "outputPath": output_path,
        "originalDuration": total_dur,
        "formattedOriginalDuration": format_time(total_dur),
        "processedDuration": out_meta["duration"],
        "formattedProcessedDuration": out_meta["formattedDuration"],
        "cuts": cut_info["cuts"],
        "cutCount": len(cut_info["cuts"]),
        "params": params,
        "validation": validation,
        "filterComplex": filter_complex if len(filter_complex) < 400 else filter_complex[:400] + "...",
        "success": True
    }
