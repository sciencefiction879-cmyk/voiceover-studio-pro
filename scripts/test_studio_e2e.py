#!/usr/bin/env python3
import urllib.request
import json
import time
import os
import sys

BASE_URL = 'http://127.0.0.1:5055'

def post_json(endpoint, data):
    req = urllib.request.Request(
        f'{BASE_URL}{endpoint}',
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def get_json(endpoint):
    with urllib.request.urlopen(f'{BASE_URL}{endpoint}') as resp:
        return json.loads(resp.read().decode('utf-8'))

print('=== 1. Checking Health API ===', flush=True)
h = get_json('/api/health')
print(f"FFmpeg available: {h['ffmpeg']['available']}")
print(f"Whisper engine: {h['whisper']['engine']}")

print('\n=== 2. Scanning Folder ===', flush=True)
sample_dir = os.path.abspath('sample_voiceovers')
scan = post_json('/api/scan-folder', {'folder_path': sample_dir})
print(f"Found {scan['count']} files:")
for f in scan['files']:
    print(f"  #{f['index']} {f['fileName']}: duration={f['formattedDuration']}, cuts={f['cutCount']}", flush=True)

# Target file
target_file = scan['files'][0]
target_id = target_file['id']

print(f'\n=== 3. Testing Single Preview on {target_file["fileName"]} (ID: {target_id}) ===', flush=True)
prev = post_json('/api/preview', {
    'file_id': target_id,
    'settings': {
        'pitch_min': 0.5, 'pitch_max': 0.5,
        'speed_min': 1.05, 'speed_max': 1.05,
        'bass_min': 1.5, 'bass_max': 1.5,
        'denoise_enabled': True,
        'loudnorm_enabled': True
    },
    'preview_duration': 30.0
})
print(f"Preview generated: {prev['success']}, url={prev.get('previewUrl')}", flush=True)

print(f'\n=== 4. Starting Batch Audio Processing on {target_file["fileName"]} ===', flush=True)
batch = post_json('/api/process-batch', {
    'file_ids': [target_id],
    'settings': {
        'randomize_per_file': True,
        'pitch_min': -0.5, 'pitch_max': 0.5,
        'speed_min': 0.98, 'speed_max': 1.04,
        'bass_min': 0.0, 'bass_max': 2.0,
        'denoise_enabled': True,
        'comp_enabled': True,
        'loudnorm_enabled': True,
        'limiter_enabled': True
    }
})

print(f"Batch response: {batch}", flush=True)

while True:
    time.sleep(1.0)
    st = get_json('/api/status')
    print(f"  Audio Progress: {st['progress']}% | Current: {st.get('currentFile')} | Processing: {st['isProcessing']}", flush=True)
    if not st['isProcessing'] and st['progress'] == 100:
        break

print('\n=== 5. Generating SRT (Time-Synced) ===', flush=True)
srt_res = post_json('/api/generate-srt-batch', {'model_size': 'base'})
print(f"SRT batch response: {srt_res}", flush=True)

while True:
    time.sleep(1.0)
    st = get_json('/api/status')
    print(f"  SRT Progress: {st['progress']}% | Current: {st.get('currentFile')} | Processing: {st['isProcessing']}", flush=True)
    if not st['isProcessing'] and st['progress'] == 100:
        break

print('\n=== 6. Testing Manual Export (MP3 + SRT) ===', flush=True)
export_dir = os.path.abspath('data/exports/test_run')
exp = post_json('/api/export', {
    'destination_path': export_dir,
    'export_type': 'both'
})
print(f"Export result: {exp['success']}, MP3 count: {exp['exportedMp3Count']}, SRT count: {exp['exportedSrtCount']}", flush=True)

exported = sorted(os.listdir(export_dir))
print(f"Files in export folder ({export_dir}): {exported}", flush=True)
target_base = os.path.splitext(target_file['fileName'])[0]
assert f"{target_base}.mp3" in exported, f"Missing {target_base}.mp3 in export"
assert f"{target_base}.srt" in exported, f"Missing {target_base}.srt in export"

print(f"\n🎉 Verified exported paired files: {target_base}.mp3 and {target_base}.srt", flush=True)
print('🎉 ALL TESTS PASSED WITH 100% SUCCESS!', flush=True)

