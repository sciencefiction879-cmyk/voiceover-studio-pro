import os
import re
from typing import List, Dict, Any, Optional

PUNCTUATION_SPLIT_CHARS = frozenset({')', '॥', '।', '—', '،', '۔', '-', '!', '"', '.', '»', ',', ':', ';', '؟', '?'})
TIMESTAMP_TOKEN_REGEX = re.compile(r'^(?:\[|\()?\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?(?:\]|\))?$')
INLINE_TIMESTAMP_REGEX = re.compile(r'(?:\[|\()?\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?\b(?:\]|\))?')


def is_timestamp_token(tok: str) -> bool:
    t = tok.strip().strip('[]()<>{},;"\'').strip()
    if not t:
        return False
    return bool(TIMESTAMP_TOKEN_REGEX.match(t))


def sanitize_subtitle_text(text: str) -> str:
    if not text:
        return ''

    # Remove isolated line numbers
    text = re.sub(r'(?m)^\s*\d+\s*$', '', text)
    # Remove SRT timing lines
    text = re.sub(r'(?m)^\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?.*$', '', text)
    # Remove WEBVTT headers
    text = re.sub(r'(?m)^WEBVTT.*$', '', text)
    # Remove inline timestamp tokens
    text = INLINE_TIMESTAMP_REGEX.sub('', text)
    # Clean arrow separators
    text = re.sub(r'\s*-->\s*', ' ', text)

    lines = [re.sub(r'[ \t]+', ' ', l).strip() for l in text.splitlines()]
    clean_lines = [l for l in lines if l]
    return '\r\n'.join(clean_lines)


def parse_timestamp_str(ts_str: str) -> float:
    """Converts '00:01:23,456' or '00:01:23.456' into float seconds."""
    ts_str = ts_str.strip().replace(',', '.')
    parts = ts_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600.0 + float(m) * 60.0 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return float(m) * 60.0 + float(s)
    return float(ts_str)


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0

    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        seconds += 1.0
        millis = 0

    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


class SubtitleCue:
    def __init__(self, index: int, start: float, end: float, lines: List[str]):
        self.index = index
        self.start = max(0.0, start)
        self.end = max(self.start + 0.2, end)

        sanitized_lines = []
        for l in lines:
            s_l = sanitize_subtitle_text(l)
            if s_l:
                sanitized_lines.extend([sub_l.strip() for sub_l in s_l.splitlines() if sub_l.strip()])
        self.lines = sanitized_lines

    @property
    def text(self) -> str:
        return '\r\n'.join(self.lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'index': self.index,
            'start': round(self.start, 3),
            'end': round(self.end, 3),
            'start_str': format_timestamp(self.start),
            'end_str': format_timestamp(self.end),
            'text': self.text,
            'lines': list(self.lines)
        }

    def to_srt_block(self) -> str:
        start_str = format_timestamp(self.start)
        end_str = format_timestamp(self.end)
        return f"{self.index}\r\n{start_str} --> {end_str}\r\n{self.text}"

    def to_vtt_block(self) -> str:
        start_str = format_timestamp(self.start).replace(',', '.')
        end_str = format_timestamp(self.end).replace(',', '.')
        return f"{start_str} --> {end_str}\r\n{self.text}"


def split_text_into_two_lines(words: List[str], max_chars_per_line: int = 38) -> List[str]:
    clean_words = [w for w in words if w and not is_timestamp_token(w)]
    if not clean_words:
        return []

    full_text = ' '.join(clean_words)
    if len(full_text) <= max_chars_per_line:
        return [full_text]

    if len(clean_words) == 1:
        return [clean_words[0]]

    best_split_idx = 1
    best_score = float('inf')

    for i in range(1, len(clean_words)):
        line1 = ' '.join(clean_words[:i])
        line2 = ' '.join(clean_words[i:])
        len1 = len(line1)
        len2 = len(line2)

        penalty = 0
        if len1 > max_chars_per_line:
            penalty += (len1 - max_chars_per_line) * 20
        if len2 > max_chars_per_line:
            penalty += (len2 - max_chars_per_line) * 20

        punct_bonus = 0
        last_ch = clean_words[i - 1][-1] if clean_words[i - 1] else ''
        if last_ch in PUNCTUATION_SPLIT_CHARS:
            punct_bonus = -18

        diff = abs(len1 - len2)
        score = diff + penalty + punct_bonus

        if score < best_score:
            best_score = score
            best_split_idx = i

    line1 = ' '.join(clean_words[:best_split_idx])
    line2 = ' '.join(clean_words[best_split_idx:])
    res = [l for l in (line1, line2) if l.strip()]
    return res if res else [full_text]


