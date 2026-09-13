import os
import csv
import time
import zipfile
import tempfile
import threading
import logging
import shutil
from datetime import datetime
from typing import List, Dict, Any, Optional

from .audio_utils import standardize_audio
from .validator import JobPair, verify_synchronization_and_duration
from .engine import AlignerModel
from .srt_builder import (
    build_srt_from_word_segments,
    build_vtt_from_word_segments,
    extract_clean_text_from_srt,
    parse_srt_to_cues
)
from .checkpoint import CheckpointManager

logger = logging.getLogger(__name__)


class NaturalActivityLog:
    def __init__(self, level: str, title: str, problem: str = "", solution: str = ""):
        self.timestamp = datetime.now().strftime('%H:%M:%S')
        self.level = level.upper()  # 'COMPLETED', 'WARNING', 'ERROR', 'INFO'
        self.title = title
        self.problem = problem
        self.solution = solution

    def to_dict(self) -> Dict[str, str]:
        return {
            'timestamp': self.timestamp,
            'level': self.level,
            'title': self.title,
            'problem': self.problem,
            'solution': self.solution
        }


class BatchProgress:
    def __init__(self, total: int = 0):
        self.total = total
        self.completed = 0
        self.successful = 0
        self.warning_count = 0
        self.failed = 0
        self.percent = 0.0
        self.current_file = ''
        self.current_stage = ''
        self.speed_fps = 0.0
        self.eta_seconds = 0.0
        self.elapsed_seconds = 0.0
        self.is_running = False
        self.is_paused = False
        self.is_cancelled = False
        self.is_finished = False
        self.start_time = 0.0
        self.total_audio_seconds = 0.0
        self.total_subtitles_generated = 0
        self.activity_logs: List[NaturalActivityLog] = []
        self._lock = threading.Lock()

    def add_log(self, level: str, title: str, problem: str = "", solution: str = ""):
        log_obj = NaturalActivityLog(level=level, title=title, problem=problem, solution=solution)
        with self._lock:
            self.activity_logs.append(log_obj)
            if len(self.activity_logs) > 300:
                self.activity_logs.pop(0)

    def set_stage(self, file_name: str, stage: str):
        with self._lock:
            self.current_file = file_name
            self.current_stage = stage

    def update_progress(self, file_name: str, status: str, audio_dur: float = 0.0, sub_count: int = 0):
        with self._lock:
            self.completed += 1
            if status == 'COMPLETED':
                self.successful += 1
            elif status == 'WARNING':
                self.warning_count += 1
                self.successful += 1
            else:
                self.failed += 1

            self.current_file = file_name
            self.total_audio_seconds += audio_dur
            self.total_subtitles_generated += sub_count

            if self.total > 0:
                self.percent = round((self.completed / self.total) * 100.0, 1)

            now = time.time()
            if self.start_time > 0:
                self.elapsed_seconds = round(now - self.start_time, 1)
                if self.elapsed_seconds > 0.5 and self.completed > 0:
                    self.speed_fps = round(self.completed / self.elapsed_seconds, 2)
                    remaining = self.total - self.completed
                    if self.speed_fps > 0:
                        self.eta_seconds = round(remaining / self.speed_fps, 1)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            avg_time = round(self.elapsed_seconds / max(1, self.completed), 1)
            return {
                'total': self.total,
                'completed': self.completed,
                'successful': self.successful,
                'warning_count': self.warning_count,
                'failed': self.failed,
                'percent': self.percent,
                'current_file': self.current_file,
                'current_stage': self.current_stage,
                'speed_fps': self.speed_fps,
                'eta_seconds': self.eta_seconds,
                'elapsed_seconds': self.elapsed_seconds,
                'is_running': self.is_running,
                'is_paused': self.is_paused,
                'is_cancelled': self.is_cancelled,
                'is_finished': self.is_finished,
                'total_audio_seconds': round(self.total_audio_seconds, 1),
                'total_subtitles_generated': self.total_subtitles_generated,
                'avg_time_per_file': avg_time,
                'activity_logs': [l.to_dict() for l in self.activity_logs]
            }


