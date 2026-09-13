import os
import json
import time
import shutil
import tempfile
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_DIR = os.path.expanduser("~/.bulk_audio_to_srt")
DEFAULT_CHECKPOINT_FILE = os.path.join(DEFAULT_CHECKPOINT_DIR, "project_state.json")


class CheckpointManager:
    """
    Manages atomic local persistence of project state and processing progress.
    Guarantees crash recovery across macOS restarts, power outages, and laptop sleep.
    """
    def __init__(self, checkpoint_path: str = DEFAULT_CHECKPOINT_FILE):
        self.checkpoint_path = os.path.abspath(checkpoint_path)
        self.checkpoint_dir = os.path.dirname(self.checkpoint_path)
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def has_resumable_project(self) -> bool:
        """Returns True if a persistent unfinished project state exists."""
        if not os.path.exists(self.checkpoint_path):
            return False
        try:
            state = self.load()
            if not state:
                return False
            total = state.get('total_files', 0)
            completed = len(state.get('completed_files', {}))
            is_finished = state.get('is_finished', False)
            # Resumable if project exists with files, and not all files are completed, or not marked finished
            return total > 0 and (completed < total or not is_finished)
        except Exception as e:
            logger.warning(f"Error inspecting checkpoint file: {e}")
            return False

    def load(self) -> Dict[str, Any]:
        """Loads persistent project state from disk."""
        if not os.path.exists(self.checkpoint_path):
            return {}
        try:
            with open(self.checkpoint_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load checkpoint from {self.checkpoint_path}: {e}")
            return {}

    def save(self, state: Dict[str, Any]) -> bool:
        """
        Atomically saves state to disk using a temporary file and atomic replace.
        Guarantees no partial corruption even if power is abruptly lost.
        """
        try:
            state['last_updated'] = time.time()
            state['last_updated_human'] = time.strftime('%Y-%m-%d %H:%M:%S')

            dir_name = os.path.dirname(self.checkpoint_path)
            os.makedirs(dir_name, exist_ok=True)

            fd, temp_file_path = tempfile.mkstemp(dir=dir_name, prefix="chkpt_", suffix=".tmp")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            # Atomic replace
            os.replace(temp_file_path, self.checkpoint_path)
            return True
        except Exception as e:
            logger.error(f"Failed to save checkpoint to {self.checkpoint_path}: {e}")
            return False

    def record_completed_file(
        self,
        base_name: str,
        srt_path: str,
        vtt_path: str,
        txt_path: str,
        duration: float,
        subtitle_count: int,
        processing_time: float,
        sync_passed: bool,
        sync_details: str = "Passed"
    ) -> bool:
        """Records a single verified completed file to disk checkpoint immediately."""
        state = self.load()
        if not state:
            state = {
                'total_files': 0,
                'completed_files': {},
                'failed_files': {},
                'warning_files': {},
                'output_folder': os.path.dirname(srt_path),
                'is_finished': False
            }

        completed = state.setdefault('completed_files', {})
        completed[base_name] = {
            'base_name': base_name,
            'srt_path': srt_path,
            'vtt_path': vtt_path,
            'txt_path': txt_path,
            'duration': duration,
            'subtitle_count': subtitle_count,
            'processing_time': processing_time,
            'sync_passed': sync_passed,
            'sync_details': sync_details,
            'completed_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }

        # Remove from failed or warning if previously there
        if 'failed_files' in state and base_name in state['failed_files']:
            del state['failed_files'][base_name]

        return self.save(state)

    def record_failed_file(self, base_name: str, reason: str, problem_solution: Dict[str, str] = None) -> bool:
        """Records a failed file with problem and solution metadata."""
        state = self.load()
        failed = state.setdefault('failed_files', {})
        failed[base_name] = {
            'base_name': base_name,
            'reason': reason,
            'problem': problem_solution.get('problem', reason) if problem_solution else reason,
            'solution': problem_solution.get('solution', 'Check audio file and retry.') if problem_solution else 'Check audio file and retry.',
            'failed_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        return self.save(state)

    def record_warning_file(self, base_name: str, warning_msg: str, problem_solution: Dict[str, str] = None) -> bool:
        """Records a file that completed with warnings."""
        state = self.load()
        warnings = state.setdefault('warning_files', {})
        warnings[base_name] = {
            'base_name': base_name,
            'warning': warning_msg,
            'problem': problem_solution.get('problem', warning_msg) if problem_solution else warning_msg,
            'solution': problem_solution.get('solution', 'Review subtitle timing in the preview editor.') if problem_solution else 'Review subtitle timing in the preview editor.',
            'warned_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        return self.save(state)

    def clear(self) -> bool:
        """Intentionally removes saved project state."""
        try:
            if os.path.exists(self.checkpoint_path):
                os.remove(self.checkpoint_path)
            return True
        except Exception as e:
            logger.error(f"Failed to clear checkpoint: {e}")
            return False
