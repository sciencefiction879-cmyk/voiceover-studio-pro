#!/usr/bin/env python3
"""
Generate realistic synthetic test voiceovers (V1.mp3, V2.mp3, V3.mp3, V4.mp3)
for testing the bulk processing studio, including post-25m cut intervals.
"""

import os
import subprocess
import shutil

FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample_voiceovers")
os.makedirs(SAMPLE_DIR, exist_ok=True)

# Durations in seconds:
# V1: 27 minutes (1620s) -> tests 0-25m (no cuts) + 25-27m (1 cut)
# V2: 32 minutes (1920s) -> tests 0-25m (no cuts) + 25-30m (1 cut) + 30-32m (1 cut)
# V3: 15 minutes (900s)  -> tests under 25m (0 cuts)
# V4: 45 seconds (45s)   -> rapid test clip
FILES_TO_GENERATE = [
    ("V1.mp3", 1620, 220), # 27 mins
    ("V2.mp3", 1920, 180), # 32 mins
    ("V3.mp3", 900, 260),  # 15 mins
    ("V4.mp3", 45, 200),   # 45 secs
]

def generate_voiceover(filename, duration, pitch_hz):
    target = os.path.join(SAMPLE_DIR, filename)
    if os.path.exists(target):
        print(f"File already exists: {target}")
        return

    print(f"Generating {filename} ({duration}s, ~{duration/60:.1f} mins)...")

    # Generate multi-harmonic voice-like audio with speech modulation
    cmd = [
        FFMPEG_PATH, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={pitch_hz}:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency={pitch_hz*2}:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency={pitch_hz*3}:duration={duration}",
        "-f", "lavfi",
        "-i", f"anoisesrc=d={duration}:c=pink:r=44100:a=0.015",
        "-filter_complex",
        f"[0:a]volume=0.5[a0];[1:a]volume=0.3[a1];[2:a]volume=0.15[a2];[3:a]volume=0.08[a3];"
        f"[a0][a1][a2][a3]amix=inputs=4:normalize=0[mixed];"
        f"[mixed]tremolo=f=0.8:d=0.7,chorus=0.7:0.9:55:0.4:0.25:2[vocal];"
        f"[vocal]acompressor=threshold=-20dB:ratio=3:attack=20:release=150:makeup=2dB[outa]",
        "-map", "[outa]",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        "-ar", "44100",
        target
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print(f"Created {target} ({os.path.getsize(target) / (1024*1024):.1f} MB)")

if __name__ == "__main__":
    print(f"Generating sample voiceovers in: {SAMPLE_DIR}")
    for fname, dur, pitch in FILES_TO_GENERATE:
        generate_voiceover(fname, dur, pitch)
    print("Done generating sample files.")
