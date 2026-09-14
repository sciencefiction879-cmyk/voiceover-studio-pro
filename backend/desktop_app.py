#!/usr/bin/env python3
"""
Voiceover Studio Pro - Native Desktop Application Runner (macOS WebKit)
"""

import os
import sys
import time
import socket
import threading
import urllib.request
import webbrowser

# Add backend directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from server import app

def find_free_port(default_port=5055):
    """Check if default port is free or find an alternative."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', default_port))
            return default_port
        except OSError:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

def run_server(port):
    """Run Flask API and static file server."""
    # Suppress verbose development server logs
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)

def wait_for_server(url, timeout=10.0):
    """Wait until the Flask server responds."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False

def main():
    port = find_free_port(5055)
    server_url = f"http://127.0.0.1:{port}"

    print(f"🎙️  Starting Voiceover Studio Pro Desktop Backend on {server_url}...")
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(server_url):
        print("⚠️ Server initialization timed out, launching interface anyway...")

    # Launch native WebKit desktop window
    try:
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication, NSApplicationActivationPolicyRegular
                app_instance = NSApplication.sharedApplication()
                app_instance.setActivationPolicy_(NSApplicationActivationPolicyRegular)
                app_instance.activateIgnoringOtherApps_(True)
            except Exception as e_cocoa:
                print(f"Cocoa activation notice: {e_cocoa}", flush=True)

        import webview
        print("🚀 Launching native macOS WebKit window...", flush=True)
        window = webview.create_window(
            title="Voiceover Studio Pro",
            url=server_url,
            width=1360,
            height=900,
            min_size=(1080, 720),
            background_color='#0a0c10',
            text_select=True,
            confirm_close=False
        )
        webview.start(debug=False)
    except Exception as e:
        print(f"Native window error: {e}", flush=True)
        # Keep process alive without forcing web browser opening
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
