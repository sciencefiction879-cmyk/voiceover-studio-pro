"""
Voiceover Studio Pro - Error Detection, Diagnostics & Solution System
Provides structured, non-vague diagnostics for all stages of audio processing and forced alignment.

Format per error:
- What failed (Category)
- Which audio/file is affected
- Why it likely failed (Reason)
- What the user can do to fix it (Solution)
- Can Retry flag
"""

import os
from typing import Dict, Any, Optional

ERROR_CATEGORIES = {
    "AUDIO": "Audio Error",
    "SCRIPT": "Script Error",
    "ALIGNMENT": "Alignment Error",
    "CUT_TIMELINE": "Cut/Timeline Error",
    "OUTPUT": "Output Error",
    "SRT": "SRT Error",
    "GENERAL": "Processing Error"
}

STATUS_TYPES = {
    "SUCCESS": "SUCCESS ✓",
    "WARNING": "WARNING ⚠",
    "PARTIAL": "PARTIAL ⚠",
    "ERROR": "ERROR ✕"
}


def create_structured_error(
    category: str,
    target_file: str,
    reason: str,
    solution: str,
    can_retry: bool = True,
    raw_error: Optional[str] = None
) -> Dict[str, Any]:
    """
    Construct a standardized error dictionary adhering strictly to the
    'What failed, which file, why, solution, retry' pattern.
    """
    cat_label = ERROR_CATEGORIES.get(category.upper(), category)
    formatted_msg = (
        f"{cat_label} — {target_file}\n"
        f"{reason}\n"
        f"Possible solution: {solution}"
    )

    return {
        "status": "ERROR ✕",
        "category": cat_label,
        "targetFile": target_file,
        "reason": reason,
        "solution": solution,
        "canRetry": can_retry,
        "formatted": formatted_msg,
        "rawError": str(raw_error) if raw_error else None
    }


def diagnose_audio_error(file_path: str, file_name: str, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose errors when opening, probing, or reading an audio file."""
    raw_str = str(exception) if exception else ""
    
    if not os.path.exists(file_path):
        return create_structured_error(
            category="AUDIO",
            target_file=file_name,
            reason=f"{file_name} could not be found on disk.",
            solution=f"Re-upload {file_name} or verify the folder path, then retry.",
            raw_error=raw_str
        )
    
    if "Invalid data found" in raw_str or "unsupported codec" in raw_str.lower() or "moov atom not found" in raw_str.lower():
        return create_structured_error(
            category="AUDIO",
            target_file=file_name,
            reason=f"{file_name} contains corrupted data or an unsupported audio container.",
            solution=f"Re-export {file_name} as standard MP3 or WAV (44.1kHz / 48kHz stereo) and retry.",
            raw_error=raw_str
        )

    return create_structured_error(
        category="AUDIO",
        target_file=file_name,
        reason=f"{file_name} could not be opened.",
        solution=f"Re-upload {file_name} or use a supported audio format (.mp3, .wav, .m4a, .aac, .flac).",
        raw_error=raw_str
    )


def diagnose_script_error(audio_name: str, script_name: Optional[str] = None, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose missing or unreadable script files."""
    raw_str = str(exception) if exception else ""
    base_name = os.path.splitext(audio_name)[0]
    
    if not script_name:
        return create_structured_error(
            category="SCRIPT",
            target_file=f"{base_name} script",
            reason=f"{base_name} script is missing.",
            solution=f"Upload the matching script ({base_name}.txt, {base_name}.docx, or {base_name}.srt) before starting alignment.",
            raw_error=raw_str
        )
    
    return create_structured_error(
        category="SCRIPT",
        target_file=script_name,
        reason=f"Script file {script_name} could not be parsed or contains no readable dialogue text.",
        solution=f"Check that {script_name} contains readable text and is saved in UTF-8 encoding (or valid Word .docx), then retry.",
        raw_error=raw_str
    )


def diagnose_alignment_error(audio_name: str, script_name: str, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose acoustic forced alignment failures."""
    raw_str = str(exception) if exception else ""
    base_name = os.path.splitext(audio_name)[0]

    if "silence" in raw_str.lower() or "no speech" in raw_str.lower():
        return create_structured_error(
            category="ALIGNMENT",
            target_file=f"{base_name} Forced Alignment",
            reason=f"No speech was detected in {audio_name}.",
            solution=f"Ensure {audio_name} is not completely silent or muted, verify microphone levels, and retry.",
            raw_error=raw_str
        )

    return create_structured_error(
        category="ALIGNMENT",
        target_file=f"{base_name} Forced Alignment",
        reason=f"The script could not be aligned with {audio_name}.",
        solution=f"Check that the script belongs to {audio_name}, the audio is readable, and the script language matches the spoken language. Then retry alignment.",
        raw_error=raw_str
    )


def diagnose_cut_error(audio_name: str, cut_details: Optional[str] = None, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose cut recalculation and caption boundary errors."""
    raw_str = str(exception) if exception else ""
    base_name = os.path.splitext(audio_name)[0]

    return create_structured_error(
        category="CUT_TIMELINE",
        target_file=f"{base_name} Timeline",
        reason=f"Caption timing could not be recalculated after the selected cut for {audio_name}.",
        solution="Check the cut boundaries and retry processing.",
        raw_error=raw_str
    )


def diagnose_output_error(audio_name: str, output_path: str, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose missing or unverified output files."""
    raw_str = str(exception) if exception else ""
    base_name = os.path.splitext(audio_name)[0]
    out_name = os.path.basename(output_path) if output_path else f"{base_name}.mp3"

    return create_structured_error(
        category="OUTPUT",
        target_file=out_name,
        reason=f"{out_name} was processed, but the output file could not be verified.",
        solution=f"Retry processing {base_name}.",
        raw_error=raw_str
    )


def diagnose_srt_error(srt_name: str, reason_detail: Optional[str] = None, exception: Optional[Exception] = None) -> Dict[str, Any]:
    """Diagnose corrupted or timestamp-inconsistent SRT files."""
    raw_str = str(exception) if exception else ""

    return create_structured_error(
        category="SRT",
        target_file=srt_name,
        reason=reason_detail or f"{srt_name} contains invalid timestamps.",
        solution="Regenerate the SRT after successful alignment.",
        raw_error=raw_str
    )
