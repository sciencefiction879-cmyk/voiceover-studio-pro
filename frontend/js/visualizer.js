/**
 * Voiceover Studio Pro - Waveform Visualizer & Cut Timeline Inspector
 */

class VisualizerManager {
  constructor() {
    this.canvas = document.getElementById("waveformCanvas");
    this.ctx = this.canvas ? this.canvas.getContext("2d") : null;
    this.scrubber = document.getElementById("playbackScrubber");
    this.timelineTrack = document.getElementById("cutTimelineTrack");
    this.waveformPeaks = [];
    this.currentDuration = 0;
    this.currentTime = 0;
    this.isPlaying = false;
    this.animationFrame = null;

    this.initCanvas();
    this.bindCanvasEvents();
  }

  initCanvas() {
    if (!this.canvas) return;
    this.resizeCanvas();
    window.addEventListener("resize", () => this.resizeCanvas());
  }

  resizeCanvas() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = rect.width * window.devicePixelRatio;
    this.canvas.height = rect.height * window.devicePixelRatio;
    this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    this.drawWaveform();
  }

  bindCanvasEvents() {
    if (!this.canvas) return;
    const wrap = this.canvas.parentElement;
    wrap.addEventListener("click", (e) => {
      if (!this.currentDuration) return;
      const rect = wrap.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const percent = Math.max(0, Math.min(1, clickX / rect.width));
      const targetTime = percent * this.currentDuration;
      if (window.App && window.App.audioElement) {
        window.App.audioElement.currentTime = targetTime;
        this.updatePlayhead(targetTime, this.currentDuration);
      }
    });
  }

  generateSyntheticWaveform(duration) {
    this.currentDuration = duration || 60;
    const numBars = 120;
    this.waveformPeaks = [];
    
    // Generate organic voiceover-style amplitude envelope
    let prev = 0.4;
    for (let i = 0; i < numBars; i++) {
      const isPause = Math.random() < 0.15;
      if (isPause) {
        prev = 0.05 + Math.random() * 0.08;
      } else {
        const delta = (Math.random() - 0.48) * 0.35;
        prev = Math.max(0.15, Math.min(0.95, prev + delta));
      }
      this.waveformPeaks.push(prev);
    }
    this.drawWaveform();
  }

  drawWaveform() {
    if (!this.ctx || !this.canvas) return;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    const ctx = this.ctx;

    ctx.clearRect(0, 0, w, h);

    if (this.waveformPeaks.length === 0) {
      // Draw idle placeholder grid
      ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, h / 2);
      ctx.lineTo(w, h / 2);
      ctx.stroke();
      return;
    }

    const barWidth = w / this.waveformPeaks.length;
    const progressPercent = this.currentDuration > 0 ? (this.currentTime / this.currentDuration) : 0;
    const currentBarIdx = Math.floor(progressPercent * this.waveformPeaks.length);

    // Waveform gradients
    const gradPlayed = ctx.createLinearGradient(0, 0, 0, h);
    gradPlayed.addColorStop(0, "#06b6d4");
    gradPlayed.addColorStop(0.5, "#38bdf8");
    gradPlayed.addColorStop(1, "#0284c7");

    const gradUnplayed = ctx.createLinearGradient(0, 0, 0, h);
    gradUnplayed.addColorStop(0, "rgba(255, 255, 255, 0.4)");
    gradUnplayed.addColorStop(1, "rgba(255, 255, 255, 0.1)");

    for (let i = 0; i < this.waveformPeaks.length; i++) {
      const peak = this.waveformPeaks[i];
      const barH = Math.max(4, peak * (h - 16));
      const x = i * barWidth + 1;
      const y = (h - barH) / 2;

      ctx.fillStyle = (i <= currentBarIdx) ? gradPlayed : gradUnplayed;
      ctx.beginPath();
      ctx.roundRect(x, y, Math.max(1.5, barWidth - 1.5), barH, 2);
      ctx.fill();
    }
  }

  updatePlayhead(currentTime, duration) {
    this.currentTime = currentTime;
    this.currentDuration = duration || this.currentDuration || 1;
    const percent = Math.min(100, Math.max(0, (this.currentTime / this.currentDuration) * 100));
    
    if (this.scrubber) {
      this.scrubber.style.left = `${percent}%`;
    }
    this.drawWaveform();
  }

  /**
   * Render interactive Cut Map Timeline
   */
  renderCutTimeline(duration, cuts = []) {
    if (!this.timelineTrack) return;
    this.timelineTrack.innerHTML = "";

    if (!duration || duration <= 0) {
      this.timelineTrack.innerHTML = `
        <div class="timeline-legend" style="margin: auto;">
          <span class="legend-item"><span class="legend-box safe"></span> 0-25m Protected</span>
          <span class="legend-item"><span class="legend-box cut"></span> ~1m Cuts (After 25m)</span>
        </div>`;
      return;
    }

    const protectLimit = Math.min(duration, 1500.0); // 25 mins = 1500s
    const safePercent = (protectLimit / duration) * 100;

    // 1. Safe Zone (0 - 25m)
    const safeDiv = document.createElement("div");
    safeDiv.className = "timeline-segment safe-zone";
    safeDiv.style.left = "0%";
    safeDiv.style.width = `${safePercent}%`;
    safeDiv.title = `0:00 – ${this.formatTime(protectLimit)} (Protected 0-25m: 0 Cuts)`;
    this.timelineTrack.appendChild(safeDiv);

    // 2. Cut Segments (> 25m)
    cuts.forEach((cut, idx) => {
      const cutStartPercent = (cut.start / duration) * 100;
      const cutWidthPercent = (cut.duration / duration) * 100;

      const cutDiv = document.createElement("div");
      cutDiv.className = "timeline-segment cut-zone";
      cutDiv.style.left = `${cutStartPercent}%`;
      cutDiv.style.width = `${Math.max(1.2, cutWidthPercent)}%`;
      cutDiv.title = `Cut #${idx + 1}: ${cut.formattedStart} – ${cut.formattedEnd} (${cut.duration}s cut)`;
      this.timelineTrack.appendChild(cutDiv);
    });

    const badge = document.getElementById("cutSummaryBadge");
    if (badge) {
      badge.textContent = cuts.length > 0 ? `${cuts.length} Cuts Scheduled` : `0 Cuts (Under 25m)`;
    }
  }

  formatTime(secs) {
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  }
}

window.Visualizer = new VisualizerManager();
