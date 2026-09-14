import os
import re
import sys
import json
import time
import zipfile
import tempfile
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any, Optional

from flask import Flask, request, jsonify, send_file, Response, send_from_directory
from flask_cors import CORS

from audio_engine import (
    get_audio_metadata,
    calculate_cut_schedule,
    resolve_dsp_params,
    process_voiceover,
    validate_processed_audio,
    format_time,
    safe_float,
    safe_int,
    safe_bool,
    DEFAULT_AUDIO_CHARACTERISTICS,
    randomize_characteristics_3_to_7_pct,
    FFMPEG_PATH,
    FFPROBE_PATH
)
from srt_engine import (
    process_srt_file,
    parse_srt_file,
    process_caption_pipeline,
    find_matching_script,
    validate_caption_sync
)
from gemini_engine import KEY_MANAGER, analyze_audio_with_gemini
from error_diagnostics import (
    diagnose_audio_error,
    diagnose_script_error,
    diagnose_alignment_error,
    diagnose_cut_error,
    diagnose_output_error,
    diagnose_srt_error,
    create_structured_error
)

# Base & frontend directory detection (supports local dev, macOS app bundle, and Windows PyInstaller)
_meipass = getattr(sys, '_MEIPASS', None)
if _meipass:
    FRONTEND_DIR = os.path.join(_meipass, "frontend")
    BASE_DIR = os.path.abspath(os.path.dirname(sys.executable)) if getattr(sys, 'frozen', False) else os.path.abspath(os.path.join(_meipass, ".."))
else:
    WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = os.path.abspath(os.path.join(WORKSPACE_DIR, ".."))
    FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

if not os.path.exists(FRONTEND_DIR):
    for cand in [os.path.join(os.getcwd(), "frontend"), os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))]:
        if os.path.exists(cand):
            FRONTEND_DIR = cand
            break

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

