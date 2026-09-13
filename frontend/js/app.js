/**
 * Voiceover Studio Pro v2.3 - Main Application Controller
 * - Individual Audio Selection Dropdown & Real Duration Inspector
 * - 3-at-a-Time Chunked Batch Processing with Manual Batch Size Selector (Default 3)
 * - Real Post-Processing Duration & Verification Engine
 * - Strict V1..Vn File Naming & Smooth Downloads
 */

class VoiceoverApp {
  constructor() {
    this.files = [];
    this.selectedFile = null;
    this.activeAudioType = "original"; // "original" or "processed" / "preview"
    this.audioElement = document.getElementById("audioElement");
    this.pollTimer = null;
    this.isProcessing = false;
    this.analytics = {};
    this.captionMode = "mode1"; // "mode1" (Process + Cut + SRT) or "mode2" (Alignment Only)

    this.init();
  }

  async init() {
    this.checkHealth();
    this.bindEvents();
    this.startPolling();
  }

  async checkHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      
      const ffmpegPill = document.getElementById("ffmpegStatusPill");
      if (ffmpegPill) {
        const dot = ffmpegPill.querySelector(".pill-dot");
        const txt = ffmpegPill.querySelector(".pill-text");
        if (data.ffmpeg && data.ffmpeg.available) {
          if (dot) dot.className = "pill-dot ok";
          if (txt) txt.textContent = "FFmpeg Ready";
        } else {
          if (dot) dot.className = "pill-dot";
          if (txt) txt.textContent = "FFmpeg Missing";
        }
      }
    } catch (e) {
      console.warn("Health check notice:", e);
    }
  }

  bindEvents() {
    // 1. Source Folder Browse & Scan
    const btnBrowseSource = document.getElementById("btnBrowseSource");
    if (btnBrowseSource) {
      btnBrowseSource.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/browse-folder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: "Select folder containing voiceovers (V1.mp3, V2.mp3...)" })
          });
          const data = await res.json();
          if (data.success && data.path) {
            const pathInput = document.getElementById("sourceFolderPath");
            if (pathInput) pathInput.value = data.path;
            this.scanFolder(data.path);
          }
        } catch (e) {
          this.addLog(`Browse notice: ${e.message}`, "error");
        }
      });
    }

    const btnScanFolder = document.getElementById("btnScanFolder");
    if (btnScanFolder) {
      btnScanFolder.addEventListener("click", () => {
        const pathInput = document.getElementById("sourceFolderPath");
        const p = pathInput ? pathInput.value.trim() : "";
        if (p) this.scanFolder(p);
      });
    }

    // 2. Browser Folder/File Upload & Dropzone
    const fileInput = document.getElementById("browserFileInput");
    const filesOnlyInput = document.getElementById("browserFilesOnlyInput");
    const btnUploadFolder = document.getElementById("btnUploadFolder");
    const btnUploadFilesOnly = document.getElementById("btnUploadFilesOnly");
    const dropzone = document.getElementById("fileDropzone");

    if (btnUploadFolder && fileInput) {
      btnUploadFolder.addEventListener("click", (e) => {
        e.stopPropagation();
        fileInput.click();
      });
    }

    if (btnUploadFilesOnly && filesOnlyInput) {
      btnUploadFilesOnly.addEventListener("click", (e) => {
        e.stopPropagation();
        filesOnlyInput.click();
      });
    }

    if (filesOnlyInput) {
      filesOnlyInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          this.uploadFiles(e.target.files);
        }
      });
    }

    if (dropzone && fileInput) {
      dropzone.addEventListener("click", () => fileInput.click());

      dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      });

      dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
      });

      dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
          this.uploadFiles(e.dataTransfer.files);
        }
      });

      fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
          this.uploadFiles(e.target.files);
        }
      });
    }

    // 3. Audio Selection Dropdown
    const selectAudioDropdown = document.getElementById("selectAudioDropdown");
    if (selectAudioDropdown) {
      selectAudioDropdown.addEventListener("change", (e) => {
        const selectedId = e.target.value;
        const found = this.files.find(f => f.id === selectedId);
        if (found) {
          this.selectFile(found);
        }
      });
    }

    // 3b. Caption System Two-Mode Switch
    const optionMode1 = document.getElementById("optionMode1");
    const optionMode2 = document.getElementById("optionMode2");
    const radioMode1 = document.getElementById("radioMode1");
    const radioMode2 = document.getElementById("radioMode2");

    if (optionMode1) {
      optionMode1.addEventListener("click", () => this.setCaptionMode("mode1"));
    }
    if (optionMode2) {
      optionMode2.addEventListener("click", () => this.setCaptionMode("mode2"));
    }
    if (radioMode1) {
      radioMode1.addEventListener("change", () => this.setCaptionMode("mode1"));
    }
    if (radioMode2) {
      radioMode2.addEventListener("change", () => this.setCaptionMode("mode2"));
    }

    // 4. Audio Player Controls & A/B Switch
    const btnPlayPause = document.getElementById("btnPlayPause");
    const btnStop = document.getElementById("btnStop");
    const btnReplay = document.getElementById("btnReplay");
    const btnPlayOriginal = document.getElementById("btnPlayOriginal");
    const btnPlayProcessed = document.getElementById("btnPlayProcessed");
    const playerVolume = document.getElementById("playerVolume");

    if (btnPlayPause) {
      btnPlayPause.addEventListener("click", () => this.togglePlayback());
    }

    if (btnStop) {
      btnStop.addEventListener("click", () => this.stopPlayback());
    }

    if (btnReplay) {
      btnReplay.addEventListener("click", () => this.replayPlayback());
    }

    if (btnPlayOriginal) {
      btnPlayOriginal.addEventListener("click", () => {
        this.activeAudioType = "original";
        btnPlayOriginal.classList.add("active");
        if (btnPlayProcessed) btnPlayProcessed.classList.remove("active");
        this.loadAudioSource();
      });
    }

    if (btnPlayProcessed) {
      btnPlayProcessed.addEventListener("click", () => {
        if (!this.selectedFile?.hasProcessed && !this.selectedFile?.previewUrl) {
          this.addLog("This audio has not been processed yet. Click 'Test Preview Selected File' or 'Process All Voiceovers' first.", "warning");
          return;
        }
        this.activeAudioType = "processed";
        btnPlayProcessed.classList.add("active");
        if (btnPlayOriginal) btnPlayOriginal.classList.remove("active");
        this.loadAudioSource();
      });
    }

    if (playerVolume && this.audioElement) {
      playerVolume.addEventListener("input", (e) => {
        this.audioElement.volume = parseFloat(e.target.value);
      });
    }

    if (this.audioElement) {
      this.audioElement.addEventListener("timeupdate", () => {
        const cur = this.audioElement.currentTime;
        const dur = this.audioElement.duration || (this.activeAudioType === "processed" && this.selectedFile?.finalDuration ? this.selectedFile.finalDuration : this.selectedFile?.duration) || 0;
        const timeDisp = document.getElementById("timeDisplay");
        if (timeDisp) timeDisp.textContent = `${this.formatTime(cur)} / ${this.formatTime(dur)}`;
        if (window.Visualizer) {
          window.Visualizer.updatePlayhead(cur, dur);
        }
      });

      this.audioElement.addEventListener("ended", () => {
        this.setPlayingState(false);
      });
    }

    // 5. Test Preview Button
    const btnTestPreview = document.getElementById("btnTestPreview");
    if (btnTestPreview) {
      btnTestPreview.addEventListener("click", () => this.generateTestPreview());
    }

    // 6. Process Batch Button
    const btnProcessBatch = document.getElementById("btnProcessBatch");
    if (btnProcessBatch) {
      btnProcessBatch.addEventListener("click", () => this.startBatchProcessing());
    }

    // 7. Destination Folder Browse & Export
    const btnBrowseExport = document.getElementById("btnBrowseExport");
    if (btnBrowseExport) {
      btnBrowseExport.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/browse-folder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: "Select Destination Export Folder" })
          });
          const data = await res.json();
          if (data.success && data.path) {
            const expInput = document.getElementById("exportDestinationPath");
            if (expInput) expInput.value = data.path;
          }
        } catch (e) {
          this.addLog(`Browse destination notice: ${e.message}`, "error");
        }
      });
    }

    const btnExportAll = document.getElementById("btnExportAll") || document.getElementById("btnExportMp3");
    if (btnExportAll) {
      btnExportAll.addEventListener("click", () => this.exportFiles());
    }

    const btnExportSrts = document.getElementById("btnExportSrts");
    if (btnExportSrts) {
      btnExportSrts.addEventListener("click", () => this.exportSrtsOnly());
    }

    const btnDownloadAudioZip = document.getElementById("btnDownloadAudioZip");
    if (btnDownloadAudioZip) {
      btnDownloadAudioZip.addEventListener("click", () => {
        this.addLog("Initiating ZIP download of processed MP3 audio files...", "info");
        window.location.href = `/api/download-audio-zip`;
      });
    }

    const btnDownloadCaptionsZip = document.getElementById("btnDownloadCaptionsZip");
    if (btnDownloadCaptionsZip) {
      btnDownloadCaptionsZip.addEventListener("click", () => {
        this.addLog("Initiating ZIP download of synchronized SRT captions...", "info");
        window.location.href = `/api/download-captions-zip`;
      });
    }

    const btnDownloadZip = document.getElementById("btnDownloadZip");
    if (btnDownloadZip) {
      btnDownloadZip.addEventListener("click", () => {
        this.addLog("Initiating complete bundle ZIP download (Audio + Captions)...", "info");
        window.location.href = `/api/download-zip`;
      });
    }

    // 8. Log clear & Copy
    const btnClearLogs = document.getElementById("btnClearLogs");
    if (btnClearLogs) {
      btnClearLogs.addEventListener("click", () => {
        const terminal = document.getElementById("terminalLogs");
        if (terminal) terminal.innerHTML = "";
      });
    }

    const btnCopyLogs = document.getElementById("btnCopyLogs");
    if (btnCopyLogs) {
      btnCopyLogs.addEventListener("click", () => this.copyLogsToClipboard());
    }
  }

  copyLogsToClipboard() {
    const terminal = document.getElementById("terminalLogs");
    if (!terminal) return;
    const text = terminal.innerText;
    navigator.clipboard.writeText(text).then(() => {
      this.addLog("📋 Console logs copied to clipboard!", "success");
    }).catch(() => {
      this.addLog("Failed to copy logs to clipboard.", "warning");
    });
  }

  setCaptionMode(mode) {
    this.captionMode = mode === "mode2" ? "mode2" : "mode1";
    const opt1 = document.getElementById("optionMode1");
    const opt2 = document.getElementById("optionMode2");
    const rad1 = document.getElementById("radioMode1");
    const rad2 = document.getElementById("radioMode2");
    const indicator = document.getElementById("modeActiveIndicator");
    const banner = document.getElementById("mode2NoticeBanner");
    const btnBatch = document.getElementById("btnProcessBatch");

    const dspTitle = document.getElementById("dspPanelTitle");
    const actionsTitle = document.getElementById("actionsPanelTitle");
    const masterActions = document.getElementById("masterQuickActions");
    const masterV1Tag = document.getElementById("masterV1Tag");
    const mode1Controls = document.getElementById("mode1ControlsContainer");
    const mode2Studio = document.getElementById("mode2StudioContainer");

    const mode1OnlyElements = document.querySelectorAll('[data-mode1-only="true"]');

    if (this.captionMode === "mode2") {
      if (opt1) opt1.classList.remove("active");
      if (opt2) {
        opt2.classList.add("active");
        opt2.classList.add("mode2-active");
      }
      if (rad1) rad1.checked = false;
      if (rad2) rad2.checked = true;
      if (indicator) {
        indicator.textContent = "Mode 2 Active: Forced Alignment Only (Audio Untouched)";
        indicator.style.color = "#67e8f9";
      }
      if (banner) banner.style.display = "flex";

      // HIDE ALL MODE 1 AUDIO DSP & CUT CONTROLS
      if (dspTitle) dspTitle.textContent = "Forced Alignment & Subtitle Studio";
      if (actionsTitle) actionsTitle.textContent = "Preview & Alignment";
      if (masterActions) masterActions.style.display = "none";
      if (masterV1Tag) masterV1Tag.style.display = "none";
      if (mode1Controls) mode1Controls.style.display = "none";
      if (mode2Studio) mode2Studio.style.display = "flex";

      // Hide all Mode 1-only UI elements
      mode1OnlyElements.forEach(el => el.style.display = "none");

      if (btnBatch) {
        btnBatch.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          <span>🚀 Generate Synced SRTs (Alignment Only)</span>`;
      }
      this.addLog("Active Mode: MODE 2 (Alignment Only) — Generates SRTs from speech timing. Audio is untouched (no cuts, no DSP).", "info");
    } else {
      if (opt1) opt1.classList.add("active");
      if (opt2) {
        opt2.classList.remove("active");
        opt2.classList.remove("mode2-active");
      }
      if (rad1) rad1.checked = true;
      if (rad2) rad2.checked = false;
      if (indicator) {
        indicator.textContent = "Mode 1 Active: Audio Processing + Cut-Sync SRT";
        indicator.style.color = "var(--accent-emerald)";
      }
      if (banner) banner.style.display = "none";

      // SHOW MODE 1 AUDIO DSP & CUT CONTROLS
      if (dspTitle) dspTitle.textContent = "Audio DSP & Cut Engine";
      if (actionsTitle) actionsTitle.textContent = "Preview & Processing";
      if (masterActions) masterActions.style.display = "flex";
      if (masterV1Tag) masterV1Tag.style.display = "inline-block";
      if (mode1Controls) mode1Controls.style.display = "block";
      if (mode2Studio) mode2Studio.style.display = "none";

      // Restore all Mode 1-only UI elements
      mode1OnlyElements.forEach(el => el.style.display = "");

      if (btnBatch) {
        btnBatch.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          <span>⚡ Process All Voiceovers &amp; Synced SRTs (Mode 1)</span>`;
      }
      this.addLog("Active Mode: MODE 1 (Process + Cut + SRT) — Full voiceover processing with identical cuts applied to SRT captions.", "info");
    }

    // Sync mode with server
    fetch("/api/set-caption-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: this.captionMode })
    }).catch(e => console.warn(e));

    this.updateButtonsState();
    this.renderFileList();
    if (this.selectedFile) {
      this.updateInspectorCard(this.selectedFile);
    }
  }

  async scanFolder(folderPath) {
    this.addLog(`Scanning folder: ${folderPath}...`, "info");
    try {
      const res = await fetch("/api/scan-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_path: folderPath })
      });
      const data = await res.json();
      if (data.success) {
        this.files = data.files || [];
        this.analytics = data.analytics || {};
        this.populateAudioDropdown();
        this.renderFileList();
        this.updateMasterV1Label();
        this.updateAnalyticsUI(this.analytics);
        this.addLog(`Loaded ${data.count} voiceovers. V1 Master assigned.`, "success");
        if (this.files.length > 0) {
          this.selectFile(this.files[0]);
        }
      } else {
        this.addLog(`Scan failed: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Scan error: ${e.message}`, "error");
    }
  }

  async uploadFiles(fileList) {
    const formData = new FormData();
    for (let i = 0; i < fileList.length; i++) {
      formData.append("files", fileList[i]);
    }

    this.addLog(`Uploading ${fileList.length} files...`, "info");
    try {
      const res = await fetch("/api/upload-files", {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      if (data.success) {
        this.files = data.files || [];
        this.analytics = data.analytics || {};
        this.populateAudioDropdown();
        this.renderFileList();
        this.updateMasterV1Label();
        this.updateAnalyticsUI(this.analytics);
        this.addLog(`Indexed ${data.count} voiceovers. V1 Master assigned.`, "success");
        if (this.files.length > 0) {
          this.selectFile(this.files[0]);
        }
      } else {
        this.addLog(`Upload error: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Upload network error: ${e.message}`, "error");
    }
  }

  populateAudioDropdown() {
    const select = document.getElementById("selectAudioDropdown");
    if (!select) return;

    if (this.files.length === 0) {
      select.innerHTML = `<option value="">No audio loaded</option>`;
      return;
    }

    select.innerHTML = this.files.map(f => {
      const tag = f.index === 1 ? "👑 " : "";
      const statusIcon = f.hasProcessed ? " [✓ Processed]" : "";
      return `<option value="${f.id}" ${this.selectedFile?.id === f.id ? 'selected' : ''}>${tag}${this.escapeHtml(f.fileName)} (${f.formattedDuration})${statusIcon}</option>`;
    }).join("");
  }

  updateMasterV1Label() {
    const masterNameEl = document.getElementById("masterFileName");
    if (masterNameEl && this.files.length > 0) {
      masterNameEl.textContent = this.files[0].fileName;
    }
  }

  renderFileList() {
    const container = document.getElementById("fileListContainer");
    const countBadge = document.getElementById("fileCountBadge");
    const durLabel = document.getElementById("queueTotalDuration") || document.getElementById("totalQueueDuration");

    if (!container) return;

    if (countBadge) {
      countBadge.textContent = `${this.files.length} files`;
    }
    container.innerHTML = "";

    if (this.files.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <p>No audio files loaded yet.</p>
          <small>Scan a directory or upload voiceovers to get started.</small>
        </div>`;
      if (durLabel) durLabel.textContent = "Total: 00:00";
      return;
    }

    let totalSecs = 0;

    this.files.forEach((f, idx) => {
      const effectiveDur = (f.hasProcessed && f.finalDuration) ? f.finalDuration : (f.duration || 0);
      const effectiveFormattedDur = (f.hasProcessed && f.formattedFinalDuration) ? f.formattedFinalDuration : (f.formattedDuration || '00:00');
      
      totalSecs += effectiveDur;
      const itemDiv = document.createElement("div");
      itemDiv.className = `file-item ${this.selectedFile?.id === f.id ? 'active' : ''}`;
      
      // Determine explicit status tag
      let statusClass = "ready";
      let statusText = "Ready";
      if (f.statusLabel) {
        statusText = f.statusLabel;
        if (statusText.includes("SUCCESS")) statusClass = "completed";
        else if (statusText.includes("ERROR")) statusClass = "error";
        else if (statusText.includes("PARTIAL")) statusClass = "partial";
        else if (statusText.includes("WARNING")) statusClass = "warning";
        else statusClass = "processing";
      } else if (f.status === "completed") {
        statusClass = "completed";
        statusText = "SUCCESS ✓";
      } else if (f.status === "error") {
        statusClass = "error";
        statusText = "ERROR ✕";
      } else if (f.status === "partial") {
        statusClass = "partial";
        statusText = "PARTIAL ⚠";
      } else if (f.status === "warning") {
        statusClass = "warning";
        statusText = "WARNING ⚠";
      } else if (f.status === "processing") {
        statusClass = "processing";
        statusText = "Processing...";
      }

      const masterTag = (idx === 0 && this.captionMode !== "mode2")
        ? `<span class="master-badge-v1">👑 MASTER V1</span>`
        : "";

      let scriptBadge = "";
      if (f.hasScript || f.hasSrt) {
        if (f.scriptType === '.docx') {
          scriptBadge = `<span class="srt-badge docx-badge" title="Word document script linked: ${this.escapeHtml(f.scriptFileName || '')}">📝 DOCX</span>`;
        } else if (f.scriptType === '.srt') {
          scriptBadge = `<span class="srt-badge" title="Matching subtitle linked: ${this.escapeHtml(f.scriptFileName || '')}">📝 SRT</span>`;
        } else {
          scriptBadge = `<span class="srt-badge txt-badge" title="Text script linked: ${this.escapeHtml(f.scriptFileName || '')}">📝 SCRIPT</span>`;
        }
      }

      // Meta row: In Mode 2, hide audio cuts
      let metaDetails = `<span>⏱️ ${effectiveFormattedDur}</span>`;
      if (this.captionMode !== "mode2") {
        if (f.hasProcessed && f.durationRemoved > 0) {
          metaDetails += `<span style="color:var(--accent-amber); font-size:0.68rem;">(-${f.formattedDurationRemoved || '0:00'})</span>`;
        }
        const cutTag = (f.hasCuts || f.cutCount > 0)
          ? `<span style="color:var(--accent-amber); font-weight:600;">✂️ ${f.cutCount} cuts</span>`
          : `<span style="color:var(--accent-emerald);">🛡️ 0 cuts</span>`;
        metaDetails += `<span>&bull;</span><span>${cutTag}</span>`;
      } else {
        metaDetails += `<span>&bull;</span><span class="text-cyan" style="font-size:0.68rem;">🛡️ Audio Untouched</span>`;
      }

      // Action Buttons: Download + Retry
      let downloadActions = "";
      if (f.status === "error" || f.status === "partial" || (f.errorDetails && f.errorDetails.canRetry)) {
        downloadActions += `<button class="btn-retry-queue" data-id="${f.id}" data-name="${this.escapeHtml(f.fileName)}" title="Retry ${this.escapeHtml(f.fileName)}">↻ Retry</button>`;
      }

      if (f.hasProcessed && this.captionMode !== "mode2" && f.captionMode !== "mode2") {
        const mp3Name = f.processedFileName || (f.fileName.replace(/\.[^.]+$/, '') + '.mp3');
        downloadActions += `<button class="btn-download-file btn-dl-mp3" data-filename="${this.escapeHtml(mp3Name)}" title="Download Processed MP3">⬇️ MP3</button>`;
      }
      if (f.hasProcessedSrt) {
        const srtName = f.processedSrtFileName || (f.fileName.replace(/\.[^.]+$/, '') + '.srt');
        downloadActions += `<button class="btn-download-file btn-dl-srt" data-srt="${this.escapeHtml(srtName)}" title="Download Synced SRT">⬇️ SRT</button>`;
      }

      itemDiv.innerHTML = `
        <div class="file-info">
          <span class="file-seq-tag">#${f.index}</span>
          <div class="file-name-meta">
            <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
              <span class="file-name">${this.escapeHtml(f.fileName)}</span>
              ${masterTag}
              ${scriptBadge}
            </div>
            <div class="file-meta-row">
              ${metaDetails}
            </div>
          </div>
        </div>
        <div class="file-item-right">
          <span class="file-status-tag ${statusClass}">${statusText}</span>
          ${downloadActions}
        </div>
      `;

      itemDiv.addEventListener("click", (e) => {
        if (e.target.classList.contains("btn-download-file") || e.target.classList.contains("btn-retry-queue")) return;
        this.selectFile(f);
      });

      const retryBtn = itemDiv.querySelector(".btn-retry-queue");
      if (retryBtn) {
        retryBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.retryItem(retryBtn.dataset.id, retryBtn.dataset.name);
        });
      }

      const dlMp3 = itemDiv.querySelector(".btn-dl-mp3");
      if (dlMp3) {
        dlMp3.addEventListener("click", (e) => {
          e.stopPropagation();
          this.downloadSingleFile(dlMp3.dataset.filename);
        });
      }

      const dlSrt = itemDiv.querySelector(".btn-dl-srt");
      if (dlSrt) {
        dlSrt.addEventListener("click", (e) => {
          e.stopPropagation();
          this.downloadSingleSrt(dlSrt.dataset.srt);
        });
      }

      container.appendChild(itemDiv);
    });

    if (durLabel) {
      durLabel.textContent = `Total: ${this.formatTime(totalSecs)}`;
    }
  }

  downloadSingleFile(fileName) {
    if (!fileName) return;
    this.addLog(`Downloading processed MP3: ${fileName}...`, "info");
    const link = document.createElement("a");
    link.href = `/api/download-single?name=${encodeURIComponent(fileName)}`;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  downloadSingleSrt(srtName) {
    if (!srtName) return;
    this.addLog(`Downloading synchronized subtitle: ${srtName}...`, "info");
    const link = document.createElement("a");
    link.href = `/api/download-single-srt?name=${encodeURIComponent(srtName)}`;
    link.download = srtName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  selectFile(file) {
    this.selectedFile = file;
    document.querySelectorAll(".file-item").forEach(item => item.classList.remove("active"));
    
    // Synchronize audio dropdown
    const select = document.getElementById("selectAudioDropdown");
    if (select && select.value !== file.id) {
      select.value = file.id;
    }

    const activeBadge = document.getElementById("activeFileBadge");
    if (activeBadge && file) {
      activeBadge.textContent = file.index === 1 ? `👑 ${file.fileName} (Master)` : file.fileName;
    }

    // Update Post-Processing & Validation Inspector Card
    this.updateInspectorCard(file);

    this.renderFileList();

    if (window.Visualizer && file) {
      const dur = (file.hasProcessed && file.finalDuration) ? file.finalDuration : file.duration;
      window.Visualizer.generateSyntheticWaveform(dur);
      const cutSchedule = file.cuts || this.simulateCuts(file.duration);
      window.Visualizer.renderCutTimeline(file.duration, cutSchedule);
    }

    this.loadAudioSource();
  }

  updateInspectorCard(file) {
    if (!file) return;

    const setTxt = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    setTxt("inspectorFileName", `${file.fileName}${file.index === 1 ? ' (Master V1)' : ''}`);
    setTxt("inspectorOrigDur", file.formattedDuration || '00:00');
    
    // Processed Duration (measured from disk after processing/preview)
    if (file.hasProcessed && file.formattedFinalDuration) {
      setTxt("inspectorProcDur", file.formattedFinalDuration);
    } else if (file.formattedPreviewDuration) {
      setTxt("inspectorProcDur", file.formattedPreviewDuration);
    } else {
      setTxt("inspectorProcDur", "--:--");
    }

    setTxt("inspectorCuts", `${file.cutCount || 0} cuts`);
    setTxt("inspectorRemoved", file.formattedDurationRemoved ? `-${file.formattedDurationRemoved}` : (file.durationRemoved > 0 ? `-${this.formatTime(file.durationRemoved)}` : '00:00'));

    const procStatus = file.statusLabel
      ? file.statusLabel
      : file.hasProcessed
      ? "SUCCESS ✓"
      : file.status === "processing" ? "Processing..." : file.status === "error" ? "ERROR ✕" : file.status === "partial" ? "PARTIAL ⚠" : "Not Processed";
    setTxt("inspectorProcessStatus", procStatus);

    const valStatus = (file.validation && file.validation.status)
      ? (file.validation.status === "SUCCESS" ? "✓ SUCCESS" : file.validation.status)
      : (file.hasProcessed || file.hasProcessedSrt ? "✓ Verified" : file.status === "error" ? "ERROR ✕" : "Pending");
    setTxt("inspectorValidationStatus", valStatus);

    // Linked Script
    const scriptEl = document.getElementById("inspectorScript");
    if (scriptEl) {
      if (file.hasScript && file.scriptFileName) {
        scriptEl.textContent = `${file.scriptFileName}`;
        scriptEl.className = "val text-amber";
      } else if (file.hasSrt && file.srtFileName) {
        scriptEl.textContent = `${file.srtFileName}`;
        scriptEl.className = "val text-amber";
      } else {
        scriptEl.textContent = "None";
        scriptEl.className = "val";
      }
    }

    // Caption Sync & Validation
    const capEl = document.getElementById("inspectorCaptionStatus");
    if (capEl) {
      if (file.hasProcessedSrt) {
        const countTxt = file.captionCuesCount ? ` (${file.captionCuesCount} cues)` : '';
        capEl.textContent = `✓ Synced${countTxt}`;
        capEl.className = "val text-success";
      } else if (file.captionInfo && file.captionInfo.success) {
        capEl.textContent = `✓ Preview Synced (${file.captionInfo.cuesCount} cues)`;
        capEl.className = "val text-success";
      } else if (file.hasScript || file.hasSrt) {
        capEl.textContent = "Ready for alignment";
        capEl.className = "val text-amber";
      } else {
        capEl.textContent = "No script provided";
        capEl.className = "val text-muted";
      }
    }

    const badge = document.getElementById("inspectorStatusBadge");
    if (badge) {
      if (file.status === "error") {
        badge.textContent = "✕ Error";
        badge.className = "inspector-status-badge text-danger";
      } else if (file.status === "partial") {
        badge.textContent = "⚠ Partial";
        badge.className = "inspector-status-badge text-amber";
      } else if (file.hasProcessed || file.hasProcessedSrt) {
        badge.textContent = "✓ Verified";
        badge.className = "inspector-status-badge text-success";
      } else if (file.index === 1 && this.captionMode !== "mode2") {
        badge.textContent = "👑 Master Reference";
        badge.className = "inspector-status-badge text-amber";
      } else {
        badge.textContent = "Ready";
        badge.className = "inspector-status-badge";
      }
    }

    // Structured Error Diagnostic Box
    const errBox = document.getElementById("inspectorErrorBox");
    if (file.status === "error" || file.status === "partial" || file.errorDetails) {
      if (errBox) {
        errBox.style.display = "flex";
        const diagBadge = document.getElementById("errorDiagBadge");
        const diagTarget = document.getElementById("errorDiagTarget");
        const diagReason = document.getElementById("errorDiagReason");
        const diagSolution = document.getElementById("errorDiagSolution");
        const btnRetry = document.getElementById("btnRetryDiag");

        const err = file.errorDetails || {};
        if (diagBadge) {
          diagBadge.textContent = err.category || (file.status === "partial" ? "PARTIAL ⚠" : "ERROR ✕");
          diagBadge.className = file.status === "partial" ? "error-diag-badge warning-badge" : "error-diag-badge";
        }
        if (diagTarget) {
          diagTarget.textContent = err.targetFile || file.fileName;
        }
        if (diagReason) {
          diagReason.textContent = err.reason || file.errorMessage || "Operation failed.";
        }
        if (diagSolution) {
          diagSolution.textContent = err.solution || "Check input files and retry.";
        }
        if (btnRetry) {
          btnRetry.onclick = (e) => {
            e.stopPropagation();
            this.retryItem(file.id, file.fileName);
          };
        }
      }
    } else {
      if (errBox) errBox.style.display = "none";
    }

    this.renderSrtPreview(file);
  }

  async renderSrtPreview(file) {
    const card = document.getElementById("srtPreviewCard");
    const scroller = document.getElementById("srtCuesScroller");
    const cueBadge = document.getElementById("srtPreviewCueCount");
    const btnCopy = document.getElementById("btnCopySrtPreview");
    const btnDl = document.getElementById("btnDownloadCurrentSrt");

    if (!card || !scroller) return;

    if (!file || (!file.hasProcessedSrt && file.scriptType !== ".srt")) {
      scroller.innerHTML = `<div class="empty-srt-state">No subtitles generated yet. Run alignment or processing to preview synced cues.</div>`;
      if (cueBadge) cueBadge.textContent = "0 Cues";
      if (btnDl) btnDl.style.display = "none";
      return;
    }

    try {
      const srtName = file.processedSrtFileName || `${file.fileName.replace(/\.[^.]+$/, '')}.srt`;
      const res = await fetch(`/api/get-srt-cues?name=${encodeURIComponent(srtName)}`);
      const data = await res.json();
      if (data.success && data.cues && data.cues.length > 0) {
        if (cueBadge) cueBadge.textContent = `${data.cueCount} Cues`;
        if (btnDl) {
          btnDl.style.display = "inline-flex";
          btnDl.onclick = (e) => {
            e.stopPropagation();
            this.downloadSingleSrt(srtName);
          };
        }
        scroller.innerHTML = data.cues.map(c => `
          <div class="srt-cue-item">
            <span class="srt-cue-time">#${c.index} [${c.start} &rarr; ${c.end}]</span>
            <span class="srt-cue-text">${this.escapeHtml(c.text)}</span>
          </div>
        `).join("");

        if (btnCopy) {
          btnCopy.onclick = (e) => {
            e.stopPropagation();
            const fullText = data.cues.map(c => `${c.index}\n${c.start} --> ${c.end}\n${c.text}\n`).join("\n");
            navigator.clipboard.writeText(fullText);
            this.addLog(`Copied ${data.cueCount} subtitle cues to clipboard.`, "info");
          };
        }
      } else {
        scroller.innerHTML = `<div class="empty-srt-state">Subtitles are scheduled. Run Mode 1 or Mode 2 to generate cues.</div>`;
        if (cueBadge) cueBadge.textContent = "0 Cues";
        if (btnDl) btnDl.style.display = "none";
      }
    } catch (e) {
      scroller.innerHTML = `<div class="empty-srt-state">Could not load subtitle preview: ${this.escapeHtml(e.message)}</div>`;
    }
  }

  async retryItem(fileId, fileName) {
    this.addLog(`↻ Retrying ${fileName}...`, "info");
    
    // Find item and set status to processing
    const item = this.files.find(f => f.id === fileId || f.fileName === fileName);
    if (item) {
      item.status = "processing";
      item.statusLabel = "Retrying...";
      this.renderFileList();
      if (this.selectedFile?.id === item.id) {
        this.updateInspectorCard(item);
      }
    }

    try {
      const res = await fetch("/api/retry-item", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: fileId, file_name: fileName })
      });
      const data = await res.json();
      if (data.success) {
        const updated = data.file;
        const idx = this.files.findIndex(f => f.id === updated.id);
        if (idx !== -1) {
          this.files[idx] = updated;
        }
        if (this.selectedFile?.id === updated.id) {
          this.selectedFile = updated;
          this.updateInspectorCard(updated);
        }
        this.renderFileList();
        if (data.analytics) {
          this.analytics = data.analytics;
          this.updateAnalyticsUI(data.analytics);
        }
        this.addLog(`✓ ${fileName} successfully verified and recovered!`, "success");
      } else {
        const updated = data.file || item;
        if (updated) {
          const idx = this.files.findIndex(f => f.id === updated.id);
          if (idx !== -1) this.files[idx] = updated;
          if (this.selectedFile?.id === updated.id) {
            this.selectedFile = updated;
            this.updateInspectorCard(updated);
          }
        }
        this.renderFileList();
        if (data.analytics) {
          this.analytics = data.analytics;
          this.updateAnalyticsUI(data.analytics);
        }
        const err = data.errorDetails;
        if (err) {
          this.addLog(`${err.category}: ${err.reason} (${fileName})`, "error");
        } else {
          this.addLog(`Retry failed for ${fileName}: ${data.error || 'Check logs'}`, "error");
        }
      }
    } catch (e) {
      this.addLog(`Retry network error: ${e.message}`, "error");
      if (item) {
        item.status = "error";
        this.renderFileList();
      }
    }
  }

  async retryAllFailed() {
    const failed = this.files.filter(f => f.status === "error" || f.status === "partial");
    if (failed.length === 0) {
      this.addLog("No failed items to retry.", "info");
      return;
    }
    this.addLog(`Retrying ${failed.length} failed files...`, "info");
    for (const f of failed) {
      await this.retryItem(f.id, f.fileName);
    }
  }

  simulateCuts(duration) {
    if (!duration || duration <= 1500.0) return [];
    const cuts = [];
    let cur = 1500.0;
    while (cur < duration) {
      const blockEnd = Math.min(duration, cur + 300.0);
      if (blockEnd - cur >= 30.0) {
        const cutDur = 60.0;
        const cutStart = cur + 30.0;
        cuts.push({
          start: cutStart,
          end: cutStart + cutDur,
          duration: cutDur,
          formattedStart: this.formatTime(cutStart),
          formattedEnd: this.formatTime(cutStart + cutDur)
        });
      }
      cur += 300.0;
    }
    return cuts;
  }

  loadAudioSource() {
    if (!this.selectedFile || !this.audioElement) return;

    this.stopPlayback();

    let url = "";
    let durText = this.selectedFile.formattedDuration || "00:00";

    const btnOrig = document.getElementById("btnPlayOriginal");
    const btnProc = document.getElementById("btnPlayProcessed");

    if (this.activeAudioType === "processed" && this.selectedFile.hasProcessed && this.selectedFile.processedPath) {
      url = `/api/stream-audio?type=processed&name=${encodeURIComponent(this.selectedFile.processedFileName || this.selectedFile.fileName)}`;
      durText = this.selectedFile.formattedFinalDuration || durText;
    } else if (this.activeAudioType === "processed" && this.selectedFile.previewUrl) {
      url = this.selectedFile.previewUrl;
      durText = this.selectedFile.formattedPreviewDuration || this.selectedFile.formattedFinalDuration || durText;
    } else {
      url = `/api/stream-audio?type=file&path=${encodeURIComponent(this.selectedFile.filePath)}`;
      durText = this.selectedFile.formattedDuration || "00:00";
    }

    this.audioElement.src = url;
    const timeDisp = document.getElementById("timeDisplay");
    if (timeDisp) {
      timeDisp.textContent = `00:00 / ${durText}`;
    }
  }

  togglePlayback() {
    if (!this.audioElement || !this.audioElement.src) return;
    if (this.audioElement.paused) {
      this.audioElement.play().then(() => this.setPlayingState(true)).catch(e => console.warn(e));
    } else {
      this.audioElement.pause();
      this.setPlayingState(false);
    }
  }

  stopPlayback() {
    if (!this.audioElement) return;
    this.audioElement.pause();
    this.audioElement.currentTime = 0;
    this.setPlayingState(false);
    if (window.Visualizer) {
      const dur = (this.activeAudioType === "processed" && this.selectedFile?.finalDuration) ? this.selectedFile.finalDuration : (this.selectedFile?.duration || 1);
      window.Visualizer.updatePlayhead(0, dur);
    }
  }

  replayPlayback() {
    if (!this.audioElement) return;
    this.audioElement.currentTime = 0;
    this.audioElement.play().then(() => this.setPlayingState(true)).catch(e => console.warn(e));
  }

  setPlayingState(playing) {
    const playIcon = document.getElementById("playIcon");
    const pauseIcon = document.getElementById("pauseIcon");
    if (playIcon && pauseIcon) {
      playIcon.style.display = playing ? "none" : "block";
      pauseIcon.style.display = playing ? "block" : "none";
    }
  }

  async generateTestPreview() {
    if (!this.selectedFile) {
      this.addLog("Please select a voiceover file first.", "warning");
      return;
    }

    const btn = document.getElementById("btnTestPreview");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner"></span> Generating Preview...`;
    }

    const isV1 = this.selectedFile.index === 1 || this.selectedFile.isMaster;
    this.addLog(`Processing ${isV1 ? 'V1 Master' : this.selectedFile.fileName} Preview...`, "info");

    const settings = window.AudioDSP ? window.AudioDSP.getPayload() : {};

    try {
      const res = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_id: this.selectedFile.id,
          file_path: this.selectedFile.filePath,
          settings: settings,
          preview_duration: 60.0
        })
      });
      const data = await res.json();
      if (data.success) {
        this.selectedFile.previewUrl = data.previewUrl;
        this.selectedFile.formattedPreviewDuration = data.formattedPreviewDuration;
        this.selectedFile.validation = data.validation;
        this.activeAudioType = "processed";
        
        const btnProc = document.getElementById("btnPlayProcessed");
        const btnOrig = document.getElementById("btnPlayOriginal");
        if (btnProc) btnProc.classList.add("active");
        if (btnOrig) btnOrig.classList.remove("active");

        this.updateInspectorCard(this.selectedFile);
        this.loadAudioSource();
        this.addLog(`Preview ready for ${this.selectedFile.fileName}. Verified duration: ${data.formattedPreviewDuration}.`, "success");
        this.togglePlayback();
      } else {
        this.addLog(`Preview notice: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Preview network error: ${e.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>
          <span>Test Preview Selected File</span>`;
      }
    }
  }

  async startBatchProcessing() {
    if (this.files.length === 0) {
      this.addLog("No files in queue to process.", "warning");
      return;
    }

    const batchSizeSelect = document.getElementById("processBatchSizeSelect");
    const batchSize = batchSizeSelect ? parseInt(batchSizeSelect.value, 10) || 3 : 3;

    const settings = window.AudioDSP ? window.AudioDSP.getPayload() : {};
    this.addLog(`Starting chunked batch processing (${batchSize} audios at a time) for ${this.files.length} files...`, "info");

    try {
      const res = await fetch("/api/process-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          settings,
          batch_size: batchSize,
          caption_mode: this.captionMode
        })
      });
      const data = await res.json();
      if (data.success) {
        this.isProcessing = true;
        this.updateButtonsState();
      } else {
        this.addLog(`Batch start notice: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Batch error: ${e.message}`, "error");
    }
  }

  async exportFiles() {
    const expInput = document.getElementById("exportDestinationPath");
    const destPath = expInput ? expInput.value.trim() : "";
    if (!destPath) {
      this.addLog("Please select or enter an export destination folder.", "warning");
      return;
    }

    this.addLog(`Exporting processed MP3s & synchronized SRTs to ${destPath}...`, "info");
    try {
      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination_path: destPath })
      });
      const data = await res.json();
      if (data.success) {
        this.addLog(`✓ Exported ${data.exportedMp3Count} MP3s and ${data.exportedSrtCount || 0} SRTs with strict numbering to: ${destPath}`, "success");
      } else {
        this.addLog(`Export notice: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Export network error: ${e.message}`, "error");
    }
  }

  async exportSrtsOnly() {
    const expInput = document.getElementById("exportDestinationPath");
    const destPath = expInput ? expInput.value.trim() : "";
    if (!destPath) {
      this.addLog("Please select or enter an export destination folder.", "warning");
      return;
    }

    this.addLog(`Exporting synchronized SRT captions to ${destPath}...`, "info");
    try {
      const res = await fetch("/api/export-captions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination_path: destPath })
      });
      const data = await res.json();
      if (data.success) {
        this.addLog(`✓ Exported ${data.exportedCount || 0} synchronized SRT subtitles to: ${destPath}`, "success");
      } else {
        this.addLog(`Export captions notice: ${data.error}`, "error");
      }
    } catch (e) {
      this.addLog(`Export captions network error: ${e.message}`, "error");
    }
  }

  startPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);

    this.pollTimer = setInterval(async () => {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();

        this.isProcessing = data.isProcessing;
        this.updateProgress(data.progress, data.currentFile, data.currentBatch, data.totalBatches);

        if (data.files && data.files.length > 0) {
          this.files = data.files;
          this.renderFileList();
          this.populateAudioDropdown();
          
          if (this.selectedFile) {
            const updated = this.files.find(f => f.id === this.selectedFile.id);
            if (updated) {
              this.selectedFile = updated;
              this.updateInspectorCard(updated);
            }
          }
        }

        if (data.analytics) {
          this.analytics = data.analytics;
          this.updateAnalyticsUI(data.analytics);
        }

        if (data.logs) {
          this.renderLogs(data.logs);
        }

        this.updateButtonsState();
      } catch (e) {
        // quiet poll fail
      }
    }, 1200);
  }

  updateProgress(percent, currentFile, currentBatch, totalBatches) {
    const fill = document.getElementById("batchProgressBarFill");
    const status = document.getElementById("batchProgressStatus");
    const percentEl = document.getElementById("batchProgressPercent");

    if (fill) fill.style.width = `${percent}%`;
    if (percentEl) percentEl.textContent = `${percent}%`;
    if (status) {
      if (this.isProcessing) {
        if (currentBatch && totalBatches) {
          status.textContent = `Batch ${currentBatch} of ${totalBatches}: ${currentFile || 'processing...'}`;
        } else {
          status.textContent = currentFile ? `Processing: ${currentFile}` : "Processing batch...";
        }
      } else if (percent === 100) {
        status.textContent = "Batch Processing Complete ✓";
      } else {
        status.textContent = "Ready";
      }
    }
  }

  updateAnalyticsUI(analytics) {
    if (!analytics) return;
    const setTxt = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    setTxt("analyticsFiles", `${analytics.processedFiles || 0} / ${analytics.totalFiles || 0}`);
    setTxt("analyticsCompletion", `${analytics.completionPercent || 0}%`);
    setTxt("analyticsSuccessful", `${analytics.successfulCount || 0}`);
    setTxt("analyticsFailed", `${analytics.failedCount || 0}`);
    setTxt("analyticsOriginalDur", analytics.formattedTotalOriginal || "00:00");
    setTxt("analyticsProcessedDur", analytics.formattedTotalProcessed || "00:00");
    setTxt("analyticsRemovedDur", analytics.formattedTotalRemoved || "00:00");
    setTxt("analyticsOutputs", `${analytics.outputsGenerated || 0}`);

    const tag = document.getElementById("analyticsStatusTag");
    if (tag) {
      if (this.isProcessing) {
        tag.textContent = analytics.currentBatch ? `Batch ${analytics.currentBatch}/${analytics.totalBatches}` : "Processing...";
        tag.className = "analytics-status-tag text-amber";
      } else if (analytics.isCompleted) {
        tag.textContent = "Complete ✓";
        tag.className = "analytics-status-tag text-success";
      } else {
        tag.textContent = "Ready";
        tag.className = "analytics-status-tag";
      }
    }

    this.updateFinalReport(analytics);
  }

  updateFinalReport(analytics) {
    const reportCard = document.getElementById("finalReportCard");
    if (!reportCard) return;

    const report = analytics?.finalReport;
    if (!report || !report.lines || (analytics.processedFiles === 0 && !analytics.isCompleted)) {
      reportCard.style.display = "none";
      return;
    }

    reportCard.style.display = "flex";
    const title = document.getElementById("finalReportTitle");
    const badge = document.getElementById("finalReportBadge");
    const linesContainer = document.getElementById("finalReportLines");
    const actions = document.getElementById("finalReportActions");
    const badgeCount = document.getElementById("failedCountBadge");

    if (title) {
      title.textContent = report.mode === "mode2" ? "Mode 2: Final Alignment Report" : "Mode 1: Final Processing & Caption Report";
    }

    if (badge) {
      if (report.failed > 0) {
        badge.textContent = `⚠ ${report.failed} Failed`;
        badge.style.background = "rgba(239, 68, 68, 0.25)";
        badge.style.color = "#fca5a5";
        badge.style.borderColor = "rgba(239, 68, 68, 0.45)";
      } else {
        badge.textContent = "✓ Verified Success";
        badge.style.background = "rgba(16, 185, 129, 0.2)";
        badge.style.color = "#6ee7b7";
        badge.style.borderColor = "rgba(16, 185, 129, 0.4)";
      }
    }

    if (linesContainer) {
      linesContainer.innerHTML = report.lines.map(line => {
        const parts = line.split(":");
        const k = parts[0].trim();
        const v = parts.slice(1).join(":").trim();
        return `
          <div class="report-stat-row">
            <span class="lbl">${this.escapeHtml(k)}:</span>
            <span class="val">${this.escapeHtml(v)}</span>
          </div>
        `;
      }).join("");
    }

    if (actions && badgeCount) {
      if (report.failed > 0) {
        actions.style.display = "block";
        badgeCount.textContent = report.failed;
        const btnRetryAll = document.getElementById("btnRetryFailedAll");
        if (btnRetryAll) {
          btnRetryAll.onclick = (e) => {
            e.stopPropagation();
            this.retryAllFailed();
          };
        }
      } else {
        actions.style.display = "none";
      }
    }
  }

  updateButtonsState() {
    const isMode2 = this.captionMode === "mode2";
    const hasProcessedAudio = this.files.some(f => f.hasProcessed && f.captionMode !== "mode2");
    const hasProcessedSrts = this.files.some(f => f.hasProcessedSrt);

    const btnExportAll = document.getElementById("btnExportAll") || document.getElementById("btnExportMp3");
    if (btnExportAll) {
      btnExportAll.disabled = isMode2 || !hasProcessedAudio;
    }

    const btnExportSrts = document.getElementById("btnExportSrts");
    if (btnExportSrts) {
      btnExportSrts.disabled = !hasProcessedSrts;
    }

    const btnDownloadAudioZip = document.getElementById("btnDownloadAudioZip");
    if (btnDownloadAudioZip) {
      btnDownloadAudioZip.disabled = isMode2 || !hasProcessedAudio;
    }

    const btnDownloadCaptionsZip = document.getElementById("btnDownloadCaptionsZip");
    if (btnDownloadCaptionsZip) {
      btnDownloadCaptionsZip.disabled = !hasProcessedSrts;
    }

    const btnZip = document.getElementById("btnDownloadZip");
    if (btnZip) {
      btnZip.disabled = isMode2 || !hasProcessedAudio;
    }

    const btnBatch = document.getElementById("btnProcessBatch");
    if (btnBatch) {
      btnBatch.disabled = this.isProcessing || this.files.length === 0;
    }
  }

  renderLogs(logs) {
    const terminal = document.getElementById("terminalLogs");
    if (!terminal) return;
    
    const content = logs.map(l => `
      <div class="log-line ${l.level || 'info'}">
        <span class="log-time">[${l.timestamp}]</span>
        <span class="log-msg">${this.escapeHtml(l.message)}</span>
      </div>
    `).join("");

    terminal.innerHTML = content;
    terminal.scrollTop = terminal.scrollHeight;
  }

  addLog(msg, level = "info") {
    const terminal = document.getElementById("terminalLogs");
    if (!terminal) return;
    const now = new Date().toTimeString().split(' ')[0];
    const line = document.createElement("div");
    line.className = `log-line ${level}`;
    line.innerHTML = `<span class="log-time">[${now}]</span> <span class="log-msg">${this.escapeHtml(msg)}</span>`;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
  }

  formatTime(secs) {
    if (!secs || isNaN(secs)) return "00:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    const h = Math.floor(m / 60);
    const remM = m % 60;
    if (h > 0) {
      return `${h}:${remM < 10 ? '0' : ''}${remM}:${s < 10 ? '0' : ''}${s}`;
    }
    return `${remM < 10 ? '0' : ''}${remM}:${s < 10 ? '0' : ''}${s}`;
  }

  escapeHtml(str) {
    return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.App = new VoiceoverApp();
});