def build_cues_list(
    word_segments: List[Dict[str, Any]],
    max_chars_per_line: int = 38,
    max_lines_per_cue: int = 2,
    max_duration_sec: float = 4.2,
    max_chars_per_cue: int = 76,
    pause_threshold_sec: float = 0.3
) -> List[SubtitleCue]:
    if not word_segments:
        return []

    cues: List[SubtitleCue] = []
    current_cue_words: List[Dict[str, Any]] = []

    def flush_cue():
        nonlocal current_cue_words
        if not current_cue_words:
            return

        start_time = current_cue_words[0]['start']
        end_time = current_cue_words[-1]['end']
        if (end_time - start_time) < 0.3:
            end_time = start_time + 0.3

        words_text = [
            w.get('word', '').strip()
            for w in current_cue_words
            if w.get('word', '').strip() and not is_timestamp_token(w.get('word', ''))
        ]
        if not words_text:
            current_cue_words = []
            return

        lines = split_text_into_two_lines(words_text, max_chars_per_line=max_chars_per_line)
        cues.append(SubtitleCue(
            index=len(cues) + 1,
            start=round(start_time, 3),
            end=round(end_time, 3),
            lines=lines[:max_lines_per_cue]
        ))
        current_cue_words = []

    for i, w_seg in enumerate(word_segments):
        w_word = w_seg.get('word', '').strip()
        if not w_word or is_timestamp_token(w_word):
            continue

        if current_cue_words:
            prev_w = current_cue_words[-1]
            pause = w_seg['start'] - prev_w['end']
            current_text = ' '.join([w['word'] for w in current_cue_words] + [w_word])
            current_dur = w_seg['end'] - current_cue_words[0]['start']
            last_char = prev_w['word'][-1] if prev_w['word'] else ''
            is_sentence_end = last_char in frozenset({'!', '.', '۔', '؟', '?', '॥', '।'})

            if (
                pause >= pause_threshold_sec or
                current_dur >= max_duration_sec or
                len(current_text) > max_chars_per_cue or
                is_sentence_end
            ):
                flush_cue()

        current_cue_words.append(w_seg)

    flush_cue()
    return cues


def build_sentence_cues_from_words(
    word_segments: List[Dict[str, Any]],
    max_chars_per_line: int = 48,
    max_lines: int = 2
) -> List[Dict[str, Any]]:
    """Helper wrapper for converting word segments to sentence-level cues dicts."""
    raw_cues = build_cues_list(
        word_segments,
        max_chars_per_line=max_chars_per_line,
        max_lines_per_cue=max_lines
    )
    return [
        {"start": c.start, "end": c.end, "text": c.text}
        for c in raw_cues
    ]


def validate_and_sanitize_cues(cues: List[SubtitleCue]) -> List[SubtitleCue]:
    if not cues:
        return []

    valid_cues: List[SubtitleCue] = []
    last_end = 0.0

    for cue in cues:
        clean_lines = []
        for line in cue.lines:
            s_l = sanitize_subtitle_text(line)
            if s_l:
                for sub_l in s_l.splitlines():
                    if sub_l.strip():
                        clean_lines.append(sub_l.strip())

        if not clean_lines:
            continue

        cue_start = max(0.0, cue.start)
        if cue_start < last_end:
            cue_start = last_end

        cue_end = max(cue_start + 0.2, cue.end)
        last_end = cue_end

        valid_cues.append(SubtitleCue(
            index=len(valid_cues) + 1,
            start=cue_start,
            end=cue_end,
            lines=clean_lines
        ))

    return valid_cues


def build_srt_from_word_segments(
    word_segments: List[Dict[str, Any]],
    max_chars_per_line: int = 38,
    max_lines_per_cue: int = 2,
    max_duration_sec: float = 4.2,
    max_chars_per_cue: int = 76,
    pause_threshold_sec: float = 0.3
) -> str:
    raw_cues = build_cues_list(
        word_segments,
        max_chars_per_line=max_chars_per_line,
        max_lines_per_cue=max_lines_per_cue,
        max_duration_sec=max_duration_sec,
        max_chars_per_cue=max_chars_per_cue,
        pause_threshold_sec=pause_threshold_sec
    )
    cues = validate_and_sanitize_cues(raw_cues)
    if not cues:
        return ''

    blocks = [cue.to_srt_block() for cue in cues]
    return '\r\n\r\n'.join(blocks).strip() + '\r\n'