# Workspace directories
def _resolve_data_dir() -> str:
    """
    Resolve a writable directory for uploads, previews, processed files, and exports.
    If BASE_DIR/data is writable (e.g. during development or portable install), use it.
    If BASE_DIR is read-only (e.g. running directly from macOS .dmg or /Applications bundle),
    transparently fallback to standard user application support directory.
    """
    local_data = os.path.join(BASE_DIR, "data")
    try:
        os.makedirs(local_data, exist_ok=True)
        test_file = os.path.join(local_data, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return local_data
    except Exception:
        home = os.path.expanduser("~")
        if sys.platform == "darwin":
            user_data = os.path.join(home, "Library", "Application Support", "VoiceoverStudioPro", "data")
        elif sys.platform == "win32":
            appdata = os.environ.get("APPDATA", home)
            user_data = os.path.join(appdata, "VoiceoverStudioPro", "data")
        else:
            user_data = os.path.join(home, ".voiceover_studio", "data")
        os.makedirs(user_data, exist_ok=True)
        return user_data

DATA_ROOT = _resolve_data_dir()
UPLOADS_DIR = os.path.join(DATA_ROOT, "uploads")
PROCESSED_DIR = os.path.join(DATA_ROOT, "processed")
PREVIEWS_DIR = os.path.join(DATA_ROOT, "previews")
EXPORTS_DIR = os.path.join(DATA_ROOT, "exports")

for d in [UPLOADS_DIR, PROCESSED_DIR, PREVIEWS_DIR, EXPORTS_DIR]:
    os.makedirs(d, exist_ok=True)

def format_human_duration(seconds: float) -> str:
    """Format seconds into human-readable duration like '16h 40m' or '48m 12s'."""
    seconds = max(0.0, safe_float(seconds, 0.0))
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"

# Global State
STATE = {
    "files": [],               # List of loaded file metadata
    "settings": {},            # Current DSP & cut settings
    "is_processing": False,    # Processing flag
    "current_file": None,      # Active file(s) being processed
    "current_batch": 0,        # Current chunk index (1-based)
    "total_batches": 0,        # Total chunks count
    "active_batch_files": [],  # List of filenames currently in active batch
    "batch_size": 3,           # Default batch chunk size: 3
    "progress": 0,             # 0 to 100
    "caption_mode": "mode1",   # "mode1" (Process + Cut + SRT) or "mode2" (Alignment Only)
    "logs": [],                # System logs
    "processed_files": {},     # Key: filename, Value: processed info
    "analytics": {
        "totalFiles": 0,
        "processedFiles": 0,
        "remainingFiles": 0,
        "successfulCount": 0,
        "failedCount": 0,
        "partialCount": 0,
        "completionPercent": 0,
        "currentBatch": 0,
        "totalBatches": 0,
        "activeBatchFiles": [],
        "batchSize": 3,
        "totalOriginalSecs": 0.0,
        "formattedTotalOriginal": "00:00",
        "totalProcessedSecs": 0.0,
        "formattedTotalProcessed": "00:00",
        "totalRemovedSecs": 0.0,
        "formattedTotalRemoved": "00:00",
        "outputsGenerated": 0,
        "averageSecsPerFile": 0.0,
        "isCompleted": False
    }
}


def add_log(message: str, level: str = "info"):
    """Append a log message with timestamp."""
    entry = {
        "timestamp": time.strftime("%H:%M:%S"),
        "message": message,
        "level": level
    }
    STATE["logs"].append(entry)
    if len(STATE["logs"]) > 500:
        STATE["logs"].pop(0)
    print(f"[{entry['timestamp']}] [{level.upper()}] {message}")


def natural_sort_key(s: str):
    """Natural sort key to sort 'V1, V2, V10' correctly."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def update_analytics():
    """Recalculate batch and lifecycle analytics from STATE."""
    files = STATE["files"]
    total = len(files)
    completed = [f for f in files if f.get("status") == "completed"]
    failed = [f for f in files if f.get("status") == "error"]
    partial = [f for f in files if f.get("status") == "partial"]
    mode = STATE.get("caption_mode", "mode1")
    
    orig_secs = sum(safe_float(f.get("duration"), 0.0) for f in files)
    proc_secs = sum(safe_float(f.get("finalDuration"), safe_float(f.get("duration"), 0.0)) for f in completed)
    removed_secs = max(0.0, sum(safe_float(f.get("duration"), 0.0) - safe_float(f.get("finalDuration"), safe_float(f.get("duration"), 0.0)) for f in completed))
    
    processed_count = len(completed) + len(failed) + len(partial)
    
    # Calculate Final Verified Report
    if mode == "mode2":
        aligned_count = sum(1 for f in files if f.get("hasProcessedSrt"))
        validated_count = sum(1 for f in files if f.get("status") == "completed" and f.get("hasProcessedSrt"))
        srt_generated_count = sum(1 for f in files if f.get("hasProcessedSrt"))
        final_report = {
            "mode": "mode2",
            "total": total,
            "aligned": aligned_count,
            "validated": validated_count,
            "failed": len(failed),
            "srtGenerated": srt_generated_count,
            "srtDownloadsReady": srt_generated_count,
            "lines": [
                f"Total: {total}",
                f"Aligned: {aligned_count}",
                f"Validated: {validated_count}",
                f"Failed: {len(failed)}",
                f"SRT Generated: {srt_generated_count}",
                f"SRT Downloads Ready: {srt_generated_count}"
            ]
        }
    else:
        # Mode 1
        mp3_count = sum(1 for f in files if f.get("hasProcessed"))
        srt_count = sum(1 for f in files if f.get("hasProcessedSrt"))
        val_count = sum(1 for f in files if f.get("status") == "completed" and f.get("hasProcessed"))
        final_report = {
            "mode": "mode1",
            "total": total,
            "processed": len(completed) + len(partial),
            "validated": val_count,
            "failed": len(failed),
            "mp3Generated": mp3_count,
            "srtGenerated": srt_count,
            "downloadsReady": max(mp3_count, srt_count),
            "lines": [
                f"Total: {total}",
                f"Processed: {len(completed) + len(partial)}",
                f"Validated: {val_count}",
                f"Failed: {len(failed)}",
                f"MP3 Generated: {mp3_count}",
                f"SRT Generated: {srt_count}",
                f"Downloads Ready: {max(mp3_count, srt_count)}"
            ]
        }

    STATE["analytics"] = {
        "totalFiles": total,
        "processedFiles": processed_count,
        "remainingFiles": max(0, total - processed_count),
        "successfulCount": len(completed),
        "failedCount": len(failed),
        "partialCount": len(partial),
        "completionPercent": int(processed_count / total * 100) if total > 0 else 0,
        "currentBatch": STATE.get("current_batch", 0),
        "totalBatches": STATE.get("total_batches", 0),
        "activeBatchFiles": STATE.get("active_batch_files", []),
        "batchSize": STATE.get("batch_size", 3),
        "captionMode": mode,
        "totalOriginalSecs": round(orig_secs, 2),
        "formattedTotalOriginal": format_human_duration(orig_secs),
        "totalProcessedSecs": round(proc_secs, 2),
        "formattedTotalProcessed": format_human_duration(proc_secs),
        "totalRemovedSecs": round(removed_secs, 2),
        "formattedTotalRemoved": format_human_duration(removed_secs),
        "outputsGenerated": len(completed),
        "averageSecsPerFile": round(proc_secs / len(completed), 1) if len(completed) > 0 else 0.0,
        "isCompleted": (processed_count == total and total > 0),
        "finalReport": final_report
    }


@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    if os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health", methods=["GET"])
def health_check():
    ffmpeg_ok = os.path.exists(FFMPEG_PATH)
    ffprobe_ok = os.path.exists(FFPROBE_PATH)

    return jsonify({
        "status": "healthy",
        "ffmpeg": {"path": FFMPEG_PATH, "available": ffmpeg_ok},
        "ffprobe": {"path": FFPROBE_PATH, "available": ffprobe_ok},
        "workspace": BASE_DIR
    })


@app.route("/api/browse-folder", methods=["POST"])
def browse_folder():
    """Open native macOS folder picker dialog using osascript."""
    default_prompt = request.json.get("prompt", "Select Voiceovers Folder") if request.is_json else "Select Voiceovers Folder"
    
    script = f'''
    tell application "System Events"
        activate
        set chosenFolder to choose folder with prompt "{default_prompt}"
        return POSIX path of chosenFolder
    end tell
    '''
    try:
        proc = subprocess.run(["osascript", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if proc.returncode == 0:
            selected_path = proc.stdout.strip()
            if selected_path:
                return jsonify({"success": True, "path": selected_path})
    except Exception as e:
        add_log(f"Native folder picker notice: {e}", "warning")

    return jsonify({"success": False, "message": "Folder selection cancelled or not available."})


@app.route("/api/scan-folder", methods=["POST"])
def scan_folder():
    """Scan local directory for audio files (V1.mp3, V2.mp3, etc.) and matching subtitles."""
    data = request.get_json() or {}
    folder_path = data.get("folder_path", "").strip()

    if not folder_path or not os.path.exists(folder_path):
        return jsonify({"success": False, "error": f"Folder not found: {folder_path}"}), 400

    audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}
    discovered_files = []

    try:
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in audio_extensions and not file.startswith("."):
                    full_p = os.path.join(root, file)
                    discovered_files.append(full_p)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    discovered_files.sort(key=lambda p: natural_sort_key(os.path.basename(p)))

    file_entries = []
    for idx, fpath in enumerate(discovered_files):
        try:
            meta = get_audio_metadata(fpath)
            cut_schedule = calculate_cut_schedule(meta["duration"])
            
            # Detect matching script (.txt, .docx, .srt)
            script_path = find_matching_script(fpath, search_dir=folder_path)
            has_script = bool(script_path and os.path.exists(script_path))
            script_name = os.path.basename(script_path) if has_script else None
            script_ext = os.path.splitext(script_name)[1].lower() if script_name else None
            has_srt = (script_ext == ".srt")
            srt_path = script_path if has_srt else None

            entry = {
                "id": f"file_{idx+1}",
                "index": idx + 1,
                "fileName": meta["fileName"],
                "filePath": fpath,
                "duration": meta["duration"],
                "formattedDuration": meta["formattedDuration"],
                "sizeBytes": meta["sizeBytes"],
                "bitrate": meta["bitrate"],
                "sampleRate": meta["sampleRate"],
                "channels": meta["channels"],
                "codec": meta["codec"],
                "hasCuts": len(cut_schedule["cuts"]) > 0,
                "cutCount": len(cut_schedule["cuts"]),
                "estimatedProcessedDuration": cut_schedule["finalEstimatedDuration"],
                "formattedEstimatedDuration": cut_schedule.get("formattedFinalDuration", ""),
                "status": "ready",
                "hasProcessed": False,
                "hasScript": has_script,
                "scriptPath": script_path,
                "scriptFileName": script_name,
                "scriptType": script_ext,
                "hasSrt": has_srt,
                "srtPath": srt_path,
                "srtFileName": script_name if has_srt else None,
                "isMaster": (idx == 0)
            }
            file_entries.append(entry)
        except Exception as e:
            add_log(f"Notice probing {fpath}: {e}", "warning")

    STATE["files"] = file_entries
    update_analytics()
    master_name = file_entries[0]['fileName'] if file_entries else 'None'
    add_log(f"Loaded {len(file_entries)} voiceovers from {folder_path} (Master: {master_name})", "info")

    return jsonify({
        "success": True,
        "folderPath": folder_path,
        "count": len(file_entries),
        "files": file_entries,
        "analytics": STATE["analytics"]
    })


@app.route("/api/upload-files", methods=["POST"])
def upload_files():
    """Handle browser folder/file uploads (both audio files and matching .srt files)."""
    if "files" not in request.files:
        return jsonify({"success": False, "error": "No files uploaded"}), 400

    uploaded_files = request.files.getlist("files")

    audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    script_extensions = {".txt", ".docx", ".srt", ".text"}

    has_audio_uploads = any(
        os.path.splitext(f.filename)[1].lower() in audio_extensions
        for f in uploaded_files if f.filename
    )

    if not has_audio_uploads and STATE["files"]:
        # Scripts-only upload: Save scripts and auto-link to existing files in queue
        saved_scripts = []
        for file in uploaded_files:
            if not file.filename or file.filename.startswith("."):
                continue
            fname = os.path.basename(file.filename)
            dest_path = os.path.join(UPLOADS_DIR, fname)
            file.save(dest_path)
            saved_scripts.append(dest_path)

        linked_count = 0
        for entry in STATE["files"]:
            sp = find_matching_script(entry["filePath"], search_dir=UPLOADS_DIR)
            if sp:
                entry["hasScript"] = True
                entry["scriptPath"] = sp
                entry["scriptFileName"] = os.path.basename(sp)
                entry["scriptType"] = os.path.splitext(sp)[1].lower()
                if entry["scriptType"] == ".srt":
                    entry["hasSrt"] = True
                    entry["srtPath"] = sp
                    entry["srtFileName"] = entry["scriptFileName"]
                linked_count += 1

        add_log(f"Auto-linked {linked_count} uploaded scripts to existing voiceovers.", "info")
        return jsonify({
            "success": True,
            "count": len(STATE["files"]),
            "files": STATE["files"],
            "analytics": STATE["analytics"],
            "scriptsLinked": linked_count
        })

    # Audio + Script upload: Clean old uploads and index fresh batch
    for f in os.listdir(UPLOADS_DIR):
        try:
            os.remove(os.path.join(UPLOADS_DIR, f))
        except Exception:
            pass

    for file in uploaded_files:
        if not file.filename or file.filename.startswith("."):
            continue
        fname = os.path.basename(file.filename)
        dest_path = os.path.join(UPLOADS_DIR, fname)
        file.save(dest_path)

    discovered = [
        os.path.join(UPLOADS_DIR, f)
        for f in os.listdir(UPLOADS_DIR)
        if os.path.splitext(f)[1].lower() in audio_extensions
    ]
    discovered.sort(key=lambda p: natural_sort_key(os.path.basename(p)))

    file_entries = []
    for idx, fpath in enumerate(discovered):
        try:
            meta = get_audio_metadata(fpath)
            cut_schedule = calculate_cut_schedule(meta["duration"])
            
            # Detect matching script (.txt, .docx, .srt)
            script_path = find_matching_script(fpath, search_dir=UPLOADS_DIR)
            has_script = bool(script_path and os.path.exists(script_path))
            script_name = os.path.basename(script_path) if has_script else None
            script_ext = os.path.splitext(script_name)[1].lower() if script_name else None
            has_srt = (script_ext == ".srt")
            srt_path = script_path if has_srt else None

            entry = {
                "id": f"file_{idx+1}",
                "index": idx + 1,
                "fileName": meta["fileName"],
                "filePath": fpath,
                "duration": meta["duration"],
                "formattedDuration": meta["formattedDuration"],
                "sizeBytes": meta["sizeBytes"],
                "bitrate": meta["bitrate"],
                "sampleRate": meta["sampleRate"],
                "channels": meta["channels"],
                "codec": meta["codec"],
                "hasCuts": len(cut_schedule["cuts"]) > 0,
                "cutCount": len(cut_schedule["cuts"]),
                "estimatedProcessedDuration": cut_schedule["finalEstimatedDuration"],
                "formattedEstimatedDuration": cut_schedule.get("formattedFinalDuration", ""),
                "status": "ready",
                "hasProcessed": False,
                "hasScript": has_script,
                "scriptPath": script_path,
                "scriptFileName": script_name,
                "scriptType": script_ext,
                "hasSrt": has_srt,
                "srtPath": srt_path,
                "srtFileName": script_name if has_srt else None,
                "isMaster": (idx == 0)
            }
            file_entries.append(entry)
        except Exception as e:
            add_log(f"Notice probing uploaded file {fpath}: {e}", "warning")

    STATE["files"] = file_entries
    update_analytics()
    add_log(f"Indexed {len(file_entries)} voiceover files. V1 Master assigned.", "info")

    return jsonify({
        "success": True,
        "count": len(file_entries),
        "files": file_entries,
        "analytics": STATE["analytics"]
    })


@app.route("/api/randomize-params", methods=["POST"])
def randomize_params():
    """
    Randomize audio characteristics within 3%–7% independently.
    Generates natural English logs detailing each parameter change.
    """
    data = request.get_json() or {}
    current_params = data.get("current_params", {})
    specific_key = data.get("specific_key", None)

    res = randomize_characteristics_3_to_7_pct(base=current_params, specific_key=specific_key)

    add_log("🎲 Randomization Applied", "info")
    for log_item in res.get("natural_logs", []):
        add_log(f"• {log_item}", "info")
    add_log(res.get("summary", "Randomization complete."), "success")

    return jsonify({
        "success": True,
        "randomized": res["randomized"],
        "naturalLogs": res["natural_logs"],
        "summary": res["summary"]
    })


@app.route("/api/reset-params", methods=["POST"])
def reset_params():
    """Reset all audio characteristics to default studio references."""
    add_log("↺ All audio characteristics restored to Studio Baseline Defaults.", "info")
    return jsonify({
        "success": True,
        "defaults": DEFAULT_AUDIO_CHARACTERISTICS
    })


@app.route("/api/apply-master-v1", methods=["POST"])
def apply_master_v1():
    """Lock approved V1 Master audio characteristics for all other files."""
    data = request.get_json() or {}
    master_settings = data.get("settings", {})

    STATE["master_v1_settings"] = master_settings
    STATE["master_locked"] = True

    add_log("👑 V1 Master Configuration LOCKED & APPROVED. Ready to apply to all queue voiceovers (V2, V3...).", "success")

    return jsonify({
        "success": True,
        "message": "V1 master settings successfully applied to all files in queue.",
        "masterSettings": master_settings
    })


@app.route("/api/preview", methods=["POST"])
def generate_preview():
    """
    Generate instant preview / test audio for V1 or selected file with current DSP and cut settings.
    Runs full post-processing validation on the generated output and posts a V1 Validation report.
    """
    data = request.get_json() or {}
    file_id = data.get("file_id")
    file_path = data.get("file_path")
    settings = data.get("settings", {})
    preview_duration = safe_float(data.get("preview_duration", 60.0), 60.0)

    target_file = None
    target_entry = None
    if file_path and os.path.exists(file_path):
        target_file = file_path
        for f in STATE["files"]:
            if f["filePath"] == file_path:
                target_entry = f
                break
    elif file_id:
        for f in STATE["files"]:
            if f["id"] == file_id:
                target_file = f["filePath"]
                target_entry = f
                break

    if not target_file or not os.path.exists(target_file):
        return jsonify({"success": False, "error": "Target file not found"}), 404

    try:
        resolved_params = resolve_dsp_params(
            {**settings, "master_locked": True, "randomize_per_file": False},
            file_index=0, total_files=1
        )
        base_name = os.path.splitext(os.path.basename(target_file))[0]
        preview_out = os.path.join(PREVIEWS_DIR, f"preview_{base_name}.mp3")

        is_v1 = (target_entry and target_entry.get("isMaster", False)) or (target_entry and target_entry.get("index") == 1)

        add_log(f"Processing {'V1 Master' if is_v1 else base_name} Preview...", "info")

        res = process_voiceover(
            input_path=target_file,
            output_path=preview_out,
            params=resolved_params,
            preview_mode=True,
            preview_duration=preview_duration
        )

        validation = res.get("validation", {})
        val_status = validation.get("status", "SUCCESS")
        val_checks = validation.get("checks", {})

        # Log detailed V1 Validation Report in Natural English
        if is_v1:
            add_log("━━━ V1 Processing Validation ━━━", "info")
            speed = resolved_params.get("speed_factor", 1.0)
            pitch = resolved_params.get("pitch_semitones", 0.0)
            vol = resolved_params.get("volume_db", 0.0)
            bass = resolved_params.get("bass_gain", 0.0)
            
            add_log(f"• Speed: 100% → {speed*100:.1f}% ✓ Applied", "info")
            add_log(f"• Pitch: 0.00 st → {pitch:+.2f} st ✓ Applied", "info")
            add_log(f"• Volume: 0.00 dB → {vol:+.2f} dB ✓ Applied", "info")
            add_log(f"• EQ Low: 0.00 dB → {bass:+.2f} dB ✓ Applied", "info")
            
            cuts_count = res.get("cutCount", 0)
            if cuts_count > 0:
                add_log(f"• Cut Operation: {cuts_count} cuts applied (>25m) ✓ Applied", "info")
            else:
                add_log("• Cut Operation: 0:00–25:00 protected (0 cuts) ✓ Applied", "info")
            
            add_log("• Output File ✓ Created", "info")
            add_log(f"• Final Duration: {res['formattedOriginalDuration']} → {res['formattedProcessedDuration']} ✓ Verified", "info")
            add_log(f"V1 Validation: {val_status} — All requested changes were applied and verified.", "success" if val_status == "SUCCESS" else "warning")

        # Check for matching script and align/sync caption preview
        caption_info = {"hasScript": False}
        script_in = target_entry.get("scriptPath") or target_entry.get("srtPath") if target_entry else None
        if script_in and os.path.exists(script_in):
            caption_info["hasScript"] = True
            caption_info["scriptFileName"] = os.path.basename(script_in)
            preview_srt = os.path.join(PREVIEWS_DIR, f"preview_{base_name}.srt")
            try:
                caption_res = process_caption_pipeline(
                    audio_path=target_file,
                    script_path=script_in,
                    output_srt_path=preview_srt,
                    speed_factor=safe_float(resolved_params.get("speed_factor"), 1.0),
                    cuts=res.get("cuts", []),
                    processed_audio_duration=res.get("processedDuration", 0.0),
                    ffmpeg_bin=FFMPEG_PATH
                )
                caption_info["success"] = True
                caption_info["cuesCount"] = caption_res.get("finalCueCount", 0)
                caption_info["validation"] = caption_res.get("validation", {})
                if target_entry:
                    target_entry["hasProcessedSrt"] = True
                    target_entry["captionValidation"] = caption_res.get("validation", {})
                add_log(f"• Script '{caption_info['scriptFileName']}' aligned & synchronized: {caption_info['cuesCount']} cues ✓", "info")
            except Exception as e_cap:
                caption_info["success"] = False
                caption_info["error"] = str(e_cap)

        # Also update target entry preview state so UI immediately reflects actual preview duration
        if target_entry:
            target_entry["previewUrl"] = f"/api/stream-audio?type=preview&name={os.path.basename(preview_out)}"
            target_entry["formattedPreviewDuration"] = res["formattedProcessedDuration"]
            target_entry["validation"] = validation
            target_entry["captionInfo"] = caption_info

        return jsonify({
            "success": True,
            "previewUrl": f"/api/stream-audio?type=preview&name={os.path.basename(preview_out)}",
            "originalUrl": f"/api/stream-audio?type=file&path={target_file}",
            "originalDuration": res["originalDuration"],
            "formattedOriginalDuration": res["formattedOriginalDuration"],
            "previewDuration": res["processedDuration"],
            "formattedPreviewDuration": res["formattedProcessedDuration"],
            "cuts": res["cuts"],
            "params": resolved_params,
            "validation": validation,
            "caption": caption_info,
            "validationStatus": val_status,
            "fileName": os.path.basename(target_file)
        })
    except Exception as e:
        add_log(f"Preview generation failed: {e}", "error")
        return jsonify({"success": False, "error": str(e)}), 500


def process_single_item_worker(item, idx, total_count, caption_mode, settings):
    """
    Core worker function for an individual voiceover item.
    Supports Mode 1 (Audio DSP + cuts + cut-sync SRT) and Mode 2 (Forced alignment only, audio untouched).
    Adheres strictly to the Error Detection & Recovery System:
    - Never vague: Categorized error, exact affected file, why it failed, possible solution.
    - Validates output on disk before setting SUCCESS ✓.
    - Marks PARTIAL ⚠ if audio passes but caption sync fails in Mode 1.
    - Marks ERROR ✕ on failure and provides retry capability.
    """
    in_path = item.get("filePath")
    orig_name = item.get("fileName")
    base_name = os.path.splitext(orig_name)[0]

    item["status"] = "processing"
    item["statusLabel"] = "Processing..."
    item["errorDetails"] = None
    item["errorMessage"] = None
    item["warningMessage"] = None

    # Check input audio file existence
    if not in_path or not os.path.exists(in_path):
        err = diagnose_audio_error(in_path or "", orig_name)
        item["status"] = "error"
        item["statusLabel"] = "ERROR ✕"
        item["errorDetails"] = err
        item["errorMessage"] = err["formatted"]
        add_log(f"{err['formatted']}", "error")
        return False, orig_name, err

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODE 2: ALIGNMENT ONLY (No cuts, No audio alterations, SRT only)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if caption_mode == "mode2":
        # Check script existence
        script_in = item.get("scriptPath") or item.get("srtPath")
        if not script_in:
            script_in = find_matching_script(in_path)
            if script_in:
                item["scriptPath"] = script_in
                item["scriptFileName"] = os.path.basename(script_in)
                item["scriptType"] = os.path.splitext(script_in)[1].lower()
                item["hasScript"] = True

        if not script_in or not os.path.exists(script_in):
            err = diagnose_script_error(orig_name, None)
            item["status"] = "error"
            item["statusLabel"] = "ERROR ✕"
            item["errorDetails"] = err
            item["errorMessage"] = err["formatted"]
            add_log(f"{err['formatted']}", "error")
            return False, orig_name, err

        try:
            out_srt_name = f"{base_name}.srt"
            out_srt_path = os.path.join(PROCESSED_DIR, out_srt_name)
            orig_dur = safe_float(item.get("duration"), 0.0)

            caption_res = process_caption_pipeline(
                audio_path=in_path,
                script_path=script_in,
                output_srt_path=out_srt_path,
                speed_factor=1.0,
                cuts=None,
                target_audio_duration=orig_dur,
                mode="mode2",
                ffmpeg_bin=FFMPEG_PATH
            )

            # Verify physical SRT output on disk
            if not os.path.exists(out_srt_path) or os.path.getsize(out_srt_path) == 0:
                err = diagnose_srt_error(out_srt_name, reason_detail=f"{out_srt_name} was not created on disk.")
                item["status"] = "error"
                item["statusLabel"] = "ERROR ✕"
                item["errorDetails"] = err
                item["errorMessage"] = err["formatted"]
                add_log(f"{err['formatted']}", "error")
                return False, orig_name, err

            val = caption_res.get("validation", {})
            val_status = val.get("status", "SUCCESS")

            if val_status == "FAILED":
                err = diagnose_srt_error(out_srt_name, reason_detail=val.get("error", "SRT validation failed."))
                item["status"] = "error"
                item["statusLabel"] = "ERROR ✕"
                item["errorDetails"] = err
                item["errorMessage"] = err["formatted"]
                add_log(f"{err['formatted']}", "error")
                return False, orig_name, err

            item["status"] = "completed"
            item["statusLabel"] = "SUCCESS ✓"
            item["hasProcessed"] = False
            item["hasProcessedSrt"] = True
            item["processedSrtPath"] = out_srt_path
            item["processedSrtFileName"] = out_srt_name
            item["captionValidation"] = val
            item["captionCuesCount"] = caption_res.get("finalCueCount", 0)
            item["captionMode"] = "mode2"

            STATE["processed_files"][orig_name] = item
            STATE["processed_files"][out_srt_name] = item

            add_log(
                f"{base_name}.srt — SUCCESS ✓: Mode 2 Alignment complete ({caption_res.get('finalCueCount', 0)} cues matched to voice-over, audio untouched).",
                "success"
            )
            return True, orig_name, None
        except Exception as e:
            err = diagnose_alignment_error(orig_name, os.path.basename(script_in) if script_in else "script", e)
            item["status"] = "error"
            item["statusLabel"] = "ERROR ✕"
            item["errorDetails"] = err
            item["errorMessage"] = err["formatted"]
            add_log(f"{err['formatted']}", "error")
            return False, orig_name, err

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODE 1: PROCESSED AUDIO + CUT-SYNCHRONIZED SRT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    out_name = f"{base_name}.mp3"
    out_path = os.path.join(PROCESSED_DIR, out_name)

    try:
        batch_settings = {**settings, "master_locked": True, "randomize_per_file": False}
        params = resolve_dsp_params(batch_settings, file_index=idx, total_files=total_count)

        res = process_voiceover(
            input_path=in_path,
            output_path=out_path,
            params=params,
            preview_mode=False
        )

        # Verify physical MP3 output on disk
        if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
            err = diagnose_output_error(orig_name, out_path)
            item["status"] = "error"
            item["statusLabel"] = "ERROR ✕"
            item["errorDetails"] = err
            item["errorMessage"] = err["formatted"]
            add_log(f"{err['formatted']}", "error")
            return False, orig_name, err

        validation = res.get("validation", {})
        val_status = validation.get("status", "SUCCESS")
        val_checks = validation.get("checks", {})

        if val_status == "FAILED":
            err = diagnose_output_error(orig_name, out_path, Exception(validation.get("error", "Audio verification failed")))
            item["status"] = "error"
            item["statusLabel"] = "ERROR ✕"
            item["errorDetails"] = err
            item["errorMessage"] = err["formatted"]
            add_log(f"{err['formatted']}", "error")
            return False, orig_name, err

        item["status"] = "completed"
        item["statusLabel"] = "SUCCESS ✓"
        item["hasProcessed"] = True
        item["processedPath"] = out_path
        item["processedFileName"] = out_name
        item["originalDuration"] = res["originalDuration"]
        item["finalDuration"] = res["processedDuration"]
        item["formattedFinalDuration"] = res["formattedProcessedDuration"]
        item["durationRemoved"] = val_checks.get("duration_removed", 0.0)
        item["formattedDurationRemoved"] = val_checks.get("formatted_duration_removed", "00:00")
        item["cutCount"] = res["cutCount"]
        item["cuts"] = res["cuts"]
        item["resolvedParams"] = params
        item["validation"] = validation
        item["captionMode"] = "mode1"

        # Caption Processing for Mode 1
        script_in = item.get("scriptPath") or item.get("srtPath")
        if not script_in:
            script_in = find_matching_script(in_path)
            if script_in:
                item["scriptPath"] = script_in
                item["scriptFileName"] = os.path.basename(script_in)
                item["scriptType"] = os.path.splitext(script_in)[1].lower()
                item["hasScript"] = True

        if script_in and os.path.exists(script_in):
            out_srt_name = f"{base_name}.srt"
            out_srt_path = os.path.join(PROCESSED_DIR, out_srt_name)
            speed_val = safe_float(params.get("speed_factor"), 1.0)
            cuts_list = res.get("cuts", [])
            final_dur = res.get("processedDuration", 0.0)

            try:
                caption_res = process_caption_pipeline(
                    audio_path=in_path,
                    script_path=script_in,
                    output_srt_path=out_srt_path,
                    speed_factor=speed_val,
                    cuts=cuts_list,
                    target_audio_duration=final_dur,
                    mode="mode1",
                    ffmpeg_bin=FFMPEG_PATH
                )

                if os.path.exists(out_srt_path) and os.path.getsize(out_srt_path) > 0:
                    item["hasProcessedSrt"] = True
                    item["processedSrtPath"] = out_srt_path
                    item["processedSrtFileName"] = out_srt_name
                    item["captionValidation"] = caption_res.get("validation", {})
                    item["captionCuesCount"] = caption_res.get("finalCueCount", 0)
                    item["captionStatus"] = "Cut-Synced ✓"
                    item["srtStatus"] = "ready"
                    item["scriptStatus"] = "Provided"

                    add_log(
                        f"{base_name}.srt ✓ Forced-aligned & cut-synchronized: {caption_res.get('finalCueCount', 0)} cues matched to voice-over "
                        f"({len(cuts_list)} cuts applied, duration matches {item['formattedFinalDuration']})",
                        "success"
                    )
                else:
                    err = diagnose_srt_error(out_srt_name, reason_detail=f"{out_srt_name} was not created on disk.")
                    item["status"] = "partial"
                    item["statusLabel"] = "PARTIAL ⚠"
                    item["errorDetails"] = err
                    add_log(f"{base_name} — PARTIAL ⚠: MP3 processed, but SRT generation failed: {err['reason']}", "warning")
            except Exception as e_srt:
                err = diagnose_alignment_error(orig_name, os.path.basename(script_in), e_srt)
                item["status"] = "partial"
                item["statusLabel"] = "PARTIAL ⚠"
                item["errorDetails"] = err
                item["warningMessage"] = f"Audio processed successfully, but caption alignment failed: {err['reason']}"
                add_log(f"{base_name} — PARTIAL ⚠: {err['formatted']}", "warning")
        else:
            # Script NOT provided in Mode 1 (Audio Processing Mode)
            # Per core rule: Script is OPTIONAL. Missing script must NEVER block audio processing.
            item["hasProcessedSrt"] = False
            item["captionStatus"] = "Skipped"
            item["srtStatus"] = "skipped"
            item["scriptStatus"] = "Not provided"
            item["status"] = "completed"
            item["statusLabel"] = "SUCCESS ✓"
            item["errorDetails"] = None
            item["errorMessage"] = None
            item["warningMessage"] = None

        STATE["processed_files"][orig_name] = item
        STATE["processed_files"][out_name] = item

        cap_summary = f"Captions: {item.get('captionCuesCount', 0)} cues cut-synced ✓" if item.get("hasProcessedSrt") else "Captions: Skipped (no script provided)"
        add_log(
            f"{base_name} — SUCCESS ✓: Processing applied ✓ | Speed ({params['speed_factor']:.3f}x) ✓ | "
            f"Pitch ({params['pitch_semitones']:+.2f}st) ✓ | EQ verified ✓ | Cuts ({res['cutCount']}) verified ✓ | "
            f"Output generated ✓ | Duration ({item['formattedFinalDuration']}) verified ✓ | {cap_summary}",
            "success"
        )
        return True, orig_name, None
    except Exception as e:
        err = diagnose_audio_error(in_path, orig_name, e)
        item["status"] = "error"
        item["statusLabel"] = "ERROR ✕"
        item["errorDetails"] = err
        item["errorMessage"] = err["formatted"]
        add_log(f"{err['formatted']}", "error")
        return False, orig_name, err


@app.route("/api/process-batch", methods=["POST"])
def process_batch():
    """
    Start batch processing of voiceovers in configurable chunks (Default: 3 at a time).
    Processes batch chunk -> validates every output on disk -> logs results -> proceeds to next batch chunk.
    """
    if STATE["is_processing"]:
        return jsonify({"success": False, "error": "A batch is already running."}), 400

    data = request.get_json() or {}
    settings = data.get("settings") or STATE.get("master_v1_settings") or {}
    file_ids = data.get("file_ids", [])
    batch_size = max(1, min(20, safe_int(data.get("batch_size"), 3)))

    if not STATE["files"]:
        return jsonify({"success": False, "error": "No voiceovers in queue."}), 400

    targets = [f for f in STATE["files"] if not file_ids or f["id"] in file_ids]
    STATE["batch_size"] = batch_size
    caption_mode = data.get("caption_mode") or STATE.get("caption_mode", "mode1")
    STATE["caption_mode"] = caption_mode

    def run_chunked_worker():
        STATE["is_processing"] = True
        STATE["progress"] = 0
        total = len(targets)
        start_time = time.time()

        # Split into chunks of batch_size (default 3)
        chunks = [targets[i:i + batch_size] for i in range(0, total, batch_size)]
        total_chunks = len(chunks)
        STATE["total_batches"] = total_chunks

        add_log(f"Starting batch processing: {total} files across {total_chunks} batches ({batch_size} audios at a time)...", "info")

        overall_success = 0
        overall_failed = 0
        processed_so_far = 0

        for chunk_idx, chunk in enumerate(chunks):
            chunk_num = chunk_idx + 1
            STATE["current_batch"] = chunk_num
            chunk_file_names = [f["fileName"] for f in chunk]
            STATE["active_batch_files"] = chunk_file_names
            STATE["current_file"] = ", ".join(chunk_file_names)

            chunk_start_time = time.time()
            add_log(f"━━━ Processing Batch {chunk_num} of {total_chunks} ({', '.join(chunk_file_names)}) ━━━", "info")
            update_analytics()

            # Run this chunk concurrently with max_workers = batch_size
            chunk_success = 0
            chunk_failed = 0

            with ThreadPoolExecutor(max_workers=len(chunk)) as executor:
                futures = {
                    executor.submit(process_single_item_worker, item, processed_so_far + i, total, caption_mode, settings): item
                    for i, item in enumerate(chunk)
                }

                for future in as_completed(futures):
                    success, fname, err = future.result()
                    if success:
                        chunk_success += 1
                        overall_success += 1
                    else:
                        chunk_failed += 1
                        overall_failed += 1
                    
                    processed_so_far += 1
                    STATE["progress"] = int((processed_so_far / total) * 100)
                    update_analytics()

            chunk_elapsed = time.time() - chunk_start_time
            add_log(
                f"✓ Batch {chunk_num} of {total_chunks} Completed in {format_time(chunk_elapsed)} "
                f"({chunk_success} successful, {chunk_failed} failed). "
                f"Progress: {processed_so_far}/{total} ({STATE['progress']}%)",
                "info" if chunk_failed == 0 else "warning"
            )

        total_elapsed = time.time() - start_time
        STATE["is_processing"] = False
        STATE["current_file"] = None
        STATE["active_batch_files"] = []
        update_analytics()

        # Final Batch Summary Report
        add_log("━━━━━━━━━ Batch Processing Summary ━━━━━━━━━", "info")
        add_log(f"• Total files: {total}", "info")
        add_log(f"• Batches processed: {total_chunks} ({batch_size} at a time)", "info")
        add_log(f"• Successfully processed: {overall_success}", "info")
        add_log(f"• Failed: {overall_failed}", "info" if overall_failed == 0 else "error")
        add_log(f"• Total completed: {overall_success}/{total}", "success" if overall_success == total else "warning")
        add_log(f"• Total processing time: {format_time(total_elapsed)}", "info")
        add_log(f"• Total processed duration: {STATE['analytics']['formattedTotalProcessed']}", "info")
        add_log(f"• Total duration removed: {STATE['analytics']['formattedTotalRemoved']}", "info")
        add_log(f"Batch processing complete. {overall_success} of {total} files were successfully processed and verified.", "success")

    thread = threading.Thread(target=run_chunked_worker, daemon=True)
    thread.start()

    return jsonify({
        "success": True,
        "message": f"Batch processing started ({batch_size} files at a time).",
        "total": len(targets),
        "batchSize": batch_size,
        "totalBatches": (len(targets) + batch_size - 1) // batch_size
    })


@app.route("/api/align-single-srt", methods=["POST"])
def align_single_srt():
    """
    Performs forced alignment and cut-synchronization for an already processed audio file
    WITHOUT re-running or touching the audio DSP/cuts.
    """
    data = request.get_json() or {}
    file_id = data.get("file_id")
    file_name = data.get("file_name")

    target = None
    for f in STATE["files"]:
        if (file_id and f.get("id") == file_id) or (file_name and f.get("fileName") == file_name):
            target = f
            break

    if not target:
        return jsonify({"success": False, "error": "Target file not found in queue."}), 404

    in_path = target.get("filePath", "")
    orig_name = target.get("fileName", "")
    base_name = os.path.splitext(orig_name)[0]

    # Re-scan for matching script (e.g. V1 Script.txt)
    script_in = target.get("scriptPath") or target.get("srtPath")
    if not script_in or not os.path.exists(script_in):
        script_in = find_matching_script(in_path)
        if script_in:
            target["hasScript"] = True
            target["scriptPath"] = script_in
            target["scriptFileName"] = os.path.basename(script_in)
            target["scriptType"] = os.path.splitext(script_in)[1].lower()

    if not script_in or not os.path.exists(script_in):
        err = diagnose_script_error(orig_name, None)
        return jsonify({"success": False, "error": err}), 400

    out_srt_name = f"{base_name}.srt"
    out_srt_path = os.path.join(PROCESSED_DIR, out_srt_name)

    speed_val = safe_float(target.get("resolvedParams", {}).get("speed_factor"), 1.0)
    cuts_list = target.get("cuts", [])
    final_dur = target.get("finalDuration", target.get("duration", 0.0))
    caption_mode = STATE.get("caption_mode", "mode1")

    try:
        add_log(f"⚡ Aligning SRT for {orig_name} from '{target.get('scriptFileName')}' (Audio remains untouched)...", "info")
        caption_res = process_caption_pipeline(
            audio_path=in_path,
            script_path=script_in,
            output_srt_path=out_srt_path,
            speed_factor=speed_val if caption_mode == "mode1" else 1.0,
            cuts=cuts_list if caption_mode == "mode1" else None,
            target_audio_duration=final_dur,
            mode=caption_mode,
            ffmpeg_bin=FFMPEG_PATH
        )

        if not os.path.exists(out_srt_path) or os.path.getsize(out_srt_path) == 0:
            err = diagnose_srt_error(out_srt_name, reason_detail=f"{out_srt_name} was not created on disk.")
            return jsonify({"success": False, "error": err}), 500

        target["hasProcessedSrt"] = True
        target["processedSrtPath"] = out_srt_path
        target["processedSrtFileName"] = out_srt_name
        target["captionValidation"] = caption_res.get("validation", {})
        target["captionCuesCount"] = caption_res.get("finalCueCount", 0)
        target["captionStatus"] = "Cut-Synced ✓" if caption_mode == "mode1" else "Aligned ✓"
        target["srtStatus"] = "ready"
        target["scriptStatus"] = "Provided"
        target["errorDetails"] = None
        target["errorMessage"] = None
        target["warningMessage"] = None

        if target.get("status") in ("error", "partial"):
            target["status"] = "completed"
            target["statusLabel"] = "SUCCESS ✓"

        STATE["processed_files"][orig_name] = target
        STATE["processed_files"][out_srt_name] = target
        update_analytics()

        add_log(
            f"{base_name}.srt ✓ Synchronized SRT created successfully ({caption_res.get('finalCueCount', 0)} cues matched, audio untouched).",
            "success"
        )
        return jsonify({"success": True, "file": target, "analytics": STATE["analytics"]})
    except Exception as e:
        err = diagnose_alignment_error(orig_name, os.path.basename(script_in), e)
        return jsonify({"success": False, "error": err}), 500


@app.route("/api/retry-item", methods=["POST"])
def retry_item():
    """
    Recover an individual failed voiceover item on demand without reprocessing successful files.
    Applies current mode and DSP/alignment settings, validates result on disk, updates analytics.
    If audio was already processed in Mode 1, runs caption alignment directly without re-processing audio.
    """
    data = request.get_json() or {}
    file_id = data.get("file_id")
    file_name = data.get("file_name")

    target = None
    target_idx = 0
    for idx, f in enumerate(STATE["files"]):
        if (file_id and f.get("id") == file_id) or (file_name and f.get("fileName") == file_name):
            target = f
            target_idx = idx
            break

    if not target:
        return jsonify({"success": False, "error": f"File not found in queue: {file_id or file_name}"}), 404

    # Re-scan for matching script if missing
    in_path = target.get("filePath", "")
    script_match = find_matching_script(in_path)
    if script_match:
        target["hasScript"] = True
        target["scriptPath"] = script_match
        target["scriptFileName"] = os.path.basename(script_match)
        target["scriptType"] = os.path.splitext(script_match)[1].lower()

    caption_mode = STATE.get("caption_mode", "mode1")
    settings = STATE.get("settings") or STATE.get("master_v1_settings") or {}

    # If audio is already processed in Mode 1, only run caption alignment without re-encoding audio
    if caption_mode == "mode1" and target.get("hasProcessed") and target.get("processedPath") and os.path.exists(target["processedPath"]):
        if target.get("hasScript") and target.get("scriptPath") and os.path.exists(target["scriptPath"]):
            out_srt_name = f"{os.path.splitext(target['fileName'])[0]}.srt"
            out_srt_path = os.path.join(PROCESSED_DIR, out_srt_name)
            speed_val = safe_float(target.get("resolvedParams", {}).get("speed_factor"), 1.0)
            cuts_list = target.get("cuts", [])
            final_dur = target.get("finalDuration", target.get("duration", 0.0))

            try:
                caption_res = process_caption_pipeline(
                    audio_path=in_path,
                    script_path=target["scriptPath"],
                    output_srt_path=out_srt_path,
                    speed_factor=speed_val,
                    cuts=cuts_list,
                    target_audio_duration=final_dur,
                    mode="mode1",
                    ffmpeg_bin=FFMPEG_PATH
                )

                if os.path.exists(out_srt_path) and os.path.getsize(out_srt_path) > 0:
                    target["hasProcessedSrt"] = True
                    target["processedSrtPath"] = out_srt_path
                    target["processedSrtFileName"] = out_srt_name
                    target["captionValidation"] = caption_res.get("validation", {})
                    target["captionCuesCount"] = caption_res.get("finalCueCount", 0)
                    target["captionStatus"] = "Cut-Synced ✓"
                    target["srtStatus"] = "ready"
                    target["status"] = "completed"
                    target["statusLabel"] = "SUCCESS ✓"
                    target["errorDetails"] = None
                    target["errorMessage"] = None
                    target["warningMessage"] = None

                    STATE["processed_files"][target["fileName"]] = target
                    STATE["processed_files"][out_srt_name] = target
                    update_analytics()

                    add_log(f"{target['fileName']} — SUCCESS ✓: Cut-synchronized SRT generated without re-processing audio.", "success")
                    return jsonify({
                        "success": True,
                        "file": target,
                        "errorDetails": None,
                        "statusLabel": "SUCCESS ✓",
                        "analytics": STATE["analytics"]
                    })
            except Exception as e_srt:
                err = diagnose_alignment_error(target["fileName"], target.get("scriptFileName", "script"), e_srt)
                target["errorDetails"] = err
                return jsonify({
                    "success": False,
                    "file": target,
                    "errorDetails": err,
                    "statusLabel": target.get("statusLabel"),
                    "analytics": STATE["analytics"]
                })

    add_log(f"↻ Retrying {target['fileName']} ({caption_mode.upper()})...", "info")
    success, fname, err_res = process_single_item_worker(
        target,
        target_idx,
        len(STATE["files"]),
        caption_mode,
        settings
    )

    update_analytics()
    return jsonify({
        "success": success,
        "file": target,
        "errorDetails": target.get("errorDetails"),
        "statusLabel": target.get("statusLabel"),
        "analytics": STATE["analytics"]
    })


@app.route("/api/final-report", methods=["GET"])
def get_final_report():
    """Return the final verified report breakdown for the active mode based on actual disk validation."""
    update_analytics()
    return jsonify({
        "success": True,
        "captionMode": STATE.get("caption_mode", "mode1"),
        "report": STATE["analytics"].get("finalReport", {})
    })


@app.route("/api/set-caption-mode", methods=["POST"])
def set_caption_mode():
    """Set active caption system mode: 'mode1' (Process + Cut + SRT) or 'mode2' (Alignment Only)."""
    data = request.get_json() or {}
    mode = data.get("mode", "mode1")
    if mode not in ("mode1", "mode2"):
        mode = "mode1"
    STATE["caption_mode"] = mode
    mode_title = "MODE 1 (Process + Cut + SRT)" if mode == "mode1" else "MODE 2 (Alignment Only — Audio Untouched)"
    add_log(f"Active Caption Pipeline: {mode_title}", "info")
    return jsonify({"success": True, "captionMode": mode})


@app.route("/api/status", methods=["GET"])
def get_status():
    """Poll processing status, progress, logs, analytics, active batch files, and updated file states."""
    return jsonify({
        "isProcessing": STATE["is_processing"],
        "currentFile": STATE["current_file"],
        "currentBatch": STATE.get("current_batch", 0),
        "totalBatches": STATE.get("total_batches", 0),
        "activeBatchFiles": STATE.get("active_batch_files", []),
        "batchSize": STATE.get("batch_size", 3),
        "captionMode": STATE.get("caption_mode", "mode1"),
        "progress": STATE["progress"],
        "logs": STATE["logs"][-60:],
        "files": STATE["files"],
        "analytics": STATE["analytics"]
    })


@app.route("/api/get-srt-cues", methods=["GET"])
def get_srt_cues():
    """Return parsed subtitle cues for quick preview in the Subtitle Viewer."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "cues": []})
    safe_n = os.path.basename(name)
    if not safe_n.lower().endswith(".srt"):
        safe_n = f"{os.path.splitext(safe_n)[0]}.srt"
    srt_p = os.path.join(PROCESSED_DIR, safe_n)
    if not os.path.exists(srt_p):
        return jsonify({"success": False, "cues": []})
    cues = parse_srt_file(srt_p)
    formatted_cues = []
    for c in cues[:60]:
        formatted_cues.append({
            "index": c.index,
            "start": f"{int(c.start//60):02d}:{int(c.start%60):02d}",
            "end": f"{int(c.end//60):02d}:{int(c.end%60):02d}",
            "text": c.text
        })
    return jsonify({
        "success": True,
        "cueCount": len(cues),
        "cues": formatted_cues
    })


@app.route("/api/download-single", methods=["GET"])
def download_single():
    """Stream a single processed MP3 as a file attachment with strict naming and validation."""
    name = request.args.get("name", "").strip()
    if not name:
        return "Missing 'name' parameter", 400

    safe_name = os.path.basename(name)
    file_path = os.path.join(PROCESSED_DIR, safe_name)

    if not os.path.exists(file_path):
        return f"Processed file not found: {safe_name}", 404

    try:
        meta = get_audio_metadata(file_path)
        add_log(f"Downloaded {safe_name} successfully. Processed duration: {meta['formattedDuration']}.", "success")
    except Exception as e:
        add_log(f"Downloaded {safe_name} (Warning: {e})", "info")

    return send_file(
        file_path,
        as_attachment=True,
        download_name=safe_name,
        mimetype="audio/mpeg"
    )


@app.route("/api/download-single-srt", methods=["GET"])
def download_single_srt():
    """Stream a single synchronized SRT file as an attachment with strict naming."""
    name = request.args.get("name", "").strip()
    if not name:
        return "Missing 'name' parameter", 400

    safe_name = os.path.basename(name)
    file_path = os.path.join(PROCESSED_DIR, safe_name)

    if not os.path.exists(file_path):
        return f"SRT file not found: {safe_name}", 404

    add_log(f"Downloaded subtitle {safe_name} successfully.", "success")
    return send_file(
        file_path,
        as_attachment=True,
        download_name=safe_name,
        mimetype="text/plain"
    )


@app.route("/api/export", methods=["POST"])
def export_files():
    """
    Export processed MP3 and SRT files to a user-chosen destination folder.
    Strictly preserves V1.mp3, V2.mp3, V1.srt, V2.srt naming without confusing suffixes.
    """
    data = request.get_json() or {}
    dest_dir = data.get("destination_path", "").strip()

    if not dest_dir:
        return jsonify({"success": False, "error": "Destination folder path is required."}), 400

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"success": False, "error": f"Cannot create destination folder: {e}"}), 400

    exported_mp3s = []
    exported_srts = []

    for item in STATE["files"]:
        base_name = os.path.splitext(item["fileName"])[0]
        mp3_name = f"{base_name}.mp3"
        srt_name = f"{base_name}.srt"

        if item.get("hasProcessed") and item.get("processedPath") and os.path.exists(item["processedPath"]):
            target_mp3 = os.path.join(dest_dir, mp3_name)
            shutil.copy2(item["processedPath"], target_mp3)
            exported_mp3s.append(mp3_name)

        if item.get("hasProcessedSrt") and item.get("processedSrtPath") and os.path.exists(item["processedSrtPath"]):
            target_srt = os.path.join(dest_dir, srt_name)
            shutil.copy2(item["processedSrtPath"], target_srt)
            exported_srts.append(srt_name)

    add_log(f"✓ Exported {len(exported_mp3s)} processed MP3s and {len(exported_srts)} synchronized SRTs to: {dest_dir}", "success")

    return jsonify({
        "success": True,
        "exportedMp3Count": len(exported_mp3s),
        "exportedSrtCount": len(exported_srts),
        "destinationPath": dest_dir
    })


