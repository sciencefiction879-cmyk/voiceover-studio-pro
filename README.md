# Bulk Voiceover Processing Studio & SRT Generator

A studio-grade bulk voiceover audio processing application built for macOS. It automates audio variation ranges, post-25m smart cut extraction, loudness normalization, peak limiting, noise floor cleanup, sibilance de-essing, live waveform preview, and time-synced SRT subtitle generation.

---

## Key Features & Cutting Rules

### 1. 25-Minute Protected Audio Zone
- **First 25 Minutes (0:00 – 25:00)**: **Strictly ZERO cuts**. All voiceover content in the first 25 minutes is preserved in its entirety while receiving all chosen audio variations.
- **Post-25-Minute Intervals**: Generates random ~1-minute cuts (customizable range e.g. 50s–70s) inside every 5-minute block:
  - **25:00 – 30:00**: Random ~1-min cut with 40ms micro-crossfades
  - **30:00 – 35:00**: Random ~1-min cut
  - **35:00 – 40:00**: Random ~1-min cut
  - ... and continuing every 5 minutes until the file ends.
- **Files under 25 minutes**: Receive full DSP variation processing with 0 cuts.

### 2. Audio DSP Variation Suite (Customizable Ranges)
- **Pitch Shift**: Tempo-compensated pitch variation (e.g. -2.0 to +2.0 semitones).
- **Speed / Tempo Shift**: Pitch-preserving time stretch (e.g. 0.95x to 1.15x).
- **Volume / Gain**: Range in dB (e.g. -3 dB to +3 dB).
- **EQ & Frequency Sculpting**:
  - Highpass filter (rumble cutoff: 20 Hz – 150 Hz)
  - Bass warmth (180 Hz, ±6 dB)
  - Presence & Intelligibility (3.2 kHz, ±6 dB)
  - Treble air (11 kHz, ±6 dB)
  - Lowpass smoothing
- **Noise-Floor Cleanup**: Adaptive spectral denoising (`afftdn`) to remove ambient room hiss and preamp noise.
- **Sibilance De-Esser**: Targets 5.5 kHz – 8.5 kHz harsh sibilants ('s', 'sh', 'ch', 't').
- **Gentle Compression**: Broadcast vocal leveling compressor (`acompressor`).
- **Loudness Normalization**: EBU R128 standard (target -16.0 LUFS or -14.0 LUFS, true peak -1.0 dBTP).
- **Peak Limiting**: True peak brickwall limiter (`alimiter` -1.0 dBTP ceiling).
- **Consistency & Randomization**: Toggle between randomizing per-file within specified ranges or applying a fixed value across the batch.

### 3. Bulk Folder Processing & Natural Naming
- Scan or drag & drop a folder containing `V1.mp3, V2.mp3, V3.mp3, ..., V10.mp3, V11.mp3`.
- Natural sorting ensures `V2` comes before `V10`.
- Keeps original filenames and sequence throughout.

### 4. Interactive A/B Preview & Waveform Testing
- Test single files or rapid 60s preview clips before committing to bulk processing.
- Live waveform visualizer with A/B switch (Original vs Processed Audio).
- Interactive Cut Map timeline highlighting the protected 0–25m zone and exact cut points.

### 5. Time-Synced SRT Subtitle Generation
- **"Generate All SRT"** button transcribes each final processed audio file.
- Guarantees caption timestamps match the exact final audio duration and cuts.
- In-browser SRT editor to inspect and edit subtitles.

### 6. Manual Destination Selection & Export
- Does NOT export automatically.
- User specifies destination directory or browses via native macOS Finder dialog.
- Separate export options:
  - **Export Processed MP3s** (`V1.mp3, V2.mp3, ...`)
  - **Export Synced SRTs** (`V1.srt, V2.srt, ...`)
  - **Export All (MP3 + SRT)**
  - **Download ZIP** bundle option.

---

## Quick Start

Run the launcher script in terminal:
```bash
./start.sh
```

Or run manually:
```bash
source .venv/bin/activate
python backend/server.py
```
Open your browser at `http://127.0.0.1:5055`.
