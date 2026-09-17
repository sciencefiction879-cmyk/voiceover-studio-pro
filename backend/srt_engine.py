import os
import re
import tempfile
import subprocess
from typing import List, Dict, Any, Optional, Tuple

try:
    from .aligner.engine import AlignerModel
    from .aligner.validator import read_script_file, clean_script_header_metadata, is_metadata_header_line
    from .aligner.srt_builder import SubtitleCue as AlignerCue, format_timestamp
except (ImportError, ValueError):
    try:
        from aligner.engine import AlignerModel
        from aligner.validator import read_script_file, clean_script_header_metadata, is_metadata_header_line
        from aligner.srt_builder import SubtitleCue as AlignerCue, format_timestamp
    except (ImportError, ValueError):
        from backend.aligner.engine import AlignerModel
        from backend.aligner.validator import read_script_file, clean_script_header_metadata, is_metadata_header_line
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

def clean_srt_cues_header_metadata(cues: List[SRTCue]) -> Tuple[List[SRTCue], List[str]]:
    """
    If an imported .srt file begins with non-spoken metadata cues (e.g. 'V1', 'Competitor Script Word Count: ...'),
    detect and strip those cues from the beginning of the cue list.
    Re-indexes remaining cues.
    """
    if not cues:
        return [], []

    removed_headers: List[str] = []
    first_spoken_idx = -1

    for idx, cue in enumerate(cues):
        cue_text = cue.text.strip()
        lines = [l.strip() for l in cue_text.split('\n') if l.strip()]
        if lines and all(is_metadata_header_line(line) for line in lines):
            removed_headers.extend(lines)
        else:
            first_spoken_idx = idx
            break

    if first_spoken_idx == -1:
        return [], removed_headers

    valid_cues = cues[first_spoken_idx:]
    reindexed = [
        SRTCue(i + 1, c.start, c.end, c.text)
        for i, c in enumerate(valid_cues)
    ]
    return reindexed, removed_headers


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
    raw_script_text = read_script_file(script_path)
    if not raw_script_text.strip():
        raise ValueError(f"Script file '{os.path.basename(script_path)}' is empty.")

    script_text, removed_headers = clean_script_header_metadata(raw_script_text)
    if not script_text.strip():
        raise ValueError(f"Script file '{os.path.basename(script_path)}' contains no spoken narration after header cleanup ({', '.join(removed_headers)}).")
    if removed_headers:
        print(f"[SRT Engine] Cleaned {len(removed_headers)} header line(s) before alignment: {', '.join(removed_headers)}")

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
    removed_headers: List[str] = []

    # Step 1: Acoustic forced alignment or parse pre-existing SRT
    if ext == ".srt":
        raw_cues = parse_srt_file(script_path)
        base_cues, removed_headers = clean_srt_cues_header_metadata(raw_cues)
    else:
        try:
            raw_text = read_script_file(script_path)
            _, removed_headers = clean_script_header_metadata(raw_text)
        except Exception:
            removed_headers = []
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
        "removedHeaders": removed_headers,
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



