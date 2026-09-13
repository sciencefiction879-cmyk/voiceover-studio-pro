/**
 * Voiceover Studio Pro - Gemini AI Audio Intelligence Module
 * Handles:
 * - Bulk API Key Pool (with local storage persistence and server synchronization)
 * - Automatic Key Rotation & Failover Tracking
 * - Audio Analysis triggering and Google AI Studio Model Selection
 * - 1-Click Apply to All Voiceovers
 */

const GeminiAI = {
  keys: [],
  selectedModel: "gemini-2.5-flash",
  lastAnalysis: null,

  init() {
    // Load saved keys from localStorage
    const savedKeys = localStorage.getItem("gemini_api_keys_pool");
    if (savedKeys) {
      try {
        this.keys = JSON.parse(savedKeys);
      } catch (e) {
        this.keys = savedKeys.split("\n").filter(k => k.trim());
      }
    }

    const savedModel = localStorage.getItem("gemini_selected_model");
    if (savedModel) {
      this.selectedModel = savedModel;
    }

    this.bindEvents();
    this.syncKeysWithServer();
  },

  bindEvents() {
    // Open Gemini AI Modal
    const btnOpenModal = document.getElementById("btnOpenGeminiModal");
    const btnSuggestQuick = document.getElementById("btnGeminiSuggestQuick");
    const modal = document.getElementById("geminiModal");
    const btnCloseModal = document.getElementById("btnCloseGeminiModal");
    const btnSaveKeys = document.getElementById("btnSaveGeminiKeys");
    const btnRunAnalysis = document.getElementById("btnRunGeminiAnalysis");
    const btnApplySettings = document.getElementById("btnApplyGeminiSettings");
    const modelSelect = document.getElementById("geminiModelSelect");

    if (btnOpenModal) {
      btnOpenModal.addEventListener("click", () => this.openModal());
    }
    if (btnSuggestQuick) {
      btnSuggestQuick.addEventListener("click", () => this.quickAnalyze());
    }
    if (btnCloseModal && modal) {
      btnCloseModal.addEventListener("click", () => modal.classList.remove("active"));
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.remove("active");
      });
    }

    if (btnSaveKeys) {
      btnSaveKeys.addEventListener("click", () => this.saveKeysFromInput());
    }

    if (btnRunAnalysis) {
      btnRunAnalysis.addEventListener("click", () => this.runAnalysis());
    }

    if (btnApplySettings) {
      btnApplySettings.addEventListener("click", () => this.applySettingsToAll());
    }

    if (modelSelect) {
      modelSelect.value = this.selectedModel;
      modelSelect.addEventListener("change", (e) => {
        this.selectedModel = e.target.value;
        localStorage.setItem("gemini_selected_model", this.selectedModel);
      });
    }
  },

  openModal() {
    const modal = document.getElementById("geminiModal");
    if (!modal) return;

    // Populate textarea with current keys
    const textarea = document.getElementById("geminiKeysInput");
    if (textarea) {
      textarea.value = this.keys.join("\n");
    }

    const modelSelect = document.getElementById("geminiModelSelect");
    if (modelSelect) {
      modelSelect.value = this.selectedModel;
    }

    this.updateKeyStatusUI();
    modal.classList.add("active");
  },

  async syncKeysWithServer() {
    if (!this.keys || this.keys.length === 0) {
      this.updateHeaderBadge(0, false);
      return;
    }

    try {
      const resp = await fetch("/api/gemini/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys: this.keys })
      });
      const data = await resp.json();
      if (data.success && data.pool) {
        this.updateHeaderBadge(data.pool.totalKeys, data.pool.hasAvailable);
      }
    } catch (e) {
      console.warn("Could not sync Gemini keys with server:", e);
    }
  },

  async saveKeysFromInput() {
    const textarea = document.getElementById("geminiKeysInput");
    if (!textarea) return;

    const raw = textarea.value;
    const cleanKeys = raw
      .replace(/,/g, "\n")
      .replace(/;/g, "\n")
      .split("\n")
      .map(k => k.trim())
      .filter(k => k.length > 0 && !k.startsWith("#"));

    this.keys = cleanKeys;
    localStorage.setItem("gemini_api_keys_pool", JSON.stringify(this.keys));

    try {
      const resp = await fetch("/api/gemini/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys: this.keys })
      });
      const data = await resp.json();
      if (data.success) {
        App.showToast(`✅ Saved ${this.keys.length} Gemini API keys into pool!`, "success");
        this.updateKeyStatusUI(data.pool);
        this.updateHeaderBadge(data.pool.totalKeys, data.pool.hasAvailable);
      }
    } catch (e) {
      App.showToast(`Error saving keys: ${e.message}`, "error");
    }
  },

  updateKeyStatusUI(pool) {
    const statusContainer = document.getElementById("geminiKeyPoolList");
    if (!statusContainer) return;

    if (!this.keys || this.keys.length === 0) {
      statusContainer.innerHTML = `<div class="empty-pool-msg">No Gemini API keys added. Paste keys above (one per line).</div>`;
      return;
    }

    let html = `<div class="key-pool-grid">`;
    this.keys.forEach((k, idx) => {
      const mask = k.length > 8 ? `${k.slice(0, 4)}...${k.slice(-4)}` : `Key #${idx + 1}`;
      const isActive = (pool && pool.activeKeyIndex === idx) || idx === 0;
      const isExhausted = pool && pool.keys && pool.keys[idx] && pool.keys[idx].status === "exhausted";

      html += `
        <div class="key-pill ${isActive ? 'active' : ''} ${isExhausted ? 'exhausted' : ''}">
          <span class="key-indicator ${isExhausted ? 'dot-red' : (isActive ? 'dot-green' : 'dot-gray')}"></span>
          <span class="key-name">Key #${idx + 1} (${mask})</span>
          ${isExhausted ? '<span class="key-tag tag-exhausted">Quota Limit</span>' : (isActive ? '<span class="key-tag tag-active">Active</span>' : '<span class="key-tag">Ready</span>')}
        </div>
      `;
    });
    html += `</div>`;
    statusContainer.innerHTML = html;
  },

  updateHeaderBadge(count, hasAvailable) {
    const badge = document.getElementById("geminiHeaderBadge");
    if (!badge) return;

    if (count > 0 && hasAvailable) {
      badge.className = "status-pill gemini-active";
      badge.innerHTML = `<span class="pill-dot gemini-glow"></span><span class="pill-text">✨ Gemini AI (${count} Keys)</span>`;
    } else if (count > 0 && !hasAvailable) {
      badge.className = "status-pill gemini-warning";
      badge.innerHTML = `<span class="pill-dot warning"></span><span class="pill-text">⚠️ Gemini Keys Exhausted</span>`;
    } else {
      badge.className = "status-pill gemini-idle";
      badge.innerHTML = `<span class="pill-dot"></span><span class="pill-text">Gemini AI (Optional)</span>`;
    }
  },

  async quickAnalyze() {
    if (!this.keys || this.keys.length === 0) {
      this.openModal();
      App.showToast("Please add at least one Gemini API key first.", "warning");
      return;
    }
    this.openModal();
    this.runAnalysis();
  },

  async runAnalysis() {
    if (!this.keys || this.keys.length === 0) {
      App.showToast("Please add your Gemini API key(s) first.", "warning");
      return;
    }

    const currentFile = App.selectedFile || (App.files && App.files[0]);
    if (!currentFile) {
      App.showToast("Please load or scan voiceover files first.", "warning");
      return;
    }

    const btnRun = document.getElementById("btnRunGeminiAnalysis");
    const resultCard = document.getElementById("geminiResultCard");
    const customPrompt = document.getElementById("geminiCustomPrompt") ? document.getElementById("geminiCustomPrompt").value : "";

    if (btnRun) {
      btnRun.disabled = true;
      btnRun.innerHTML = `<span class="spinner-sm"></span> Analyzing Audio Timbre with Gemini...`;
    }

    try {
      const resp = await fetch("/api/gemini/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_id: currentFile.id,
          file_path: currentFile.filePath,
          model: this.selectedModel,
          custom_instructions: customPrompt,
          keys: this.keys
        })
      });

      const res = await resp.json();
      if (!res.success) {
        throw new Error(res.error || "Analysis failed");
      }

      this.lastAnalysis = res.data.analysis;
      this.displayAnalysisResult(res.data);
      this.updateKeyStatusUI(res.pool);
      this.updateHeaderBadge(res.pool.totalKeys, res.pool.hasAvailable);
      App.showToast("✨ Gemini AI analysis complete! Optimal settings ready.", "success");
    } catch (e) {
      App.showToast(`Gemini Error: ${e.message}`, "error");
    } finally {
      if (btnRun) {
        btnRun.disabled = false;
        btnRun.innerHTML = `✨ Re-Analyze Voiceover`;
      }
    }
  },

  displayAnalysisResult(data) {
    const resultCard = document.getElementById("geminiResultCard");
    if (!resultCard) return;

    const analysis = data.analysis;
    const p = analysis.parameters || {};

    resultCard.innerHTML = `
      <div class="gemini-card-header">
        <div class="gemini-badge-glow">✨ AI Broadcast & Anti-Fingerprint Profile</div>
        <div class="uniqueness-tag">Uniqueness Rating: <strong>${analysis.uniqueness_score || 96}/100</strong></div>
      </div>
      <h3 class="gemini-profile-title">${analysis.summary_title || "Optimized Broadcast Vocal"}</h3>
      <p class="gemini-rationale">${analysis.ai_analysis || "Acoustic parameters calculated to ensure polished voiceover clarity while making the audio completely unique from original fingerprinting."}</p>
      
      <div class="gemini-param-highlights">
        <div class="param-chip"><span>Pitch:</span> <strong>${p.pitch_min}st ~ ${p.pitch_max}st</strong></div>
        <div class="param-chip"><span>Speed:</span> <strong>${p.speed_min}x ~ ${p.speed_max}x</strong></div>
        <div class="param-chip"><span>Bass Warmth:</span> <strong>+${p.bass_max}dB (180Hz)</strong></div>
        <div class="param-chip"><span>Presence:</span> <strong>+${p.mid_max}dB (3.2kHz)</strong></div>
        <div class="param-chip"><span>Loudness:</span> <strong>${p.loudnorm_lufs_min} LUFS</strong></div>
        <div class="param-chip"><span>De-Esser:</span> <strong>Active (${p.deesser_max})</strong></div>
        <div class="param-chip"><span>25m+ Cuts:</span> <strong>${p.cut_duration_min}s ~ ${p.cut_duration_max}s</strong></div>
      </div>

      <div class="gemini-card-actions">
        <button id="btnApplyGeminiSettings" class="btn btn-primary btn-glow">
          ✨ Apply AI Settings to All Voiceovers
        </button>
        <button id="btnPreviewGeminiSettings" class="btn btn-secondary">
          ▶️ Test Live Preview
        </button>
      </div>
    `;

    resultCard.classList.add("active");

    // Bind apply & preview in card
    const btnApply = document.getElementById("btnApplyGeminiSettings");
    const btnPrev = document.getElementById("btnPreviewGeminiSettings");
    if (btnApply) btnApply.addEventListener("click", () => this.applySettingsToAll());
    if (btnPrev) btnPrev.addEventListener("click", () => {
      this.applySettingsToAll(false);
      const modal = document.getElementById("geminiModal");
      if (modal) modal.classList.remove("active");
      App.generatePreview();
    });
  },

  applySettingsToAll(showToastMessage = true) {
    if (!this.lastAnalysis || !this.lastAnalysis.parameters) {
      App.showToast("No Gemini recommendations available yet.", "warning");
      return;
    }

    const p = this.lastAnalysis.parameters;
    
    // Set AudioDSP form values
    const map = {
      "pitch_min": p.pitch_min, "pitch_max": p.pitch_max,
      "speed_min": p.speed_min, "speed_max": p.speed_max,
      "volume_min": p.volume_min, "volume_max": p.volume_max,
      "highpass_min": p.highpass_min, "highpass_max": p.highpass_max,
      "bass_min": p.bass_min, "bass_max": p.bass_max,
      "mid_min": p.mid_min, "mid_max": p.mid_max,
      "treble_min": p.treble_min, "treble_max": p.treble_max,
      "lowpass_min": p.lowpass_min, "lowpass_max": p.lowpass_max,
      "deesser_min": p.deesser_min, "deesser_max": p.deesser_max,
      "denoise_min": p.denoise_min, "denoise_max": p.denoise_max,
      "comp_thresh_min": p.comp_thresh_min, "comp_thresh_max": p.comp_thresh_max,
      "comp_ratio_min": p.comp_ratio_min, "comp_ratio_max": p.comp_ratio_max,
      "comp_makeup_min": p.comp_makeup_min, "comp_makeup_max": p.comp_makeup_max,
      "loudnorm_lufs_min": p.loudnorm_lufs_min, "loudnorm_lufs_max": p.loudnorm_lufs_max,
      "loudnorm_tp_min": p.loudnorm_tp_min, "loudnorm_tp_max": p.loudnorm_tp_max,
      "limiter_min": p.limiter_min, "limiter_max": p.limiter_max,
      "cut_duration_min": p.cut_duration_min, "cut_duration_max": p.cut_duration_max
    };

    Object.keys(map).forEach(id => {
      const el = document.getElementById(id);
      if (el && map[id] !== undefined) {
        el.value = map[id];
        // Trigger input event to update display labels and sliders
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });

    // Checkboxes
    if (p.deesser_enabled !== undefined && document.getElementById("deesser_enabled")) {
      document.getElementById("deesser_enabled").checked = p.deesser_enabled;
    }
    if (p.denoise_enabled !== undefined && document.getElementById("denoise_enabled")) {
      document.getElementById("denoise_enabled").checked = p.denoise_enabled;
    }
    if (p.comp_enabled !== undefined && document.getElementById("comp_enabled")) {
      document.getElementById("comp_enabled").checked = p.comp_enabled;
    }
    if (p.loudnorm_enabled !== undefined && document.getElementById("loudnorm_enabled")) {
      document.getElementById("loudnorm_enabled").checked = p.loudnorm_enabled;
    }
    if (p.limiter_enabled !== undefined && document.getElementById("limiter_enabled")) {
      document.getElementById("limiter_enabled").checked = p.limiter_enabled;
    }

    // Set preset select to custom or Gemini AI
    const presetSelect = document.getElementById("presetSelect");
    if (presetSelect) presetSelect.value = "custom";

    if (showToastMessage) {
      App.showToast("🎉 Applied Gemini AI settings across all voiceovers!", "success");
      const modal = document.getElementById("geminiModal");
      if (modal) modal.classList.remove("active");
    }
  }
};

window.GeminiAI = GeminiAI;
document.addEventListener("DOMContentLoaded", () => GeminiAI.init());