class AlignmentPipeline:
    def __init__(self, output_dir: str = 'output', max_workers: int = 4):
        self.output_dir = os.path.abspath(output_dir)
        self.max_workers = max_workers
        self.progress = BatchProgress(0)
        self.checkpoint = CheckpointManager()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        os.makedirs(self.output_dir, exist_ok=True)

    def pause(self):
        self._pause_event.clear()
        self.progress.is_paused = True
        self.progress.add_log(
            level="INFO",
            title="Processing Paused",
            problem="User initiated pause.",
            solution="All completed files and progress are safely preserved. Click Resume when ready."
        )

    def resume(self):
        self.progress.is_paused = False
        self._pause_event.set()
        self.progress.add_log(
            level="INFO",
            title="Processing Resumed",
            problem="None",
            solution="Continuing batch alignment from the next incomplete file."
        )

    def cancel(self):
        self._stop_event.set()
        self._pause_event.set()
        self.progress.is_cancelled = True
        self.progress.is_running = False
        self.progress.add_log(
            level="WARNING",
            title="Batch Processing Cancelled",
            problem="Processing was cancelled by the user.",
            solution="All previously generated SRT files are intact and preserved in the output directory."
        )

    def run_batch(
        self,
        jobs: List[JobPair],
        max_chars_per_line: int = 38,
        max_lines_per_cue: int = 2,
        language: str = 'russian',
        resume_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Executes dedicated Voice-over + Script forced alignment.
        """
        self._stop_event.clear()
        self._pause_event.set()

        saved_state = self.checkpoint.load()
        completed_map = saved_state.get('completed_files', {}) if resume_mode else {}

        self.progress = BatchProgress(total=len(jobs))
        self.progress.is_running = True
        self.progress.start_time = time.time()

        if resume_mode and completed_map:
            self.progress.completed = len(completed_map)
            self.progress.successful = len(completed_map)
            self.progress.add_log(
                level="INFO",
                title="Resuming Alignment Project",
                problem="None",
                solution=f"Found {len(completed_map)} already verified files. Skipping to next incomplete file."
            )

        aligner = AlignerModel.get_instance()
        temp_dir = tempfile.mkdtemp(prefix='audio_align_v12_')
        failed_records: List[Dict[str, str]] = []

        try:
            for job in jobs:
                if self._stop_event.is_set():
                    break

                self._pause_event.wait()
                audio_fname = os.path.basename(job.audio_path) if job.audio_path else job.base_name

                # Skip if already completed
                if resume_mode and job.base_name in completed_map:
                    continue

                t_start = time.time()

                # Check if script exists
                if not job.script_text or not job.script_text.strip():
                    failed_records.append({
                        'Filename': audio_fname,
                        'Status': 'FAILED',
                        'Reason': f"Missing script file for {audio_fname}",
                        'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
                    self.checkpoint.record_failed_file(
                        job.base_name,
                        f"Missing script file for {audio_fname}",
                        {
                            'problem': f"No matching script was provided for {audio_fname}.",
                            'solution': f"Add matching script {job.base_name}.txt or {job.base_name}.docx and retry."
                        }
                    )
                    self.progress.update_progress(audio_fname, status='FAILED')
                    self.progress.add_log(
                        level="ERROR",
                        title=f"Missing Script: {audio_fname}",
                        problem=f"No matching script file found for {audio_fname}.",
                        solution=f"Add {job.base_name}.txt or {job.base_name}.docx to your folder and click Validate."
                    )
                    continue

                # Stage 1: Loading
                self.progress.set_stage(audio_fname, "Loading")
                time.sleep(0.04)

                # Convert to standard 16kHz WAV
                out_wav = os.path.join(temp_dir, f"{job.base_name}_16k.wav")
                try:
                    standardize_audio(job.audio_path, out_wav)
                except Exception as e:
                    failed_records.append({
                        'Filename': audio_fname,
                        'Status': 'FAILED',
                        'Reason': str(e),
                        'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
                    self.checkpoint.record_failed_file(
                        job.base_name,
                        str(e),
                        {
                            'problem': f"Audio standardization error for {audio_fname}: {e}",
                            'solution': "Ensure the audio file is not corrupt and is in a supported format (.mp3, .wav, .m4a)."
                        }
                    )
                    self.progress.update_progress(audio_fname, status='FAILED')
                    self.progress.add_log(
                        level="ERROR",
                        title=f"Audio Error: {audio_fname}",
                        problem=f"Could not convert audio: {e}",
                        solution="Ensure the audio file is not damaged and in a supported format."
                    )
                    continue

                # Stage 2: Forced Alignment
                try:
                    self.progress.set_stage(audio_fname, "Analyzing Audio")
                    time.sleep(0.04)
                    self.progress.set_stage(audio_fname, "Synchronizing Script")

                    word_segments = aligner.align(out_wav, job.script_text, language=language)

                    # Stage 3: Generating SRT & Formats
                    self.progress.set_stage(audio_fname, "Generating SRT")
                    srt_content = build_srt_from_word_segments(
                        word_segments,
                        max_chars_per_line=max_chars_per_line,
                        max_lines_per_cue=max_lines_per_cue
                    )
                    vtt_content = build_vtt_from_word_segments(
                        word_segments,
                        max_chars_per_line=max_chars_per_line,
                        max_lines_per_cue=max_lines_per_cue
                    )
                    txt_content = extract_clean_text_from_srt(srt_content)

                    srt_out_path = os.path.join(self.output_dir, f"{job.base_name}.srt")
                    vtt_out_path = os.path.join(self.output_dir, f"{job.base_name}.vtt")
                    txt_out_path = os.path.join(self.output_dir, f"{job.base_name}.txt")

                    with open(srt_out_path, 'w', encoding='utf-8-sig', newline='') as f:
                        f.write(srt_content)
                    with open(vtt_out_path, 'w', encoding='utf-8-sig', newline='') as f:
                        f.write(vtt_content)
                    with open(txt_out_path, 'w', encoding='utf-8-sig', newline='') as f:
                        f.write(txt_content)

                    # Stage 4: 10-Point Final Synchronization & Duration Check
                    self.progress.set_stage(audio_fname, "Final Duration Check")
                    passed, warnings, errors, prob_sol = verify_synchronization_and_duration(
                        job.audio_path, srt_out_path, audio_duration=job.audio_duration
                    )

                    cues = parse_srt_to_cues(srt_out_path)
                    sub_count = len(cues)
                    proc_time = round(time.time() - t_start, 2)

                    if passed and not warnings:
                        self.checkpoint.record_completed_file(
                            base_name=job.base_name,
                            srt_path=srt_out_path,
                            vtt_path=vtt_out_path,
                            txt_path=txt_out_path,
                            duration=job.audio_duration,
                            subtitle_count=sub_count,
                            processing_time=proc_time,
                            sync_passed=True,
                            sync_details="Passed all 10 checks"
                        )
                        self.progress.update_progress(
                            audio_fname, status='COMPLETED',
                            audio_dur=job.audio_duration, sub_count=sub_count
                        )
                        self.progress.add_log(
                            level="COMPLETED",
                            title=f"Verified: {job.base_name}.srt",
                            problem="None",
                            solution=f"Generated {sub_count} synchronized subtitles across {job.audio_duration:.1f}s audio in {proc_time}s."
                        )

                    elif passed and warnings:
                        warn_text = '; '.join(warnings)
                        self.checkpoint.record_warning_file(job.base_name, warn_text, prob_sol)
                        self.checkpoint.record_completed_file(
                            base_name=job.base_name,
                            srt_path=srt_out_path,
                            vtt_path=vtt_out_path,
                            txt_path=txt_out_path,
                            duration=job.audio_duration,
                            subtitle_count=sub_count,
                            processing_time=proc_time,
                            sync_passed=True,
                            sync_details=warn_text
                        )
                        self.progress.update_progress(
                            audio_fname, status='WARNING',
                            audio_dur=job.audio_duration, sub_count=sub_count
                        )
                        self.progress.add_log(
                            level="WARNING",
                            title=f"Warning: {job.base_name}.srt",
                            problem=prob_sol.get('problem', warn_text),
                            solution=prob_sol.get('solution', "Check timing in Center Preview.")
                        )

                    else:
                        err_text = '; '.join(errors)
                        failed_records.append({
                            'Filename': audio_fname,
                            'Status': 'FAILED',
                            'Reason': err_text,
                            'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        self.checkpoint.record_failed_file(job.base_name, err_text, prob_sol)
                        self.progress.update_progress(audio_fname, status='FAILED')
                        self.progress.add_log(
                            level="ERROR",
                            title=f"Sync Check Issue: {job.base_name}",
                            problem=prob_sol.get('problem', err_text),
                            solution=prob_sol.get('solution', "Adjust timestamps in Subtitle Editor.")
                        )

                except Exception as e:
                    logger.error(f"Alignment failed on {audio_fname}: {e}", exc_info=True)
                    failed_records.append({
                        'Filename': audio_fname,
                        'Status': 'FAILED',
                        'Reason': str(e),
                        'Timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
                    self.checkpoint.record_failed_file(
                        job.base_name,
                        str(e),
                        {
                            'problem': f"Processing interrupted on {audio_fname}: {e}",
                            'solution': "Ensure the script matches spoken dialogue and retry."
                        }
                    )
                    self.progress.update_progress(audio_fname, status='FAILED')
                    self.progress.add_log(
                        level="ERROR",
                        title=f"Alignment Failed: {audio_fname}",
                        problem=f"Could not synchronize {audio_fname} with its script: {e}",
                        solution="Check that the script text matches spoken audio."
                    )

            self._write_failed_csv(failed_records)
            self._build_zip_archive()

            if not self._stop_event.is_set():
                state = self.checkpoint.load()
                state['is_finished'] = True
                self.checkpoint.save(state)
                self.progress.is_finished = True
                self.progress.set_stage("", "Completed")
                self.progress.add_log(
                    level="COMPLETED",
                    title="Batch Alignment Complete",
                    problem="None",
                    solution=f"Processed all files. Output saved to {self.output_dir}."
                )

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.progress.is_running = False

        return self.progress.to_dict()

    def _write_failed_csv(self, records: List[Dict[str, str]]) -> str:
        csv_path = os.path.join(self.output_dir, 'failed_jobs.csv')
        if not records:
            if os.path.exists(csv_path):
                try:
                    os.remove(csv_path)
                except Exception:
                    pass
            return csv_path

        fieldnames = ['Filename', 'Status', 'Reason', 'Timestamp']
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                writer.writerow(r)
        return csv_path

    def _build_zip_archive(self) -> str:
        zip_path = os.path.join(self.output_dir, 'captions.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item in os.listdir(self.output_dir):
                if item.endswith('.srt') or item.endswith('.vtt') or item.endswith('.txt') or item == 'failed_jobs.csv':
                    full_p = os.path.join(self.output_dir, item)
                    zf.write(full_p, arcname=item)
        return zip_path
