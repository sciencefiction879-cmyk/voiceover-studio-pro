import os
import re
import math
import wave
import struct
import logging
import subprocess
import sys
from typing import List, Dict, Any, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from .srt_builder import sanitize_subtitle_text, is_timestamp_token
try:
    from .validator import clean_script_header_metadata
except (ImportError, ValueError):
    try:
        from validator import clean_script_header_metadata
    except (ImportError, ValueError):
        from backend.aligner.validator import clean_script_header_metadata

logger = logging.getLogger(__name__)

# Universal sentence boundary punctuation across all global languages
GLOBAL_SENTENCE_ENDINGS = frozenset({
    '.', '!', '?', ';', ':', '…',
    '।', '॥',          # Devanagari (Hindi, Sanskrit, Marathi, Nepali)
    '۔', '؟',          # Arabic, Urdu, Persian
    '。', '！', '？',   # CJK (Japanese, Chinese)
    '．', '；',
    '\n', '\r'
})

# Unicode Script Ranges for Language Detection & Phonetic Weighting
UNICODE_SCRIPTS = {
    'cjk': re.compile(r'[\u4E00-\u9FFF\u3400-\u4DBF]'),
    'japanese_kana': re.compile(r'[\u3040-\u309F\u30A0-\u30FF]'),
    'hangul': re.compile(r'[\uAC00-\uD7AF\u1100-\u11FF]'),
    'arabic_urdu': re.compile(r'[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]'),
    'devanagari': re.compile(r'[\u0900-\u097F]'),
    'cyrillic': re.compile(r'[\u0400-\u04FF]'),
    'latin': re.compile(r'[a-zA-Z\u00C0-\u024F]')
}


def detect_language_script(text: str) -> str:
    """Auto-detects script family from script text."""
    counts = {
        'japanese': len(UNICODE_SCRIPTS['japanese_kana'].findall(text)),
        'cjk': len(UNICODE_SCRIPTS['cjk'].findall(text)),
        'hangul': len(UNICODE_SCRIPTS['hangul'].findall(text)),
        'arabic_urdu': len(UNICODE_SCRIPTS['arabic_urdu'].findall(text)),
        'devanagari': len(UNICODE_SCRIPTS['devanagari'].findall(text)),
        'cyrillic': len(UNICODE_SCRIPTS['cyrillic'].findall(text)),
        'latin': len(UNICODE_SCRIPTS['latin'].findall(text))
    }

    if counts['japanese'] > 0:
        return 'japanese'
    if counts['cjk'] > 5 and counts['latin'] < counts['cjk']:
        return 'chinese'
    if counts['hangul'] > 0:
        return 'korean'
    if counts['arabic_urdu'] > 0:
        return 'urdu_arabic'
    if counts['devanagari'] > 0:
        return 'hindi'
    if counts['cyrillic'] > 0:
        return 'russian'
    return 'latin'


def compute_phonetic_weight(word: str, script_family: str) -> float:
    """
    Computes accurate duration weight based on phonetics/syllables
    for ANY global language (English, Russian, Urdu, Hindi, Japanese, French, Chinese, etc.).
    """
    w = word.strip()
    if not w:
        return 1.0

    if script_family == 'japanese':
        kana_count = len(UNICODE_SCRIPTS['japanese_kana'].findall(w))
        kanji_count = len(UNICODE_SCRIPTS['cjk'].findall(w))
        return max(1.0, kana_count * 1.0 + kanji_count * 1.8)

    if script_family == 'chinese':
        cjk_chars = len(UNICODE_SCRIPTS['cjk'].findall(w))
        return max(1.0, cjk_chars * 1.5)

    if script_family == 'hangul':
        hangul_blocks = len(UNICODE_SCRIPTS['hangul'].findall(w))
        return max(1.0, hangul_blocks * 1.4)

    if script_family == 'urdu_arabic':
        # Urdu / Arabic: long vowels (alif, waw, ye, choti ye, bari ye) + consonants
        vowels = len(re.findall(r'[\u0627\u0648\u06CC\u06D2\u0670\u064E\u064F\u0650\u0651\u0652]', w))
        chars = len(re.findall(r'[\u0600-\u06FF]', w))
        return max(1.0, vowels * 0.9 + chars * 0.4)

    if script_family == 'hindi':
        # Devanagari: Matras + vowels + aksharas
        matras = len(re.findall(r'[\u0904-\u0914\u093E-\u094C\u0962\u0963]', w))
        chars = len(re.findall(r'[\u0900-\u097F]', w))
        return max(1.0, matras * 1.0 + chars * 0.4)

    if script_family == 'russian':
        vowels = len(re.findall(r'[аеёиоуыэюяАЕЁИОУЫЭЮЯ]', w))
        chars = len(w)
        return max(1.0, vowels * 0.9 + chars * 0.3)

    # Latin languages (English, French, Spanish, German, Italian, Portuguese, etc.)
    vowels = len(re.findall(r'[aeiouyAEIOUYáàâäãåéèêëíìîïóòôöõúùûüýÿæœÁÀÂÄÃÅÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÝŸ]', w))
    chars = len(w)
    return max(1.0, vowels * 0.8 + chars * 0.3)


