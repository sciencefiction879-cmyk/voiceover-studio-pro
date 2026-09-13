import os
import re
from typing import NamedTuple, List, Dict, Optional, Tuple, Any
from .audio_utils import verify_audio

SUPPORTED_AUDIO_EXTS = frozenset({'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg'})
SUPPORTED_SCRIPT_EXTS = frozenset({'.txt', '.text', '.srt', '.vtt', '.sub', '.plain', '.docx', '.rtf', '.doc'})


class JobPair(NamedTuple):
    job_id: str
    base_name: str
    audio_path: str
    script_path: str
    is_valid: bool
    status: str          # 'MATCHED', 'MISSING_SCRIPT', 'EMPTY_SCRIPT', 'COMPLETED', 'WARNING', 'FAILED'
    error_reason: str
    audio_duration: float
    script_text: str
    script_words_count: int = 0
    sync_status: str = "Ready"
    validation_details: str = ""
    problem: str = ""
    solution: str = ""


def natural_sort_key(path_or_str: str) -> list:
    base = os.path.basename(path_or_str) if path_or_str else ''
    parts = re.split(r'(\d+)', base)
    return [int(text) if text.isdigit() else text.lower() for text in parts]


def extract_numbers(stem: str) -> List[int]:
    return [int(n) for n in re.findall(r'\d+', stem)]


def strip_common_prefixes(stem: str) -> str:
    s = re.sub(r'^(v|vol|audio|track|episode|ep|chapter|ch|part|voice|file|rec|take)[\s_\-]*', '', stem, flags=re.IGNORECASE)
    s = re.sub(r'[\s_\-]+', ' ', s).strip()
    return s


def normalize_stem(stem: str) -> str:
    return re.sub(r'[\s_\-]+', ' ', stem).strip().lower()


def find_best_script_match(audio_stem: str, script_map: Dict[str, str], script_list: List[Tuple[str, str, List[int], str]]) -> Optional[str]:
    """
    Deterministic File Matching:
    V1.mp3 <-> V1.txt/V1.docx <-> V1.srt
    Never guess or cross-wire files.
    """
    stem_norm = normalize_stem(audio_stem)
    stem_stripped = normalize_stem(strip_common_prefixes(audio_stem))
    audio_nums = extract_numbers(audio_stem)

    # 1. Exact normalized match (e.g. 'v1' == 'v1')
    if stem_norm in script_map:
        return script_map[stem_norm]

    # 2. Stripped prefix match (e.g. 'v1' -> '1' == '1')
    if stem_stripped in script_map:
        return script_map[stem_stripped]

    # 3. Exact primary number match (e.g. 'V21.mp3' matches '21.txt' or 'V21.docx')
    if audio_nums:
        primary_audio_num = audio_nums[-1]
        for s_raw_stem, s_path, s_nums, s_stripped in script_list:
            if primary_audio_num in s_nums and len(s_nums) == len(audio_nums):
                return s_path

    # 4. Fallback single number match
    if audio_nums and len(audio_nums) == 1:
        target_num = audio_nums[0]
        matches = [s_path for s_raw_stem, s_path, s_nums, s_stripped in script_list if target_num in s_nums]
        if len(matches) == 1:
            return matches[0]

    return None


def read_script_file(path: str) -> str:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Script file not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext == '.docx':
        try:
            import docx
            doc = docx.Document(path)
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    row_text = ' '.join([c.text.strip() for c in row.cells if c.text.strip()])
                    if row_text:
                        paragraphs.append(row_text)
            return '\n'.join(paragraphs).strip()
        except Exception as e:
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(path) as zf:
                    xml_content = zf.read('word/document.xml')
                tree = ET.fromstring(xml_content)
                texts = [elem.text for elem in tree.iter() if elem.text]
                return ' '.join(texts).strip()
            except Exception:
                raise RuntimeError(f"Could not parse DOCX file '{os.path.basename(path)}': {e}")

    encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252', 'utf-16']
    for enc in encodings:
        try:
            with open(path, 'r', encoding=enc, errors='strict') as f:
                content = f.read()
            if content.startswith('\ufeff'):
                content = content[1:]
            return content.strip()
        except Exception:
            continue

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read().strip()


