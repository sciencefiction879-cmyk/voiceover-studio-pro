#!/usr/bin/env python3
"""
Generate macOS AppIcon.icns using Python and iconutil.
"""

import os
import subprocess
import shutil
import math

def create_iconset():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(script_dir, "..", "assets")
    iconset_dir = os.path.join(assets_dir, "AppIcon.iconset")
    os.makedirs(iconset_dir, exist_ok=True)

    # Base SVG of the studio microphone + audio waves + dark glass glow
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <!-- Background Gradient -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0a0c16" />
      <stop offset="50%" stop-color="#121626" />
      <stop offset="100%" stop-color="#090b14" />
    </linearGradient>

    <!-- Glowing Rim Gradient -->
    <linearGradient id="rimGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#6366f1" />
      <stop offset="50%" stop-color="#06b6d4" />
      <stop offset="100%" stop-color="#10b981" />
    </linearGradient>

    <!-- Metallic Mic Gradient -->
    <linearGradient id="micGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#6366f1" />
      <stop offset="100%" stop-color="#4f46e5" />
    </linearGradient>

    <!-- Wave Glow -->
    <filter id="cyanGlow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="24" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>

    <filter id="cardShadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="18" stdDeviation="30" flood-color="#000000" flood-opacity="0.8" />
    </filter>
  </defs>

  <!-- App Rounded Squircle Background -->
  <rect x="64" y="64" width="896" height="896" rx="200" fill="url(#bgGrad)" stroke="url(#rimGrad)" stroke-width="12" filter="url(#cardShadow)" />

  <!-- Ambient Glow Disk -->
  <circle cx="512" cy="512" r="320" fill="#06b6d4" opacity="0.12" filter="url(#cyanGlow)" />
  <circle cx="512" cy="460" r="220" fill="#6366f1" opacity="0.18" filter="url(#cyanGlow)" />

  <!-- Waveform Arc Lines -->
  <!-- Left Waves -->
  <path d="M 270 420 A 180 180 0 0 0 270 604" fill="none" stroke="#06b6d4" stroke-width="18" stroke-linecap="round" opacity="0.7" />
  <path d="M 200 360 A 260 260 0 0 0 200 664" fill="none" stroke="#6366f1" stroke-width="18" stroke-linecap="round" opacity="0.45" />

  <!-- Right Waves -->
  <path d="M 754 420 A 180 180 0 0 1 754 604" fill="none" stroke="#06b6d4" stroke-width="18" stroke-linecap="round" opacity="0.7" />
  <path d="M 824 360 A 260 260 0 0 1 824 664" fill="none" stroke="#6366f1" stroke-width="18" stroke-linecap="round" opacity="0.45" />

  <!-- Center Microphone Body -->
  <!-- Capsule Top -->
  <rect x="420" y="270" width="184" height="310" rx="92" fill="url(#micGrad)" filter="url(#cardShadow)" />

  <!-- Mesh Grille Lines -->
  <line x1="435" y1="360" x2="589" y2="360" stroke="#ffffff" stroke-width="8" opacity="0.4" stroke-linecap="round" />
  <line x1="435" y1="410" x2="589" y2="410" stroke="#ffffff" stroke-width="8" opacity="0.4" stroke-linecap="round" />
  <line x1="435" y1="460" x2="589" y2="460" stroke="#ffffff" stroke-width="8" opacity="0.4" stroke-linecap="round" />
  <line x1="512" y1="285" x2="512" y2="565" stroke="#ffffff" stroke-width="8" opacity="0.3" stroke-linecap="round" />

  <!-- Outer Stand Cradle U-Bar -->
  <path d="M 360 480 C 360 630, 664 630, 664 480" fill="none" stroke="#e2e8f0" stroke-width="22" stroke-linecap="round" />

  <!-- Base Neck & Stand Foot -->
  <line x1="512" y1="595" x2="512" y2="720" stroke="#e2e8f0" stroke-width="22" stroke-linecap="round" />
  <line x1="390" y1="720" x2="634" y2="720" stroke="#e2e8f0" stroke-width="24" stroke-linecap="round" />

  <!-- Glowing Sound LED -->
  <circle cx="512" cy="520" r="14" fill="#06b6d4" filter="url(#cyanGlow)" />
  <circle cx="512" cy="520" r="8" fill="#ffffff" />
</svg>'''

    svg_path = os.path.join(assets_dir, "icon.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)

    png_1024 = os.path.join(assets_dir, "icon_1024.png")

    # Render SVG to 1024x1024 PNG using QuickLook / qlmanage or WebKit / Python / sips / resvg
    # On macOS, sips or qlmanage or python can render or we can use Python with Cairo/sips
    # QuickLook can render SVG to PNG: qlmanage -t -s 1024 -o <dir> <file.svg>
    cmd = ["qlmanage", "-t", "-s", "1024", "-o", assets_dir, svg_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rendered_ql = os.path.join(assets_dir, "icon.svg.png")

    if os.path.exists(rendered_ql):
        shutil.move(rendered_ql, png_1024)
    else:
        # Fallback using python PIL / Cocoa / webkit to render
        render_png_with_cocoa(svg_path, png_1024)

    # Standard macOS icon sizes
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    for size, name in sizes:
        dst = os.path.join(iconset_dir, name)
        subprocess.run(["sips", "-z", str(size), str(size), png_1024, "--out", dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    icns_path = os.path.join(assets_dir, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"✅ Generated macOS Icon: {icns_path}")
    return icns_path

def render_png_with_cocoa(svg_path, out_png):
    script = f'''
    import Cocoa
    import WebKit

    url = Cocoa.NSURL.fileURLWithPath_("{svg_path}")
    img = Cocoa.NSImage.alloc().initWithContentsOfURL_(url)
    if img:
        img.setSize_(Cocoa.NSMakeSize(1024, 1024))
        cg_img = img.CGImageForProposedRect_context_hints_(None, None, None)
        rep = Cocoa.NSBitmapImageRep.alloc().initWithCGImage_(cg_img)
        png_data = rep.representationUsingType_properties_(Cocoa.NSBitmapImageFileTypePNG, None)
        png_data.writeToFile_atomically_("{out_png}", True)
    '''
    subprocess.run(["python3", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    create_iconset()