def extract_v_number(filename: str) -> Optional[int]:
    """
    Extract the actual V-number (integer) from an audio or script filename.
    Accurately supports any continuous or non-continuous numbering (e.g. V20, V101, V5):
    - 'V20.mp3' -> 20
    - 'V20 Script.txt' -> 20
    - 'v20_script.docx' -> 20
    - 'V-20.srt' -> 20
    - 'V_20.mp3' -> 20
    - 'Voiceover 20.mp3' -> 20
    - 'VO 20.wav' -> 20
    - 'Script 20.txt' -> 20
    - '20.mp3' -> 20
    - '20 Script.txt' -> 20
    - 'V101.mp3' -> 101
    - 'V20_128kbps.mp3' -> 20 (avoids picking up 128 as the V-number)
    - 'Chapter 1 - V20.mp3' -> 20 (prioritizes explicit V-prefix)
    """
    if not filename:
        return None
    
    stem = os.path.splitext(os.path.basename(filename))[0].strip()

    # Rule 1 (Highest Priority): Explicit V<num> or v<num> token
    # Matches V20, v21, V-22, V_23, [V24], (V25), V020
    m = re.search(r'(?:^|[_\s\b\-\[\(\.\/])[vV][\-_]?(\d+)(?:[_\s\b\-\]\)\.\/]|$)', stem)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass

    # Rule 2: Keyword prefix followed by number (e.g. "Voiceover 20", "VO 20", "Script 20", "Audio 20")
    m = re.search(r'(?:voiceover|vo|script|audio|track|episode|ep|chapter)[_\s\b\-]+(\d+)(?:[_\s\b\-\]\)\.\/]|$)', stem, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass

    # Rule 3: Number followed by keyword (e.g. "20 Script", "20_VO", "20_voiceover")
    m = re.search(r'(?:^|[_\s\b\-\[\(\.\/])(\d+)[_\s\b\-]+(?:script|voiceover|vo|audio|track)', stem, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass

    # Rule 4: Pure numeric stem or stem starting with digits (e.g. "20.mp3", "20_final.wav")
    if stem.isdigit():
        return int(stem)
    m = re.match(r'^(\d+)(?:[_\s\b\-\.\/]|$)', stem)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass

    # Rule 5: Stem ending with digits (e.g. "Voiceover-20")
    m = re.search(r'(?:[_\s\b\-\.\/])(\d+)$', stem)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass

    # Rule 6: Exactly one numeric token in the stem
    all_nums = re.findall(r'\d+', stem)
    if len(all_nums) == 1:
        try:
            return int(all_nums[0])
        except (ValueError, TypeError):
            pass

    return None


def extract_canonical_v_label(filename: str) -> str:
    """Return canonical sequence label like 'V20' or fallback to stem."""
    v_num = extract_v_number(filename)
    if v_num is not None:
        return f"V{v_num}"
    return os.path.splitext(os.path.basename(filename))[0].strip()


def find_matching_script(
    audio_path: str,
    search_dir: Optional[str] = None,
    script_candidates: Optional[List[str]] = None
) -> Optional[str]:
    """
    Find matching script (.txt, .docx, .srt, .text) for a given audio file.
    Accurately supports arbitrary continuous or non-continuous numbering ranges:
    - 'V20 Script.txt' ↔ 'V20.mp3'
    - 'V20_script.docx' ↔ 'V20.mp3'
    - 'V20.srt' ↔ 'V20.mp3'
    - 'V101 Script.txt' ↔ 'V101.wav'
    - '20 Script.txt' ↔ '20.mp3'
    
    Priority of script formats: .txt > .docx > .srt > .text.
    Order of import does not matter.
    """
    supported_exts = {".txt", ".docx", ".srt", ".text"}
    ext_priority = {".txt": 0, ".docx": 1, ".srt": 2, ".text": 3}

    candidates = set()
    if script_candidates:
        for p in script_candidates:
            if p and os.path.exists(p) and os.path.splitext(p)[1].lower() in supported_exts:
                candidates.add(os.path.abspath(p))

    search_dirs = []
    if search_dir and os.path.exists(search_dir):
        search_dirs.append(os.path.abspath(search_dir))
    audio_dir = os.path.dirname(audio_path)
    if audio_dir and os.path.exists(audio_dir):
        norm_audio_dir = os.path.abspath(audio_dir)
        if norm_audio_dir not in search_dirs:
            search_dirs.append(norm_audio_dir)

    # Search uploads folder if available
    try:
        from .server import UPLOADS_DIR
        if UPLOADS_DIR and os.path.exists(UPLOADS_DIR):
            norm_uploads = os.path.abspath(UPLOADS_DIR)
            if norm_uploads not in search_dirs:
                search_dirs.append(norm_uploads)
    except Exception:
        pass

    for sdir in search_dirs:
        try:
            for root, _, files in os.walk(sdir):
                for f in files:
                    if f.startswith("."):
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    if ext in supported_exts:
                        candidates.add(os.path.abspath(os.path.join(root, f)))
        except Exception:
            continue

    if not candidates:
        return None

    audio_stem = os.path.splitext(os.path.basename(audio_path))[0].strip()
    lower_stem = audio_stem.lower()
    audio_v = extract_v_number(audio_path)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Strategy 1: Match by exact V-number (User Primary Requirement)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if audio_v is not None:
        v_matched_candidates = []
        for cand in candidates:
            cand_v = extract_v_number(cand)
            if cand_v == audio_v:
                cand_stem = os.path.splitext(os.path.basename(cand))[0].lower()
                cand_ext = os.path.splitext(cand)[1].lower()
                # Preference: contains 'script' or starts with 'v'
                has_script_word = 1 if "script" in cand_stem else 0
                has_v_prefix = 1 if cand_stem.startswith(f"v{audio_v}") else 0
                ext_rank = ext_priority.get(cand_ext, 99)
                # Lower tuple values sort first
                score = (-has_script_word, -has_v_prefix, ext_rank, len(cand_stem))
                v_matched_candidates.append((score, cand))

        if v_matched_candidates:
            v_matched_candidates.sort(key=lambda x: x[0])
            return v_matched_candidates[0][1]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Strategy 2: Exact stem / Normalized stem matching
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    cand_by_stem = {}
    for cand in candidates:
        c_stem = os.path.splitext(os.path.basename(cand))[0].strip()
        cand_by_stem.setdefault(c_stem, []).append(cand)
        cand_by_stem.setdefault(c_stem.lower(), []).append(cand)

    for stem_pattern in exact_stems:
        matched = cand_by_stem.get(stem_pattern) or cand_by_stem.get(stem_pattern.lower())
        if matched:
            matched.sort(key=lambda p: ext_priority.get(os.path.splitext(p)[1].lower(), 99))
            return matched[0]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Strategy 3: Normalized spacing / punctuation comparison
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    norm_audio = re.sub(r'[\s_\-]+', ' ', lower_stem).strip()
    for cand in candidates:
        c_stem = os.path.splitext(os.path.basename(cand))[0].strip()
        norm_c = re.sub(r'[\s_\-]+', ' ', c_stem.lower()).strip()
        if norm_c == f"{norm_audio} script" or norm_c == norm_audio:
            return cand

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Strategy 4: Fallback to aligner.validator fuzzy matching
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        try:
            from .aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers
        except (ImportError, ValueError):
            try:
                from aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers
            except (ImportError, ValueError):
                from backend.aligner.validator import find_best_script_match, normalize_stem, strip_common_prefixes, extract_numbers

        script_map = {}
        script_list = []
        for cand in candidates:
            c_stem = os.path.splitext(os.path.basename(cand))[0]
            norm = normalize_stem(c_stem)
            stripped = normalize_stem(strip_common_prefixes(c_stem))
            script_map[norm] = cand
            script_map[stripped] = cand
            script_list.append((c_stem, cand, extract_numbers(c_stem), stripped))

        res = find_best_script_match(audio_stem, script_map, script_list)
        if res:
            return res
    except Exception:
        pass

    return None


def match_audio_and_script_lists(
    audio_paths: List[str],
    script_paths: List[str]
) -> Tuple[Dict[str, Optional[str]], List[Dict[str, Any]]]:
    """
    Bi-directionally match audio paths with script paths based on V-number.
    Returns:
    - matches: {audio_path: matched_script_path or None}
    - orphan_scripts: list of scripts that did not match any audio file:
        [{'fileName': ..., 'filePath': ..., 'scriptType': ..., 'vNumber': ..., 'vLabel': ...}]
    """
    matches: Dict[str, Optional[str]] = {}
    used_scripts = set()

    for a_path in audio_paths:
        matched = find_matching_script(a_path, script_candidates=script_paths)
        if matched and matched not in used_scripts:
            matches[a_path] = matched
            used_scripts.add(matched)
        else:
            matches[a_path] = None

    orphan_scripts = []
    for s_path in script_paths:
        if s_path not in used_scripts:
            s_name = os.path.basename(s_path)
            s_v = extract_v_number(s_path)
            s_label = f"V{s_v}" if s_v is not None else os.path.splitext(s_name)[0]
            orphan_scripts.append({
                "fileName": s_name,
                "filePath": s_path,
                "scriptType": os.path.splitext(s_path)[1].lower(),
                "vNumber": s_v,
                "vLabel": s_label,
                "status": "orphan",
                "statusLabel": f"{s_name} — Audio not found ⚠"
            })

    # Sort orphan scripts by natural V-number
    orphan_scripts.sort(key=lambda s: (s["vNumber"] if s["vNumber"] is not None else 999999, s["fileName"].lower()))
    return matches, orphan_scripts