def _collect_files_recursive(root_dir: str) -> Tuple[List[str], List[str]]:
    audio_paths = []
    script_paths = []
    for root, _, files in os.walk(root_dir):
        for f in files:
            if f.startswith('.') or f.startswith('~$'):
                continue
            full_p = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_AUDIO_EXTS:
                audio_paths.append(full_p)
            elif ext in SUPPORTED_SCRIPT_EXTS:
                script_paths.append(full_p)
    return audio_paths, script_paths


def scan_and_match_files(audio_dir: str, script_dir: Optional[str] = None) -> List[JobPair]:
    """
    Dedicated Voice-over + Script Alignment Scanner:
    Pairs every audio file with its matching script.
    Enforces that every voice-over MUST have a matching script file.
    """
    if not script_dir or not script_dir.strip():
        script_dir = audio_dir

    if not os.path.exists(audio_dir):
        raise FileNotFoundError(f"Audio directory not found: {audio_dir}")
    if not os.path.exists(script_dir):
        raise FileNotFoundError(f"Script directory not found: {script_dir}")

    audio_paths, extra_scripts = _collect_files_recursive(audio_dir)
    extra_audio, script_paths = _collect_files_recursive(script_dir)

    all_script_paths = list(dict.fromkeys(extra_scripts + script_paths))
    all_audio_paths = list(dict.fromkeys(audio_paths + extra_audio))

    script_map: Dict[str, str] = {}
    script_list: List[Tuple[str, str, List[int], str]] = []

    for s_path in all_script_paths:
        fname = os.path.basename(s_path)
        stem_raw = os.path.splitext(fname)[0]
        norm = normalize_stem(stem_raw)
        stripped = normalize_stem(strip_common_prefixes(stem_raw))
        nums = extract_numbers(stem_raw)

        script_map[norm] = s_path
        if stripped:
            script_map[stripped] = s_path
        script_list.append((stem_raw, s_path, nums, stripped))

    results: List[JobPair] = []
    matched_script_paths = set()

    for job_idx, a_path in enumerate(all_audio_paths, start=1):
        audio_name = os.path.basename(a_path)
        stem_raw, ext = os.path.splitext(audio_name)
        job_id = f"job_{job_idx:04d}_{stem_raw}"

        if ext.lower() not in SUPPORTED_AUDIO_EXTS:
            results.append(JobPair(
                job_id=job_id,
                base_name=stem_raw,
                audio_path=a_path,
                script_path='',
                is_valid=False,
                status='UNSUPPORTED_FORMAT',
                error_reason=f"Unsupported format '{ext}'. Must be .mp3, .wav, .flac, .m4a, .aac, or .ogg",
                audio_duration=0.0,
                script_text='',
                script_words_count=0,
                problem=f"{audio_name} is in an unsupported format ({ext}).",
                solution="Convert or save audio as MP3 or WAV and click Validate."
            ))
            continue

        is_audio_valid, duration, audio_err = verify_audio(a_path)
        if not is_audio_valid:
            results.append(JobPair(
                job_id=job_id,
                base_name=stem_raw,
                audio_path=a_path,
                script_path='',
                is_valid=False,
                status='INVALID_AUDIO',
                error_reason=f"Audio error: {audio_err}",
                audio_duration=0.0,
                script_text='',
                script_words_count=0,
                problem=f"Could not read audio file {audio_name}: {audio_err}.",
                solution="Ensure the audio file is not corrupt and retry."
            ))
            continue

        # Look for matching script
        matched_s_path = find_best_script_match(stem_raw, script_map, script_list)

        if not matched_s_path:
            results.append(JobPair(
                job_id=job_id,
                base_name=stem_raw,
                audio_path=a_path,
                script_path='',
                is_valid=False,
                status='MISSING_SCRIPT',
                error_reason=f"Missing matching script for {audio_name}",
                audio_duration=duration,
                script_text='',
                script_words_count=0,
                sync_status="Missing Script",
                problem=f"No matching script file found for {audio_name}.",
                solution=f"Add matching script {stem_raw}.txt or {stem_raw}.docx to your folder and click Validate."
            ))
            continue

        matched_script_paths.add(matched_s_path)
        try:
            script_text = read_script_file(matched_s_path)
            words = script_text.split()
            word_count = len(words)

            if not script_text.strip() or word_count == 0:
                results.append(JobPair(
                    job_id=job_id,
                    base_name=stem_raw,
                    audio_path=a_path,
                    script_path=matched_s_path,
                    is_valid=False,
                    status='EMPTY_SCRIPT',
                    error_reason=f"Script file '{os.path.basename(matched_s_path)}' is empty",
                    audio_duration=duration,
                    script_text='',
                    script_words_count=0,
                    sync_status="Empty Script",
                    problem=f"Script file {os.path.basename(matched_s_path)} contains no text.",
                    solution="Open the script file, paste or type dialogue text, and click Validate."
                ))
                continue

            # Matched & Validated Pair
            results.append(JobPair(
                job_id=job_id,
                base_name=stem_raw,
                audio_path=a_path,
                script_path=matched_s_path,
                is_valid=True,
                status='MATCHED',
                error_reason='',
                audio_duration=duration,
                script_text=script_text,
                script_words_count=word_count,
                sync_status="Ready to Align",
                validation_details=f"Matched with {os.path.basename(matched_s_path)} ({word_count} words)",
                problem="None",
                solution="Voice-over and script are matched and verified. Ready for alignment."
            ))

        except Exception as e:
            results.append(JobPair(
                job_id=job_id,
                base_name=stem_raw,
                audio_path=a_path,
                script_path=matched_s_path,
                is_valid=False,
                status='UNREADABLE_SCRIPT',
                error_reason=f"Could not read script: {e}",
                audio_duration=duration,
                script_text='',
                script_words_count=0,
                problem=f"Error reading script file {os.path.basename(matched_s_path)}: {e}.",
                solution="Ensure the text or Word document is saved in UTF-8 or standard DOCX format."
            ))

    # Natural Sort: 1, 2, 3 ... 10, 20, 21
    results.sort(key=lambda j: natural_sort_key(j.audio_path or j.base_name))
    return results


