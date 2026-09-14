import os
import re
import tempfile
import subprocess
from typing import List, Dict, Any, Optional, Tuple

try:
    from .aligner.engine import AlignerModel
    from .aligner.validator import read_script_file
    from .aligner.srt_builder import SubtitleCue as AlignerCue, format_timestamp
except (ImportError, ValueError):
    try:
        from aligner.engine import AlignerModel
        from aligner.validator import read_script_file
        from aligner.srt_builder import SubtitleCue as AlignerCue, format_timestamp
    except (ImportError, ValueError):
        from backend.aligner.engine import AlignerModel
        from backend.aligner.validator import read_script_file
        from backend.aligner.srt_builder import SubtitleCue as AlignerCue, format_timestamp

TIMESTAMP_REGEX = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')


def parse_srt_timestamp(ts_str: str) -> float:
    """Parse '00:01:23,456' or '00:01:23.456' into seconds as float."""
    ts_str = ts_str.strip().replace('.', ',')
    parts = ts_str.split(':')
    if len(parts) != 3:
        return 0.0
    h = float(parts[0])
    m = float(parts[1])
    s_parts = parts[2].split(',')
    s = float(s_parts[0])
    ms = float(s_parts[1]) if len(s_parts) > 1 else 0.0
    return h * 3600.0 + m * 60.0 + s + (ms / 1000.0)


def format_srt_timestamp(seconds: float) -> str:
    """Format float seconds into standard SRT timestamp format: HH:MM:SS,mmm."""
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class SRTCue:
    def __init__(self, index: int, start: float, end: float, text: str):
        self.index = index
        self.start = round(start, 3)
        self.end = round(max(start + 0.1, end), 3)
        self.text = text.strip()

    def to_srt_block(self, new_index: Optional[int] = None) -> str:
        idx = new_index if new_index is not None else self.index
        start_str = format_srt_timestamp(self.start)
        end_str = format_srt_timestamp(self.end)
        return f"{idx}\n{start_str} --> {end_str}\n{self.text}\n"


def parse_srt_file(file_path: str) -> List[SRTCue]:
    """Parse an .srt file into a list of SRTCue objects."""
    if not os.path.exists(file_path):
        return []

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return parse_srt_content(content)


def parse_srt_content(content: str) -> List[SRTCue]:
    """Parse raw SRT content string into SRTCue objects."""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r'\n\s*\n', content.strip())
    cues: List[SRTCue] = []

    for block in blocks:
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue

        timing_idx = -1
        for i, line in enumerate(lines):
            if "-->" in line:
                timing_idx = i
                break

        if timing_idx == -1:
            continue

        timing_line = lines[timing_idx]
        parts = timing_line.split("-->")
        if len(parts) != 2:
            continue

        start_ts = parse_srt_timestamp(parts[0])
        end_ts = parse_srt_timestamp(parts[1].split()[0])

        text_lines = lines[timing_idx + 1:]
        text = "\n".join(text_lines).strip()
        if not text:
            continue

        idx = len(cues) + 1
        if timing_idx > 0 and lines[timing_idx - 1].isdigit():
            try:
                idx = int(lines[timing_idx - 1])
            except ValueError:
                pass

        cues.append(SRTCue(index=idx, start=start_ts, end=end_ts, text=text))

    return cues