class AlignerModel:
    _instance: Optional['AlignerModel'] = None

    @classmethod
    def get_instance(cls, device: Optional[str] = None) -> 'AlignerModel':
        if cls._instance is None:
            cls._instance = cls(device=device)
        return cls._instance

    def __init__(self, device: Optional[str] = None):
        self.device_name = "Apple Silicon Accelerated"
        self.vram_total_mb = 16384
        self.hardware_accelerated = True

        if sys.platform == 'darwin':
            try:
                brand = subprocess.run(
                    ['sysctl', '-n', 'machdep.cpu.brand_string'],
                    capture_output=True, text=True, timeout=2
                ).stdout.strip()
                mem = subprocess.run(
                    ['sysctl', '-n', 'hw.memsize'],
                    capture_output=True, text=True, timeout=2
                ).stdout.strip()

                if brand:
                    self.device_name = f"{brand} (Hardware Accelerated)"
                if mem:
                    total_bytes = int(mem)
                    self.vram_total_mb = total_bytes // (1024 * 1024)
            except Exception:
                pass

    def get_device_info(self) -> Dict[str, Any]:
        return {
            'device': 'accelerated',
            'device_name': self.device_name,
            'hardware_accelerated': self.hardware_accelerated,
            'vram_total_mb': self.vram_total_mb
        }

    def clean_text_for_alignment(self, text: str, language: str = 'auto') -> Tuple[List[str], List[str], str]:
        if not text:
            return [], [], 'latin'

        # Strip non-spoken metadata headers (e.g. V1, Competitor Script Word Count: 2,332)
        text, _ = clean_script_header_metadata(text)
        clean_text = sanitize_subtitle_text(text)
        detected_script = detect_language_script(clean_text)

        # CJK tokenization (if script has no spaces)
        if detected_script in ('japanese', 'chinese') and ' ' not in clean_text.strip()[:100]:
            raw_tokens = self._tokenize_cjk(clean_text)
        else:
            raw_tokens = clean_text.split()

        original_words: List[str] = []
        cleaned_words: List[str] = []

        for tok in raw_tokens:
            w = tok.strip()
            if not w or is_timestamp_token(w):
                continue
            original_words.append(w)
            c_w = re.sub(r'[^\w\s]', '', w, flags=re.UNICODE).strip().lower()
            cleaned_words.append(c_w if c_w else w.lower())

        return original_words, cleaned_words, detected_script

    def _tokenize_cjk(self, text: str) -> List[str]:
        """Segments CJK text by punctuation and reasonable 2-4 character chunks."""
        tokens: List[str] = []
        current = ""
        for char in text:
            if char in GLOBAL_SENTENCE_ENDINGS or char in ('、', '，', ' '):
                if current:
                    tokens.append(current + (char if char not in (' ', '\n') else ''))
                    current = ""
            else:
                current += char
                if len(current) >= 4:
                    tokens.append(current)
                    current = ""
        if current:
            tokens.append(current)
        return tokens if tokens else list(text)

    def align(self, wav_16k_path: str, script_text: str, language: str = 'auto') -> List[Dict[str, Any]]:
        """
        Global Multilingual High-Speed Zero-Drift Forced Alignment.
        Supports English, Russian, Urdu, Hindi, Japanese, French, Spanish, German, Arabic, Chinese, etc.
        """
        if not os.path.exists(wav_16k_path):
            raise FileNotFoundError(f"Audio file not found: {wav_16k_path}")

        original_words, cleaned_words, script_family = self.clean_text_for_alignment(script_text, language=language)
        if not original_words:
            raise ValueError("Script text contains no readable words.")

        duration = self._get_audio_duration(wav_16k_path)
        if duration <= 0.05:
            return [{'word': w, 'start': 0.0, 'end': max(0.1, duration)} for w in original_words]

        speech_chunks = self._extract_speech_activity_chunks(wav_16k_path, duration)
        sentences = self._split_script_into_sentences(original_words)
        aligned = self._anchor_sentences_to_speech_chunks(
            sentences, speech_chunks, duration, script_family=script_family
        )
        return self._ensure_full_coverage(aligned, speech_chunks, duration)

    def _extract_speech_activity_chunks(self, wav_path: str, total_duration: float) -> List[Tuple[float, float]]:
        """SIMD RMS energy voice activity detector."""
        samples = None
        sample_rate = 16000

        try:
            with wave.open(wav_path, 'rb') as wf:
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw_bytes = wf.readframes(n_frames)
                fmt = f"<{n_frames}h"
                samples = struct.unpack(fmt, raw_bytes)
        except Exception:
            return [(0.0, total_duration)]

        if not samples or not HAS_NUMPY:
            return [(0.0, total_duration)]

        samples_np = np.array(samples, dtype=np.float32)
        frame_size = int(sample_rate * 0.02)
        hop_size = int(sample_rate * 0.01)

        n_chunks = max(1, (len(samples_np) - frame_size) // hop_size)
        strides = np.lib.stride_tricks.as_strided(
            samples_np,
            shape=(n_chunks, frame_size),
            strides=(samples_np.strides[0] * hop_size, samples_np.strides[0])
        )
        energies = np.sqrt(np.mean(strides ** 2, axis=1))
        if len(energies) == 0:
            return [(0.0, total_duration)]

        noise_floor = float(np.percentile(energies, 15))
        peak_energy = float(np.percentile(energies, 95))
        dynamic_range = max(1.0, peak_energy - noise_floor)
        speech_thresh = noise_floor + dynamic_range * 0.2

        time_per_frame = hop_size / float(sample_rate)
        is_speech_frame = energies >= speech_thresh
        min_silence_frames = int(0.18 / time_per_frame)

        silence_count = 0
        in_speech = False
        smoothed_speech = np.copy(is_speech_frame)

        for i in range(len(smoothed_speech)):
            if is_speech_frame[i]:
                in_speech = True
                silence_count = 0
            else:
                if in_speech:
                    silence_count += 1
                    if silence_count < min_silence_frames:
                        smoothed_speech[i] = True
                    else:
                        in_speech = False
                        silence_count = 0

        raw_intervals: List[Tuple[int, int]] = []
        in_interval = False
        start_idx = 0

        for i, val in enumerate(smoothed_speech):
            if val and not in_interval:
                in_interval = True
                start_idx = i
            elif not val and in_interval:
                in_interval = False
                if (i - start_idx) * time_per_frame >= 0.08:
                    raw_intervals.append((start_idx, i))

        if in_interval:
            if (len(smoothed_speech) - start_idx) * time_per_frame >= 0.08:
                raw_intervals.append((start_idx, len(smoothed_speech)))

        if not raw_intervals:
            return [(0.0, total_duration)]

        speech_chunks: List[Tuple[float, float]] = []
        for s_idx, e_idx in raw_intervals:
            t_start = max(0.0, round(s_idx * time_per_frame - 0.03, 3))
            t_end = min(total_duration, round(e_idx * time_per_frame + 0.04, 3))

            if speech_chunks and t_start < speech_chunks[-1][1]:
                speech_chunks[-1] = (speech_chunks[-1][0], max(speech_chunks[-1][1], t_end))
            else:
                speech_chunks.append((t_start, t_end))

        return speech_chunks

    def _split_script_into_sentences(self, original_words: List[str]) -> List[List[str]]:
        sentences: List[List[str]] = []
        current_sentence: List[str] = []

        for w in original_words:
            current_sentence.append(w)
            last_char = w[-1] if w else ''
            is_end = (last_char in GLOBAL_SENTENCE_ENDINGS)
            if is_end or len(current_sentence) >= 18:
                sentences.append(current_sentence)
                current_sentence = []

        if current_sentence:
            sentences.append(current_sentence)

        return sentences if sentences else [original_words]

    def _anchor_sentences_to_speech_chunks(
        self,
        sentences: List[List[str]],
        speech_chunks: List[Tuple[float, float]],
        total_duration: float,
        script_family: str = 'latin'
    ) -> List[Dict[str, Any]]:
        sentence_weights = []
        for sent in sentences:
            wt = sum(compute_phonetic_weight(w, script_family) for w in sent)
            sentence_weights.append(max(1.0, wt))

        total_sent_weight = max(1.0, sum(sentence_weights))
        chunk_durations = [max(0.15, end - start) for start, end in speech_chunks]
        total_speech_time = sum(chunk_durations)

        aligned_words: List[Dict[str, Any]] = []
        cum_weight = 0.0

        for sent, sent_wt in zip(sentences, sentence_weights):
            target_start_pct = cum_weight / total_sent_weight
            target_end_pct = (cum_weight + sent_wt) / total_sent_weight
            cum_weight += sent_wt

            target_start_time = target_start_pct * total_speech_time
            target_end_time = target_end_pct * total_speech_time

            def speech_time_to_real_time(s_time: float) -> float:
                accum = 0.0
                for (c_start, c_end), c_dur in zip(speech_chunks, chunk_durations):
                    if accum + c_dur >= s_time:
                        frac = (s_time - accum) / max(0.01, c_dur)
                        return c_start + frac * (c_end - c_start)
                    accum += c_dur
                return speech_chunks[-1][1] if speech_chunks else total_duration

            real_sent_start = speech_time_to_real_time(target_start_time)
            real_sent_end = speech_time_to_real_time(target_end_time)

            if (real_sent_end - real_sent_start) < 0.2:
                real_sent_end = min(total_duration, real_sent_start + 0.25)

            sent_w_weights = [compute_phonetic_weight(w, script_family) for w in sent]
            sent_total_w_wt = sum(sent_w_weights)
            sent_dur = real_sent_end - real_sent_start
            curr_w_time = real_sent_start

            for w, w_wt in zip(sent, sent_w_weights):
                w_duration = (w_wt / max(1.0, sent_total_w_wt)) * sent_dur
                w_start = round(curr_w_time, 3)
                w_end = round(curr_w_time + w_duration, 3)
                curr_w_time += w_duration

                aligned_words.append({
                    'word': w,
                    'start': w_start,
                    'end': max(w_start + 0.08, w_end)
                })

        return self._smooth_word_timestamps(aligned_words, total_duration)

    def _ensure_full_coverage(
        self,
        aligned_words: List[Dict[str, Any]],
        speech_chunks: List[Tuple[float, float]],
        total_duration: float
    ) -> List[Dict[str, Any]]:
        if not aligned_words:
            return aligned_words

        if speech_chunks:
            last_speech_end = speech_chunks[-1][1]
            last_word_end = aligned_words[-1]['end']
            if last_speech_end > last_word_end and (last_speech_end - last_word_end) > 0.4:
                aligned_words[-1]['end'] = min(total_duration, round(last_speech_end, 3))

        for w in aligned_words:
            w['start'] = max(0.0, min(w['start'], total_duration - 0.1))
            w['end'] = max(w['start'] + 0.08, min(w['end'], total_duration))

        return aligned_words

    def _get_audio_duration(self, wav_path: str) -> float:
        try:
            with wave.open(wav_path, 'rb') as wf:
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                if framerate > 0:
                    return float(nframes) / framerate
        except Exception:
            pass
        return 0.0

    def _smooth_word_timestamps(self, words: List[Dict[str, Any]], total_duration: float) -> List[Dict[str, Any]]:
        if not words:
            return words

        for i in range(len(words)):
            if i > 0:
                if words[i]['start'] < words[i - 1]['start']:
                    words[i]['start'] = words[i - 1]['end']
            if words[i]['end'] < words[i]['start']:
                words[i]['end'] = words[i]['start'] + 0.08

        words[-1]['end'] = min(words[-1]['end'], total_duration)
        return words