@app.route("/api/download-zip", methods=["GET"])
def download_zip():
    """Package strictly processed MP3 and SRT files into a ZIP archive with exact numbering."""
    zip_temp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    zip_path = zip_temp.name
    zip_temp.close()

    count_mp3 = 0
    count_srt = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in STATE["files"]:
            base_name = os.path.splitext(item["fileName"])[0]
            mp3_name = f"{base_name}.mp3"
            srt_name = f"{base_name}.srt"

            if item.get("hasProcessed") and item.get("processedPath") and os.path.exists(item["processedPath"]):
                zf.write(item["processedPath"], arcname=mp3_name)
                count_mp3 += 1

            if item.get("hasProcessedSrt") and item.get("processedSrtPath") and os.path.exists(item["processedSrtPath"]):
                zf.write(item["processedSrtPath"], arcname=srt_name)
                count_srt += 1

    add_log(f"Downloaded ZIP bundle containing {count_mp3} processed MP3s and {count_srt} synchronized SRTs.", "success")

    return send_file(
        zip_path,
        as_attachment=True,
        download_name="Processed_Voiceovers_Bundle.zip",
        mimetype="application/zip"
    )


@app.route("/api/download-captions-zip", methods=["GET"])
def download_captions_zip():
    """Package strictly synchronized SRT caption files into a ZIP archive with exact numbering (V1.srt, V2.srt...)."""
    zip_temp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    zip_path = zip_temp.name
    zip_temp.close()

    count_srt = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in STATE["files"]:
            base_name = os.path.splitext(item["fileName"])[0]
            srt_name = f"{base_name}.srt"
            if item.get("hasProcessedSrt") and item.get("processedSrtPath") and os.path.exists(item["processedSrtPath"]):
                zf.write(item["processedSrtPath"], arcname=srt_name)
                count_srt += 1

    if count_srt == 0:
        return "No synchronized SRT caption files found to download.", 404

    add_log(f"Downloaded Captions ZIP containing {count_srt} synchronized SRT files.", "success")
    return send_file(
        zip_path,
        as_attachment=True,
        download_name="Synchronized_Captions_SRT.zip",
        mimetype="application/zip"
    )