def write_srt_file(cues: List[SRTCue], output_path: str) -> None:
    """Write list of SRTCue objects to an .srt file."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, cue in enumerate(cues, 1):
            f.write(cue.to_srt_block(new_index=idx))
            f.write("\n")


def convert_audio_to_wav_16k(audio_path: str, ffmpeg_bin: str = "ffmpeg") -> str:
    """Convert any audio file to 16kHz mono WAV for forced alignment."""
    temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp_wav_path = temp_wav.name
    temp_wav.close()

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", audio_path,
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        temp_wav_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to prepare audio for alignment: {res.stderr}")
    return temp_wav_path


def generate_forced_alignment_cues(audio_path: str, script_path: str, ffmpeg_bin: str = "ffmpeg") -> List[SRTCue]:
    """
    Perform high-speed acoustic forced alignment from original audio + provided script (.txt, .docx, .srt).
    Returns list of aligned SRTCue objects.
    """
    script_text = read_script_file(script_path)
    if not script_text.strip():
        raise ValueError(f"Script file '{os.path.basename(script_path)}' is empty.")

    # Convert audio to standard 16k mono wav
    wav_path = convert_audio_to_wav_16k(audio_path, ffmpeg_bin=ffmpeg_bin)
    try:
        model = AlignerModel.get_instance()
        word_segments = model.align(wav_path, script_text)

        if not word_segments:
            raise RuntimeError("Forced alignment returned no timed words.")

        try:
            from .aligner.srt_builder import build_sentence_cues_from_words
        except (ImportError, ValueError):
            try:
                from aligner.srt_builder import build_sentence_cues_from_words
            except (ImportError, ValueError):
                from backend.aligner.srt_builder import build_sentence_cues_from_words
        cues_data = build_sentence_cues_from_words(word_segments, max_chars_per_line=48, max_lines=2)

        srt_cues: List[SRTCue] = []
        for idx, c in enumerate(cues_data, 1):
            srt_cues.append(SRTCue(
                index=idx,
                start=c["start"],
                end=c["end"],
                text=c["text"]
            ))
        return srt_cues
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def recalculate_srt_timestamps(
    cues: List[SRTCue],
    speed_factor: float = 1.0,
    cuts: Optional[List[Dict[str, Any]]] = None
) -> List[SRTCue]:
    """
    Apply identical cuts and speed stretch to subtitle cues:
    1. If cue is entirely inside a cut interval: dropped.
    2. If cue overlaps a cut boundary: trimmed so no caption points to deleted audio.
    3. Shift all cues backward by cumulative cut durations of cuts preceding them.
    4. Scale by speed factor: t_scaled = t_shifted / speed_factor.
    """
    speed = float(speed_factor) if speed_factor and speed_factor > 0 else 1.0
    sorted_cuts = sorted(cuts or [], key=lambda c: float(c.get("start", 0.0)))

    adjusted_cues: List[SRTCue] = []

    for cue in cues:
        curr_start = cue.start
        curr_end = cue.end

        is_dropped = False
        cumulative_cut_shift_start = 0.0
        cumulative_cut_shift_end = 0.0

        for c in sorted_cuts:
            c_start = float(c["start"])
            c_end = float(c["end"])
            c_dur = float(c.get("duration", c_end - c_start))

            # Case A: Cue is entirely inside this cut interval
            if curr_start >= (c_start - 0.05) and curr_end <= (c_end + 0.05):
                is_dropped = True
                break

            # Case B: Cue starts before cut and ends inside cut -> trim end to cut start
            if curr_start < c_start and curr_end > c_start and curr_end <= c_end:
                curr_end = c_start

            # Case C: Cue starts inside cut and ends after cut -> trim start to cut end
            elif curr_start >= c_start and curr_start < c_end and curr_end > c_end:
                curr_start = c_end

            # Accumulate shifts for cues starting or ending after this cut
            if curr_start >= c_end:
                cumulative_cut_shift_start += c_dur
            if curr_end >= c_end:
                cumulative_cut_shift_end += c_dur

        if is_dropped:
            continue

        # If after boundary trimming the cue became trivial (< 0.1s), omit it
        if curr_end - curr_start < 0.1:
            continue

        # Shift timestamps by cut durations
        final_orig_start = max(0.0, curr_start - cumulative_cut_shift_start)
        final_orig_end = max(final_orig_start + 0.1, curr_end - cumulative_cut_shift_end)

        # Scale by tempo/speed factor
        new_start = round(final_orig_start / speed, 3)
        new_end = round(final_orig_end / speed, 3)

        if new_end > new_start + 0.05:
            adjusted_cues.append(SRTCue(
                index=len(adjusted_cues) + 1,
                start=new_start,
                end=new_end,
                text=cue.text
            ))

    return adjusted_cues


def validate_caption_sync(
    srt_path: str,
    target_audio_duration: float,
    cuts: Optional[List[Dict[str, Any]]] = None,
    speed_factor: float = 1.0,
    mode: str = "mode1"
) -> Dict[str, Any]:
    """
    Validate that generated SRT conforms strictly to the selected caption mode:
    Mode 1: Cut-synchronized to processed audio (no cues in cut intervals, matches shortened duration).
    Mode 2: Aligned to untouched original audio (no cuts applied, matches original duration).
    """
    if not os.path.exists(srt_path):
        return {
            "status": "FAILED",
            "error": "Caption SRT file does not exist on disk",
            "checks": {"file_exists": False}
        }

    cues = parse_srt_file(srt_path)
    if not cues:
        return {
            "status": "FAILED",
            "error": "Caption file contains zero valid cues",
            "checks": {"cue_count": 0}
        }

    # Verify chronological monotonic timestamps
    for idx, cue in enumerate(cues):
        if cue.start >= cue.end:
            return {
                "status": "FAILED",
                "error": f"Invalid timestamp order at cue #{idx+1} ({cue.start} >= {cue.end})",
                "checks": {"monotonic_timestamps": False}
            }
        if idx > 0 and cue.start < cues[idx - 1].start:
            return {
                "status": "FAILED",
                "error": f"Timestamps out of chronological order at cue #{idx+1}",
                "checks": {"chronological_order": False}
            }

    last_cue = cues[-1]
    last_end = last_cue.end

    dur_tolerance = 1.5
    duration_match = target_audio_duration <= 0 or (last_end <= (target_audio_duration + dur_tolerance))

    checks = {
        "file_exists": True,
        "cue_count": len(cues),
        "first_cue_start": cues[0].start,
        "last_cue_end": last_end,
        "formatted_last_cue": format_srt_timestamp(last_end),
        "target_audio_duration": target_audio_duration,
        "duration_difference": round(abs(target_audio_duration - last_end), 2) if target_audio_duration > 0 else 0.0,
        "duration_match": duration_match,
        "mode": mode,
        "cuts_count": len(cuts or []) if mode == "mode1" else 0
    }

    if mode == "mode1":
        report = (
            f"✓ Mode 1 Verified: {len(cues)} cues cut-synchronized. "
            f"Final timestamp {format_srt_timestamp(last_end)} matches processed audio ({format_srt_timestamp(target_audio_duration)})."
        ) if duration_match else (
            f"Notice: Final caption ends at {format_srt_timestamp(last_end)} (processed audio is {format_srt_timestamp(target_audio_duration)})."
        )
    else:
        report = (
            f"✓ Mode 2 (Alignment Only) Verified: {len(cues)} cues aligned to original voice-over. "
            f"Final timestamp {format_srt_timestamp(last_end)} matches original audio ({format_srt_timestamp(target_audio_duration)})."
        ) if duration_match else (
            f"Notice: Final caption ends at {format_srt_timestamp(last_end)} (original audio is {format_srt_timestamp(target_audio_duration)})."
        )

    status = "SUCCESS" if duration_match else "WARNING"
    return {
        "status": status,
        "checks": checks,
        "report": report
    }


def process_caption_pipeline(
    audio_path: str,
    script_path: str,
    output_srt_path: str,
    speed_factor: float = 1.0,
    cuts: Optional[List[Dict[str, Any]]] = None,
    target_audio_duration: float = 0.0,
    mode: str = "mode1",
    ffmpeg_bin: str = "ffmpeg"
) -> Dict[str, Any]:
    """
    End-to-end caption processing for Mode 1 or Mode 2:

    MODE 1 (Process + Cut + SRT):
      Original Audio + Script -> Forced Alignment -> Audio Cuts Applied to Captions -> Shift Backward -> Final Processed SRT.

    MODE 2 (Alignment Only):
      Original Audio + Script -> Forced Alignment -> Untouched Original Timeline -> Final SRT Only (No cuts, no audio alteration).
    """
    ext = os.path.splitext(script_path)[1].lower()

    # Step 1: Acoustic forced alignment or parse pre-existing SRT
    if ext == ".srt":
        base_cues = parse_srt_file(script_path)
    else:
        base_cues = generate_forced_alignment_cues(audio_path, script_path, ffmpeg_bin=ffmpeg_bin)

    if not base_cues:
        raise ValueError("Could not extract subtitle cues from script.")

    if mode == "mode2":
        # Mode 2 — Alignment Only:
        # Absolutely NO cuts applied, NO speed factor, untouched original timeline
        final_cues = [
            SRTCue(idx + 1, c.start, c.end, c.text)
            for idx, c in enumerate(base_cues)
        ]
    else:
        # Mode 1 — Process + Cut + SRT:
        # Apply cuts (remove captions inside cuts, trim boundaries, shift backward) + speed stretch
        final_cues = recalculate_srt_timestamps(
            cues=base_cues,
            speed_factor=speed_factor,
            cuts=cuts
        )

    # Step 3: Write final synchronized SRT
    write_srt_file(final_cues, output_srt_path)

    # Step 4: Validate against audio duration for the corresponding mode
    validation = validate_caption_sync(
        srt_path=output_srt_path,
        target_audio_duration=target_audio_duration,
        cuts=cuts if mode == "mode1" else None,
        speed_factor=speed_factor if mode == "mode1" else 1.0,
        mode=mode
    )

    return {
        "success": True,
        "mode": mode,
        "inputScriptPath": script_path,
        "outputSrtPath": output_srt_path,
        "initialCueCount": len(base_cues),
        "finalCueCount": len(final_cues),
        "validation": validation,
        "cues": [c.to_srt_block() for c in final_cues]
    }


def process_srt_file(
    input_srt_path: str,
    output_srt_path: str,
    speed_factor: float = 1.0,
    cuts: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Compatibility wrapper for processing existing SRT files."""
    return process_caption_pipeline(
        audio_path="",
        script_path=input_srt_path,
        output_srt_path=output_srt_path,
        speed_factor=speed_factor,
        cuts=cuts,
        mode="mode1"
    )



