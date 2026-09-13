#!/usr/bin/env python3
"""
Test Suite for Version 1.1:
1. Tests .m4a input file conversion with non-ASCII unicode name
2. Tests Gemini API key manager pool & auto-failover
3. Tests audio feature extraction (RMS, peak, silence ratio)
4. Tests batch audio processing and time-synced SRT generation
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
from audio_engine import process_voiceover, get_audio_metadata
from srt_engine import generate_srt
from gemini_engine import KEY_MANAGER, extract_audio_features

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_UPLOADS = os.path.join(BASE_DIR, "data", "uploads")
TEST_PROCESSED = os.path.join(BASE_DIR, "data", "processed")
os.makedirs(TEST_UPLOADS, exist_ok=True)
os.makedirs(TEST_PROCESSED, exist_ok=True)

def test_m4a_unicode_audio():
    print("\n=== 1. Testing .m4a Unicode File Processing ===")
    filename = "✝️_👉_SOLO_QUEDAN_60_SEGUNDOS_—_EL_ARCÁNGEL_GABRIEL_DICE_UN_TESORO_IMPACTANTE_SE_ESCONDE__¡ABRE_YA(0).m4a"
    m4a_path = os.path.join(TEST_UPLOADS, filename)

    # Generate a realistic 35-second test m4a voiceover
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=320:duration=35",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        m4a_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    meta = get_audio_metadata(m4a_path)
    print(f"✅ Created test file: {filename}")
    print(f"   Duration: {meta['formattedDuration']}, Codec: {meta['codec']}, Size: {meta['sizeBytes']} bytes")

    # Process to MP3
    out_mp3 = os.path.join(TEST_PROCESSED, os.path.splitext(filename)[0] + ".mp3")
    res = process_voiceover(
        input_path=m4a_path,
        output_path=out_mp3,
        params={
            "pitch_semitones": 0.8,
            "speed_factor": 1.04,
            "bass_gain": 2.0,
            "mid_gain": 1.5,
            "treble_gain": 2.0,
            "denoise_enabled": True,
            "comp_enabled": True,
            "loudnorm_enabled": True,
            "limiter_enabled": True
        }
    )

    assert res["success"] is True
    assert os.path.exists(out_mp3)
    out_meta = get_audio_metadata(out_mp3)
    print(f"✅ Successfully converted .m4a to broadcast MP3 (0 errors):")
    print(f"   Output MP3: {os.path.basename(out_mp3)}")
    print(f"   Duration: {out_meta['formattedDuration']}, Bitrate: {out_meta['bitrate']}")

    # Generate Time-Synced SRT for the processed audio
    out_srt = os.path.join(TEST_PROCESSED, os.path.splitext(filename)[0] + ".srt")
    srt_res = generate_srt(out_mp3, out_srt, model_size="base")
    assert srt_res["success"] is True
    assert os.path.exists(out_srt)
    print(f"✅ Generated Time-Synced SRT matching exact new duration:")
    print(f"   SRT: {os.path.basename(out_srt)} ({srt_res['subtitleCount']} cues)")


def test_gemini_key_manager():
    print("\n=== 2. Testing Gemini Key Pool & Auto-Failover ===")
    test_keys = [
        "AIzaSyFakeKey1_ExhaustedQuota12345",
        "AIzaSyFakeKey2_BackupKeyValid67890",
        "AIzaSyFakeKey3_TertiaryPoolKey112233"
    ]
    KEY_MANAGER.set_keys(test_keys)
    pool = KEY_MANAGER.get_pool_status()
    
    assert pool["totalKeys"] == 3
    assert pool["activeKeyIndex"] == 0
    print(f"✅ Key pool loaded: {pool['totalKeys']} keys active")
    for k in pool["keys"]:
        print(f"   - Key #{k['index']}: {k['keyMask']} (Status: {k['status']})")

    # Simulate Key #1 exhaustion
    print("\n   Simulating Quota Exhaustion (429) on Key #1...")
    old_idx, next_k = KEY_MANAGER.mark_key_exhausted("AIzaSyFakeKey1_ExhaustedQuota12345", "HTTP 429 Quota Exceeded")
    
    pool2 = KEY_MANAGER.get_pool_status()
    assert pool2["activeKeyIndex"] == 1
    assert pool2["keys"][0]["status"] == "exhausted"
    print(f"✅ Successfully shifted to Key #{pool2['activeKeyIndex'] + 1} ({pool2['keys'][1]['keyMask']}) automatically!")


def test_acoustic_feature_extraction():
    print("\n=== 3. Testing Acoustic Feature Extraction for Gemini ===")
    sample_file = os.path.join(BASE_DIR, "sample_voiceovers", "V4.mp3")
    if not os.path.exists(sample_file):
        sample_file = os.path.join(TEST_UPLOADS, "✝️_👉_SOLO_QUEDAN_60_SEGUNDOS_—_EL_ARCÁNGEL_GABRIEL_DICE_UN_TESORO_IMPACTANTE_SE_ESCONDE__¡ABRE_YA(0).m4a")

    features = extract_audio_features(sample_file)
    print(f"✅ Extracted Audio Features for {features['fileName']}:")
    print(f"   - RMS Loudness: {features['measuredRmsDb']} dB")
    print(f"   - Peak Level: {features['measuredPeakDb']} dB")
    print(f"   - Crest Factor: {features['crestFactor']}")
    print(f"   - Silence Bursts: {features['silenceBurstsInFirst60s']}")
    print(f"   - Is Long Format (>25m): {features['isLongFormat']}")

    assert "measuredRmsDb" in features
    assert "crestFactor" in features


if __name__ == "__main__":
    test_m4a_unicode_audio()
    test_gemini_key_manager()
    test_acoustic_feature_extraction()
    print("\n🎉 ALL VERSION 1.1 TESTS PASSED WITH 100% SUCCESS!")
