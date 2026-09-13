#!/usr/bin/env python3
"""
Gemini AI Audio Intelligence Engine
Features:
- Bulk API Key Pool Management
- Automatic Key Failover & Rotation (on 429 Quota Exhaustion / 403 / Errors)
- Google AI Studio Model Selector (gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash, etc.)
- Audio Feature & Spectrogram Analysis for Broadcast Polish & Anti-Fingerprint Uniqueness
- One-click intelligent parameter generation matching user cutting & DSP rules
"""

import os
import json
import time
import subprocess
import urllib.request
import urllib.error
from typing import Dict, List, Any, Optional, Tuple

class GeminiKeyManager:
    """Manages a pool of Gemini API keys with state tracking and auto-failover."""
    def __init__(self, keys: Optional[List[str]] = None):
        self.keys: List[str] = []
        self.key_statuses: Dict[str, Dict[str, Any]] = {}
        self.active_index: int = 0
        if keys:
            self.set_keys(keys)

    def set_keys(self, raw_keys: List[str]):
        clean_keys = []
        for k in raw_keys:
            # support comma, semicolon, newline separated
            for sub in str(k).replace(";", "\n").replace(",", "\n").splitlines():
                sk = sub.strip()
                if sk and not sk.startswith("#") and sk not in clean_keys:
                    clean_keys.append(sk)

        self.keys = clean_keys
        self.active_index = 0
        self.key_statuses = {
            k: {
                "keyMask": f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "***",
                "status": "ready",
                "usageCount": 0,
                "lastUsed": None,
                "error": None
            }
            for k in self.keys
        }

    def get_active_key(self) -> Optional[str]:
        if not self.keys:
            return None
        # Find next ready key starting from active_index
        for offset in range(len(self.keys)):
            idx = (self.active_index + offset) % len(self.keys)
            k = self.keys[idx]
            if self.key_statuses[k]["status"] != "exhausted":
                self.active_index = idx
                return k
        # If all exhausted, fallback to first key
        return self.keys[0] if self.keys else None

    def mark_key_success(self, key: str):
        if key in self.key_statuses:
            self.key_statuses[key]["status"] = "active"
            self.key_statuses[key]["usageCount"] += 1
            self.key_statuses[key]["lastUsed"] = time.strftime("%H:%M:%S")
            self.key_statuses[key]["error"] = None

    def mark_key_exhausted(self, key: str, error_msg: str) -> Tuple[int, Optional[str]]:
        """Marks current key as exhausted and advances to the next available key."""
        old_idx = self.active_index
        if key in self.key_statuses:
            self.key_statuses[key]["status"] = "exhausted"
            self.key_statuses[key]["error"] = error_msg

        # Advance index to next ready key
        next_key = None
        for offset in range(1, len(self.keys) + 1):
            idx = (old_idx + offset) % len(self.keys)
            cand = self.keys[idx]
            if self.key_statuses[cand]["status"] != "exhausted":
                self.active_index = idx
                next_key = cand
                break

        return old_idx, next_key

    def get_pool_status(self) -> Dict[str, Any]:
        return {
            "totalKeys": len(self.keys),
            "activeKeyIndex": self.active_index,
            "hasAvailable": any(s["status"] != "exhausted" for s in self.key_statuses.values()) if self.keys else False,
            "keys": [
                {
                    "index": i + 1,
                    "keyMask": self.key_statuses[k]["keyMask"],
                    "status": self.key_statuses[k]["status"],
                    "usageCount": self.key_statuses[k]["usageCount"],
                    "lastUsed": self.key_statuses[k]["lastUsed"],
                    "error": self.key_statuses[k]["error"],
                    "isActive": (i == self.active_index)
                }
                for i, k in enumerate(self.keys)
            ]
        }

# Global Key Manager instance
KEY_MANAGER = GeminiKeyManager()