def find_matching_script(audio_path: str, search_dir: Optional[str] = None) -> Optional[str]:
    """
    Find matching script (.txt, .docx, .srt, .text) for a given audio file.
    Supports deterministic matching across multiple naming formats:
    - 'V1 Script.txt' ↔ 'V1.mp3' (User Primary Format)
    - 'V1_script.txt' ↔ 'V1.mp3'
    - 'V1-script.txt' ↔ 'V1.mp3'
    - 'V1.txt' ↔ 'V1.mp3'
    - 'V1.docx' ↔ 'V1.mp3'
    - 'V1.srt' ↔ 'V1.mp3'
    """
    search_dirs = []
    if search_dir and os.path.exists(search_dir):
        search_dirs.append(search_dir)
    audio_dir = os.path.dirname(audio_path)
    if audio_dir and os.path.exists(audio_dir) and audio_dir not in search_dirs:
        search_dirs.append(audio_dir)

    # Also search uploads folder if available
    try:
        from .server import UPLOADS_DIR
        if UPLOADS_DIR and os.path.exists(UPLOADS_DIR) and UPLOADS_DIR not in search_dirs:
            search_dirs.append(UPLOADS_DIR)
    except Exception:
        pass

    if not search_dirs:
        return None

    audio_stem = os.path.splitext(os.path.basename(audio_path))[0].strip()
    lower_stem = audio_stem.lower()
    supported_exts = {".txt", ".docx", ".srt", ".text"}

    nums = [int(n) for n in re.findall(r'\d+', audio_stem)]
    primary_num = nums[-1] if nums else None

    # Priority 1 candidate stems: e.g. "V1 Script", "V1_script", "V1", etc.
    exact_stems = [
        f"{audio_stem} Script",
        f"{audio_stem}_script",
        f"{audio_stem}-script",
        f"{audio_stem} script",
        audio_stem,
        f"{lower_stem} script",
        f"{lower_stem}_script",
        f"{lower_stem}-script",
        lower_stem,
    ]
    if primary_num is not None:
        exact_stems.extend([
            f"V{primary_num} Script",
            f"V{primary_num}_script",
            f"V{primary_num} script",
            f"v{primary_num} script",
            f"V{primary_num}",
            f"v{primary_num}",
            f"{primary_num} Script",
            f"{primary_num} script",
            f"{primary_num}"
        ])

    for sdir in search_dirs:
        # Check direct path matches in priority order (.txt, .docx, .srt, .text)
        for s_stem in exact_stems:
            for ext in [".txt", ".docx", ".srt", ".text"]:
                cand = os.path.join(sdir, f"{s_stem}{ext}")
                if os.path.exists(cand):
                    return cand

        # Directory scan (handles case variations and spacing)
        try:
            entries = os.listdir(sdir)
        except Exception:
            continue

        for fname in entries:
            fname_stem, ext = os.path.splitext(fname)
            if ext.lower() not in supported_exts:
                continue

            clean_fname_stem = fname_stem.strip()
            norm_fname = re.sub(r'[\s_\-]+', ' ', clean_fname_stem).strip().lower()
            norm_audio = re.sub(r'[\s_\-]+', ' ', lower_stem).strip().lower()

            if norm_fname == f"{norm_audio} script" or norm_fname == norm_audio:
                return os.path.join(sdir, fname)

            if primary_num is not None:
                fname_nums = [int(n) for n in re.findall(r'\d+', fname_stem)]
                if fname_nums and fname_nums[-1] == primary_num:
                    if "script" in norm_fname or norm_fname.startswith(f"v{primary_num}") or norm_fname.startswith(f"{primary_num}"):
                        return os.path.join(sdir, fname)

    # Priority 3: Fallback to aligner.validator
    try:
        try:
            from .aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers
        except (ImportError, ValueError):
            try:
                from aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers
            except (ImportError, ValueError):
                from backend.aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers
        for sdir in search_dirs:
            entries = os.listdir(sdir)
            script_map = {}
            script_list = []
            for fname in entries:
                stem, ext = os.path.splitext(fname)
                if ext.lower() in supported_exts:
                    full_p = os.path.join(sdir, fname)
                    norm = normalize_stem(stem)
                    stripped = normalize_stem(strip_common_prefixes(stem))
                    script_map[norm] = full_p
                    script_map[stripped] = full_p
                    script_list.append((stem, full_p, extract_numbers(stem), stripped))

            res = find_best_script_match(audio_stem, script_map, script_list)
            if res:
                return res
    except Exception:
        pass

    return None