def build_vtt_from_word_segments(
    word_segments: List[Dict[str, Any]],
    max_chars_per_line: int = 38,
    max_lines_per_cue: int = 2,
    max_duration_sec: float = 4.2,
    max_chars_per_cue: int = 76,
    pause_threshold_sec: float = 0.3
) -> str:
    raw_cues = build_cues_list(
        word_segments,
        max_chars_per_line=max_chars_per_line,
        max_lines_per_cue=max_lines_per_cue,
        max_duration_sec=max_duration_sec,
        max_chars_per_cue=max_chars_per_cue,
        pause_threshold_sec=pause_threshold_sec
    )
    cues = validate_and_sanitize_cues(raw_cues)
    if not cues:
        return ''

    blocks = [cue.to_vtt_block() for cue in cues]
    return 'WEBVTT\r\n\r\n' + '\r\n\r\n'.join(blocks).strip() + '\r\n'


def extract_clean_text_from_srt(srt_text: str) -> str:
    if not srt_text:
        return ''
    return sanitize_subtitle_text(srt_text)


# ==========================================================
# Subtitle Parsing & Interactive Editor Operations
# ==========================================================

def parse_srt_to_cues(srt_content_or_path: str) -> List[Dict[str, Any]]:
    """Parses SRT file path or content string into structured cue dictionary list."""
    content = srt_content_or_path
    if os.path.exists(srt_content_or_path):
        with open(srt_content_or_path, 'r', encoding='utf-8-sig', errors='replace') as f:
            content = f.read()

    blocks = re.split(r'\r?\n\r?\n', content.strip())
    cues: List[Dict[str, Any]] = []

    for blk in blocks:
        lines = [l.strip() for l in blk.splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        timing_line_idx = -1
        for idx, line in enumerate(lines):
            if '-->' in line:
                timing_line_idx = idx
                break

        if timing_line_idx == -1:
            continue

        timing_parts = lines[timing_line_idx].split('-->')
        if len(timing_parts) != 2:
            continue

        start_str = timing_parts[0].strip()
        end_str = timing_parts[1].strip().split(' ')[0].strip()

        try:
            start_sec = parse_timestamp_str(start_str)
            end_sec = parse_timestamp_str(end_str)
        except Exception:
            continue

        cue_text_lines = lines[timing_line_idx + 1:]
        clean_text_lines = [l for l in cue_text_lines if l and not is_timestamp_token(l)]

        cues.append({
            'index': len(cues) + 1,
            'start': round(start_sec, 3),
            'end': round(end_sec, 3),
            'start_str': format_timestamp(start_sec),
            'end_str': format_timestamp(end_sec),
            'text': '\r\n'.join(clean_text_lines),
            'lines': clean_text_lines
        })

    return cues


def format_cues_to_srt(cues: List[Dict[str, Any]]) -> str:
    """Formats a list of cue dictionaries back into strict CRLF SRT content."""
    blocks = []
    for idx, c in enumerate(cues, start=1):
        start_str = format_timestamp(c['start'])
        end_str = format_timestamp(c['end'])
        text = c.get('text', '').strip()
        blocks.append(f"{idx}\r\n{start_str} --> {end_str}\r\n{text}")
    return '\r\n\r\n'.join(blocks).strip() + '\r\n'


def format_cues_to_vtt(cues: List[Dict[str, Any]]) -> str:
    """Formats cue dictionaries to WebVTT."""
    blocks = []
    for idx, c in enumerate(cues, start=1):
        start_str = format_timestamp(c['start']).replace(',', '.')
        end_str = format_timestamp(c['end']).replace(',', '.')
        text = c.get('text', '').strip()
        blocks.append(f"{start_str} --> {end_str}\r\n{text}")
    return 'WEBVTT\r\n\r\n' + '\r\n\r\n'.join(blocks).strip() + '\r\n'


def split_cue(cues: List[Dict[str, Any]], cue_index: int) -> List[Dict[str, Any]]:
    """Splits target cue into two balanced subtitle cues."""
    target_idx = cue_index - 1
    if target_idx < 0 or target_idx >= len(cues):
        return cues

    target = cues[target_idx]
    words = target.get('text', '').split()
    if len(words) <= 1:
        # Split single word or short cue in half time-wise
        mid_time = round((target['start'] + target['end']) / 2.0, 3)
        c1 = dict(target, end=mid_time, end_str=format_timestamp(mid_time))
        c2 = dict(target, start=mid_time, start_str=format_timestamp(mid_time))
    else:
        half = len(words) // 2
        text1 = ' '.join(words[:half])
        text2 = ' '.join(words[half:])
        dur = target['end'] - target['start']
        mid_time = round(target['start'] + dur * (half / len(words)), 3)
        c1 = {
            'start': target['start'],
            'end': mid_time,
            'start_str': format_timestamp(target['start']),
            'end_str': format_timestamp(mid_time),
            'text': text1,
            'lines': [text1]
        }
        c2 = {
            'start': mid_time,
            'end': target['end'],
            'start_str': format_timestamp(mid_time),
            'end_str': format_timestamp(target['end']),
            'text': text2,
            'lines': [text2]
        }

    new_cues = list(cues[:target_idx]) + [c1, c2] + list(cues[target_idx + 1:])
    return resequence_cues(new_cues)


def merge_cues(cues: List[Dict[str, Any]], cue_index: int, next_cue: bool = True) -> List[Dict[str, Any]]:
    """Merges target cue with adjacent cue."""
    idx = cue_index - 1
    other_idx = idx + 1 if next_cue else idx - 1

    if idx < 0 or idx >= len(cues) or other_idx < 0 or other_idx >= len(cues):
        return cues

    first_idx, second_idx = min(idx, other_idx), max(idx, other_idx)
    c1 = cues[first_idx]
    c2 = cues[second_idx]

    merged = {
        'start': c1['start'],
        'end': c2['end'],
        'start_str': format_timestamp(c1['start']),
        'end_str': format_timestamp(c2['end']),
        'text': f"{c1.get('text', '')}\r\n{c2.get('text', '')}".strip(),
        'lines': c1.get('lines', []) + c2.get('lines', [])
    }

    new_cues = list(cues[:first_idx]) + [merged] + list(cues[second_idx + 1:])
    return resequence_cues(new_cues)


def offset_timing(cues: List[Dict[str, Any]], cue_index: Optional[int], delta_seconds: float, all_subsequent: bool = False) -> List[Dict[str, Any]]:
    """Shifts cue timing by delta_seconds (+/- 0.1s, +/- 0.5s)."""
    new_cues = [dict(c) for c in cues]
    start_i = 0 if cue_index is None else max(0, cue_index - 1)
    end_i = len(new_cues) if (all_subsequent or cue_index is None) else start_i + 1

    for i in range(start_i, min(end_i, len(new_cues))):
        c = new_cues[i]
        new_start = max(0.0, round(c['start'] + delta_seconds, 3))
        new_end = max(new_start + 0.2, round(c['end'] + delta_seconds, 3))
        c['start'] = new_start
        c['end'] = new_end
        c['start_str'] = format_timestamp(new_start)
        c['end_str'] = format_timestamp(new_end)

    return resequence_cues(new_cues)


def resequence_cues(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-indexes cues sequentially 1..N and ensures valid non-negative chronological bounds."""
    last_end = 0.0
    res = []
    for idx, c in enumerate(cues, start=1):
        c_dict = dict(c)
        c_dict['index'] = idx
        c_dict['start'] = max(0.0, c_dict.get('start', 0.0))
        c_dict['end'] = max(c_dict['start'] + 0.15, c_dict.get('end', c_dict['start'] + 0.15))
        c_dict['start_str'] = format_timestamp(c_dict['start'])
        c_dict['end_str'] = format_timestamp(c_dict['end'])
        res.append(c_dict)
    return res


def save_cues_to_disk(cues: List[Dict[str, Any]], srt_path: str) -> None:
    """Saves updated cue list to .srt and synchronizes corresponding .vtt and .txt."""
    dir_name = os.path.dirname(srt_path)
    base_name = os.path.splitext(os.path.basename(srt_path))[0]
    os.makedirs(dir_name, exist_ok=True)

    srt_content = format_cues_to_srt(cues)
    vtt_content = format_cues_to_vtt(cues)
    txt_content = extract_clean_text_from_srt(srt_content)

    with open(srt_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(srt_content)

    vtt_path = os.path.join(dir_name, f"{base_name}.vtt")
    with open(vtt_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(vtt_content)

    txt_path = os.path.join(dir_name, f"{base_name}.txt")
    with open(txt_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(txt_content)