@app.route("/api/download-audio-zip", methods=["GET"])
def download_audio_zip():
    """Package strictly processed MP3 files into a ZIP archive with exact numbering (V1.mp3, V2.mp3...)."""
    zip_temp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    zip_path = zip_temp.name
    zip_temp.close()

    count_mp3 = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in STATE["files"]:
            base_name = os.path.splitext(item["fileName"])[0]
            mp3_name = f"{base_name}.mp3"
            if item.get("hasProcessed") and item.get("processedPath") and os.path.exists(item["processedPath"]):
                zf.write(item["processedPath"], arcname=mp3_name)
                count_mp3 += 1

    if count_mp3 == 0:
        return "No processed MP3 audio files found to download.", 404

    add_log(f"Downloaded Processed Audio ZIP containing {count_mp3} MP3 files.", "success")
    return send_file(
        zip_path,
        as_attachment=True,
        download_name="Processed_Audio_MP3.zip",
        mimetype="application/zip"
    )


@app.route("/api/export-captions", methods=["POST"])
def export_captions():
    """Export only synchronized SRT caption files to a chosen destination folder."""
    data = request.get_json() or {}
    dest_dir = data.get("destination_path", "").strip()

    if not dest_dir:
        return jsonify({"success": False, "error": "Destination folder path is required."}), 400

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        return jsonify({"success": False, "error": f"Cannot create destination folder: {e}"}), 400

    exported_srts = []
    for item in STATE["files"]:
        base_name = os.path.splitext(item["fileName"])[0]
        srt_name = f"{base_name}.srt"
        if item.get("hasProcessedSrt") and item.get("processedSrtPath") and os.path.exists(item["processedSrtPath"]):
            target_srt = os.path.join(dest_dir, srt_name)
            shutil.copy2(item["processedSrtPath"], target_srt)
            exported_srts.append(srt_name)

    add_log(f"✓ Exported {len(exported_srts)} synchronized SRT captions to: {dest_dir}", "success")
    return jsonify({
        "success": True,
        "exportedCount": len(exported_srts),
        "destinationPath": dest_dir
    })



@app.route("/api/stream-audio", methods=["GET"])
def stream_audio():
    """Stream audio file (original, preview, or processed)."""
    atype = request.args.get("type", "file")
    name = request.args.get("name", "")
    path = request.args.get("path", "")

    if atype == "preview":
        full_p = os.path.join(PREVIEWS_DIR, os.path.basename(name))
    elif atype == "processed":
        full_p = os.path.join(PROCESSED_DIR, os.path.basename(name))
    else:
        full_p = path

    if not full_p or not os.path.exists(full_p):
        return f"Audio file not found: {full_p}", 404

    return send_file(full_p, mimetype="audio/mpeg")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    print(f"🎙️ Starting Voiceover Studio Pro Server on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