def extract_audio_features(audio_path: str) -> Dict[str, Any]:
    """
    Extract comprehensive acoustic metrics (RMS, peak, dynamic range, silence ratio)
    using FFmpeg astats and silencedetect for deep Gemini analysis.
    """
    try:
        from audio_engine import get_audio_metadata, FFMPEG_PATH
    except ImportError:
        from .audio_engine import get_audio_metadata, FFMPEG_PATH

    meta = get_audio_metadata(audio_path)

    dur = meta.get("duration", 0.0)

    # 1. Measure audio stats (astats filter)
    # Take a 90s representative sample if audio is very long
    astats_cmd = [
        FFMPEG_PATH, "-v", "info", "-i", audio_path,
        "-t", "90",
        "-af", "astats=metadata=1:reset=1",
        "-f", "null", "-"
    ]
    res = subprocess.run(astats_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out_lines = res.stderr.split("\n")

    rms_level = -24.0
    peak_level = -3.0
    crest_factor = 12.0
    dc_offset = 0.0

    for l in out_lines:
        if "RMS level dB:" in l:
            try:
                rms_level = float(l.split("RMS level dB:")[1].strip().split()[0])
            except Exception:
                pass
        elif "Peak level dB:" in l:
            try:
                peak_level = float(l.split("Peak level dB:")[1].strip().split()[0])
            except Exception:
                pass
        elif "Crest factor:" in l:
            try:
                crest_factor = float(l.split("Crest factor:")[1].strip().split()[0])
            except Exception:
                pass

    # 2. Silence detection
    silence_cmd = [
        FFMPEG_PATH, "-v", "info", "-i", audio_path,
        "-t", "60",
        "-af", "silencedetect=noise=-32dB:d=0.5",
        "-f", "null", "-"
    ]
    s_res = subprocess.run(silence_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    silence_count = s_res.stderr.count("silence_start:")

    return {
        "fileName": meta.get("fileName", os.path.basename(audio_path)),
        "durationSeconds": round(dur, 2),
        "formattedDuration": meta.get("formattedDuration", "00:00"),
        "sampleRate": meta.get("sampleRate", 44100),
        "channels": meta.get("channels", 2),
        "bitrate": meta.get("bitrate", 128000),
        "codec": meta.get("codec", "unknown"),
        "measuredRmsDb": round(rms_level, 2),
        "measuredPeakDb": round(peak_level, 2),
        "crestFactor": round(crest_factor, 2),
        "silenceBurstsInFirst60s": silence_count,
        "isLongFormat": (dur > 1500.0) # >25 mins
    }


def analyze_audio_with_gemini(
    audio_path: str,
    model_name: str = "gemini-2.5-flash",
    custom_instructions: str = "",
    log_callback = None
) -> Dict[str, Any]:
    """
    Analyzes audio characteristics using Gemini AI with automatic key rotation on quota limits.
    Returns recommended DSP ranges specifically optimized for broadcast vocal fidelity,
    anti-copyright acoustic fingerprinting uniqueness, and natural human vocal tone.
    """
    if log_callback:
        log_callback(f"Extracting acoustic profile for {os.path.basename(audio_path)}...", "info")

    features = extract_audio_features(audio_path)

    system_prompt = """You are a World-Class Audio Mastering Engineer & Content-ID Acoustic Fingerprinting Specialist.
Your task is to analyze voiceover audio metadata and recommend precise, broadcast-grade DSP parameter ranges to produce output audio that is:
1. CONSISTENT & POLISHED: Studio podcast / broadcast level loudness (-16 to -14 LUFS), warm radio tone, crisp de-essing, controlled dynamics, and true peak ceiling.
2. UNIQUE FROM ORIGINAL: Transform pitch, micro-tempo, harmonic equalization, and dynamic envelope just enough to make the acoustic signature completely unique and immune to automated copyright/content fingerprint matchers, while keeping the voice sounding 100% natural, human, authentic, and pleasing (never robotic or cartoonish).
3. SMART CUT RULE COMPLIANT: The first 25 minutes are 100% uncut (0 cuts). After 25 minutes, specify optimal ~1-minute cut duration ranges (e.g. 50s–70s) for the 5-minute periodic splicing.

Return STRICT JSON matching this exact JSON schema:
{
  "summary_title": "Short descriptive title for this sound profile",
  "ai_analysis": "Deep 2-3 sentence technical rationale explaining how these parameters polish vocal presence, eliminate copyright fingerprint matching, and preserve human voice realism.",
  "uniqueness_score": 95,
  "parameters": {
    "pitch_min": -0.8,
    "pitch_max": 0.8,
    "speed_min": 0.98,
    "speed_max": 1.04,
    "volume_min": -0.5,
    "volume_max": 1.0,
    "highpass_min": 75.0,
    "highpass_max": 90.0,
    "bass_min": 1.0,
    "bass_max": 2.5,
    "mid_min": 0.5,
    "mid_max": 2.0,
    "treble_min": 1.0,
    "treble_max": 2.5,
    "lowpass_min": 17500.0,
    "lowpass_max": 18500.0,
    "deesser_enabled": true,
    "deesser_min": 0.10,
    "deesser_max": 0.18,
    "denoise_enabled": true,
    "denoise_min": -38.0,
    "denoise_max": -32.0,
    "comp_enabled": true,
    "comp_thresh_min": -20.0,
    "comp_thresh_max": -16.0,
    "comp_ratio_min": 2.2,
    "comp_ratio_max": 2.8,
    "comp_makeup_min": 1.5,
    "comp_makeup_max": 2.8,
    "loudnorm_enabled": true,
    "loudnorm_lufs_min": -16.0,
    "loudnorm_lufs_max": -15.0,
    "loudnorm_tp_min": -1.0,
    "loudnorm_tp_max": -1.0,
    "limiter_enabled": true,
    "limiter_min": -1.0,
    "limiter_max": -0.8,
    "cut_duration_min": 50.0,
    "cut_duration_max": 65.0,
    "randomize_per_file": true
  }
}
"""

    user_content = f"""Input Audio Acoustic Profile:
- File Name: {features['fileName']}
- Duration: {features['formattedDuration']} ({features['durationSeconds']} seconds)
- Audio Codec: {features['codec']} ({features['sampleRate']} Hz, {features['channels']} channels, {features['bitrate']} bps)
- Measured RMS Loudness: {features['measuredRmsDb']} dB
- Measured Peak Level: {features['measuredPeakDb']} dB
- Crest Factor / Dynamic Range: {features['crestFactor']}
- Speech Energy / Silence Density: {features['silenceBurstsInFirst60s']} pause bursts/min
- Is Post-25min Long Format: {features['isLongFormat']}
{f'- User Custom Goal: {custom_instructions}' if custom_instructions else ''}

Generate the optimal studio mastering & anti-copyright parameter suite in strict JSON format."""

    request_body = {
        "contents": [
            {
                "parts": [
                    {"text": user_content}
                ]
            }
        ],
        "systemInstruction": {
            "parts": [
                {"text": system_prompt}
            ]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4
        }
    }

    # Attempt calls with key rotation on quota / failure
    max_attempts = len(KEY_MANAGER.keys) if KEY_MANAGER.keys else 1
    attempts = 0
    last_error = None
    switched_keys = []

    while attempts < max_attempts:
        active_key = KEY_MANAGER.get_active_key()
        if not active_key:
            raise ValueError("No Gemini API keys configured. Please add at least one Gemini API key.")

        active_mask = KEY_MANAGER.key_statuses[active_key]["keyMask"]
        if log_callback:
            log_callback(f"Sending audio profile to Gemini ({model_name}) using Key #{KEY_MANAGER.active_index + 1} ({active_mask})...", "info")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={active_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                if resp.status == 200:
                    raw_data = resp.read().decode("utf-8")
                    parsed_json = json.loads(raw_data)

                    # Extract generated text from candidates
                    candidate_text = parsed_json["candidates"][0]["content"]["parts"][0]["text"]
                    clean_res = json.loads(candidate_text)

                    KEY_MANAGER.mark_key_success(active_key)
                    if log_callback:
                        log_callback(f"✨ Gemini AI Intelligence analysis complete! (Uniqueness: {clean_res.get('uniqueness_score', 95)}/100)", "success")

                    return {
                        "success": True,
                        "model": model_name,
                        "usedKeyIndex": KEY_MANAGER.active_index + 1,
                        "switchedKeys": switched_keys,
                        "features": features,
                        "analysis": clean_res
                    }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            attempts += 1
            last_error = f"HTTP {e.code}: {e.reason} ({err_body[:120]})"
            
            # Quota exhausted (429) or Forbidden (403) -> Failover to next key!
            old_idx, next_key = KEY_MANAGER.mark_key_exhausted(active_key, last_error)
            switched_keys.append({"fromIndex": old_idx + 1, "error": last_error})

            if next_key and attempts < len(KEY_MANAGER.keys):
                next_mask = KEY_MANAGER.key_statuses[next_key]["keyMask"]
                msg = f"⚠️ Key #{old_idx + 1} exhausted/failed ({e.code}). Automatically switching to Key #{KEY_MANAGER.active_index + 1} ({next_mask})..."
                if log_callback:
                    log_callback(msg, "warning")
            else:
                if log_callback:
                    log_callback(f"❌ All Gemini API keys exhausted or failed: {last_error}", "error")
                break
        except Exception as e:
            attempts += 1
            last_error = str(e)
            old_idx, next_key = KEY_MANAGER.mark_key_exhausted(active_key, last_error)
            switched_keys.append({"fromIndex": old_idx + 1, "error": last_error})
            if next_key and attempts < len(KEY_MANAGER.keys):
                if log_callback:
                    log_callback(f"⚠️ Key error ({e}). Switching to next key...", "warning")
            else:
                break

    raise RuntimeError(f"Gemini API request failed on all keys: {last_error}")