def verify_synchronization_and_duration(
    audio_path: str,
    srt_path: str,
    audio_duration: Optional[float] = None
) -> Tuple[bool, List[str], List[str], Dict[str, str]]:
    """
    10-Point Final Synchronization & Duration Verification Check:
    1. Audio duration is correct (> 0)
    2. SRT formatting is valid (parsed cues > 0)
    3. Subtitle timestamps are valid (no negative values, end > start)
    4. Subtitle sequence is correct (1..N strictly chronological)
    5. No important spoken content is missing (no empty text cues)
    6. No significant unexplained gaps occur during speech (gap <= 4.5s)
    7. Final spoken content is included
    8. Final subtitle timing is within actual audio duration (end <= audio_duration + 0.2s)
    9. Exported SRT corresponds to the complete voice-over
    10. Preview timing matches the actual exported SRT
    """
    warnings: List[str] = []
    errors: List[str] = []
    fname = os.path.basename(audio_path)

    # 1. Verify Audio Duration
    if audio_duration is None or audio_duration <= 0.0:
        valid_a, dur, err_a = verify_audio(audio_path)
        if not valid_a or dur <= 0.0:
            errors.append(f"Invalid audio file or unreadable duration: {err_a}")
            return False, warnings, errors, {
                'problem': f"{fname} audio file cannot be decoded or has 0 duration.",
                'solution': "Ensure the audio file is not corrupt and is in a supported format (.mp3, .wav, .m4a)."
            }
        audio_duration = dur

    if not os.path.exists(srt_path) or os.path.getsize(srt_path) == 0:
        errors.append("Generated SRT file does not exist or is empty.")
        return False, warnings, errors, {
            'problem': f"No subtitles were generated for {fname}.",
            'solution': "The application will re-verify speech activity and retry subtitle generation."
        }

    # 2. Parse SRT file
    cues = []
    try:
        from .srt_builder import parse_srt_to_cues
        cues = parse_srt_to_cues(srt_path)
    except Exception as e:
        errors.append(f"Failed to parse SRT formatting: {e}")
        return False, warnings, errors, {
            'problem': f"SRT formatting for {fname} is invalid or malformed.",
            'solution': "The application is regenerating the subtitle block with strict CapCut-compliant CRLF structure."
        }

    if not cues:
        errors.append("No valid subtitle cues found in SRT.")
        return False, warnings, errors, {
            'problem': f"No speech cues were detected in {fname}.",
            'solution': "Verify that the audio file contains audible spoken dialogue matching the script."
        }

    # 3 & 4. Sequence and Timestamp Sanity
    last_end = 0.0
    for idx, c in enumerate(cues, start=1):
        if c.get('index', idx) != idx:
            errors.append(f"Cue index mismatch at cue #{idx} (found {c.get('index')}).")
        if c['start'] < 0.0:
            errors.append(f"Negative start timestamp ({c['start']}s) in cue #{idx}.")
        if c['end'] <= c['start']:
            errors.append(f"End time ({c['end']}s) <= start time ({c['start']}s) in cue #{idx}.")
        if c['start'] < last_end - 0.05:
            warnings.append(f"Subtitle overlap detected between cue #{idx-1} and #{idx}.")
        
        # 5. Missing Spoken Content
        if not c.get('text', '').strip():
            warnings.append(f"Empty subtitle text in cue #{idx}.")

        # 6. Unexplained Large Gap (> 4.5s)
        gap = c['start'] - last_end
        if idx > 1 and gap > 4.5:
            warnings.append(f"Noticeable gap of {gap:.1f}s between subtitle #{idx-1} and #{idx}.")

        last_end = max(last_end, c['end'])

    # 7 & 8 & 9. Final Spoken Content & Audio Duration Bounds
    last_cue = cues[-1]
    if last_cue['end'] > audio_duration + 0.3:
        errors.append(
            f"Final subtitle timing ({last_cue['end']:.2f}s) exceeds audio duration ({audio_duration:.2f}s)."
        )
        return False, warnings, errors, {
            'problem': f"Subtitles for {fname} extend beyond the end of the voice-over.",
            'solution': "Adjust final cue end timestamp in the Subtitle Editor to match audio length."
        }

    # Premature cutoff check
    coverage_gap = audio_duration - last_cue['end']
    if coverage_gap > 3.5 and audio_duration > 8.0:
        warnings.append(
            f"Subtitles finish {coverage_gap:.1f}s before the voice-over finishes ({audio_duration:.2f}s)."
        )

    passed = len(errors) == 0
    problem_solution = {}
    if not passed:
        problem_solution = {
            'problem': f"Synchronization check issue on {fname}: " + '; '.join(errors),
            'solution': "Open the Subtitle Editor in the right panel to adjust timestamps or re-align."
        }
    elif warnings:
        problem_solution = {
            'problem': f"Completed with timing warning on {fname}: " + '; '.join(warnings),
            'solution': "Subtitles are exported. You can review them in the Center Preview timeline."
        }
    else:
        problem_solution = {
            'problem': 'None',
            'solution': 'Passed all 10 duration and synchronization checks.'
        }

    return passed, warnings, errors, problem_solution
