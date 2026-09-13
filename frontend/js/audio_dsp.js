/**
 * Voiceover Studio Pro v2.1 - Audio DSP & Master V1 Manager
 * Complete manual control, intelligent 3%–7% randomization, and parameter state tracking
 * Ultra-robust, null-safe DOM manipulation
 */

const DEFAULT_AUDIO_CHARACTERISTICS = {
  pitch_semitones: 0.0,
  speed_factor: 1.0,
  volume_db: 0.0,
  bass_gain: 0.0,
  mid_gain: 0.0,
  treble_gain: 0.0,
  highpass_freq: 75.0,
  lowpass_freq: 18500.0,
  denoise_enabled: true,
  denoise_floor: -38.0,
  deesser_enabled: true,
  deesser_intensity: 0.14,
  comp_enabled: true,
  comp_thresh: -18.0,
  comp_ratio: 2.5,
  comp_makeup: 2.0,
  loudnorm_enabled: true,
  loudnorm_lufs: -16.0,
  loudnorm_tp: -1.0,
  limiter_enabled: true,
  limiter_ceiling: -1.0,
  cut_duration_min: 50.0,
  cut_duration_max: 70.0
};

const setElText = (id, text) => {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
};

class AudioDSPManager {
  constructor() {
    this.paramStates = {}; // key -> { isRandom: bool, isManual: bool, pct: number, value: number }
    this.masterLocked = false;
    this.initDefaultParamStates();
    this.bindEvents();
  }

  initDefaultParamStates() {
    for (const [k, val] of Object.entries(DEFAULT_AUDIO_CHARACTERISTICS)) {
      this.paramStates[k] = {
        value: val,
        pct: 0,
        isRandom: false,
        isManual: false,
        defaultValue: val
      };
    }
  }

  bindEvents() {
    // Tab switching
    document.querySelectorAll(".dsp-tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".dsp-tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".dsp-tab-content").forEach(c => c.classList.remove("active"));
        btn.classList.add("active");
        const tabId = `tab-${btn.dataset.tab}`;
        const target = document.getElementById(tabId);
        if (target) target.classList.add("active");
      });
    });

    // Master V1 Toolbar Buttons
    const btnRandomizeAll = document.getElementById("btnRandomizeAll");
    if (btnRandomizeAll) {
      btnRandomizeAll.addEventListener("click", () => this.randomizeAllCharacteristics());
    }

    const btnResetMaster = document.getElementById("btnResetMaster");
    if (btnResetMaster) {
      btnResetMaster.addEventListener("click", () => this.resetAllToDefaults());
    }

    const btnApplyToAll = document.getElementById("btnApplyToAll");
    if (btnApplyToAll) {
      btnApplyToAll.addEventListener("click", () => this.applyMasterSettingsToAll());
    }

    // Individual Dice Buttons (🎲)
    document.querySelectorAll(".btn-dice").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const paramKey = e.currentTarget.dataset.param;
        if (paramKey) this.randomizeSingleCharacteristic(paramKey);
      });
    });

    // Connect slider listeners for manual adjustment
    this.bindSlider("pitch_semitones", (val) => {
      const v = parseFloat(val);
      setElText("pitch_semitones_label", `${v > 0 ? '+' : ''}${v.toFixed(2)} st`);
      setElText("pitchVal", `${v > 0 ? '+' : ''}${v.toFixed(2)} st`);
      this.markManual("pitch_semitones", v);
    });

    this.bindSlider("speed_factor", (val) => {
      const v = parseFloat(val);
      setElText("speed_factor_label", `${v.toFixed(3)}x`);
      setElText("speedVal", `${v.toFixed(3)}x`);
      this.markManual("speed_factor", v);
    });

    this.bindSlider("volume_db", (val) => {
      const v = parseFloat(val);
      setElText("volume_db_label", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      setElText("volumeVal", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      this.markManual("volume_db", v);
    });

    this.bindSlider("highpass_freq", (val) => {
      const v = parseFloat(val);
      setElText("highpass_freq_label", `${v.toFixed(1)} Hz`);
      setElText("highpassVal", `${v.toFixed(1)} Hz`);
      this.markManual("highpass_freq", v);
    });

    this.bindSlider("lowpass_freq", (val) => {
      const v = parseFloat(val);
      setElText("lowpass_freq_label", `${Math.round(v)} Hz`);
      setElText("lowpassVal", `${Math.round(v)} Hz`);
      this.markManual("lowpass_freq", v);
    });

    this.bindSlider("bass_gain", (val) => {
      const v = parseFloat(val);
      setElText("bass_gain_label", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      setElText("bassVal", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      this.markManual("bass_gain", v);
    });

    this.bindSlider("mid_gain", (val) => {
      const v = parseFloat(val);
      setElText("mid_gain_label", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      setElText("midVal", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      this.markManual("mid_gain", v);
    });

    this.bindSlider("treble_gain", (val) => {
      const v = parseFloat(val);
      setElText("treble_gain_label", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      setElText("trebleVal", `${v > 0 ? '+' : ''}${v.toFixed(2)} dB`);
      this.markManual("treble_gain", v);
    });

    this.bindSlider("denoise_floor", (val) => {
      const v = parseFloat(val);
      setElText("denoise_floor_label", `${v.toFixed(1)} dB`);
      setElText("denoiseVal", `${v.toFixed(1)} dB`);
      this.markManual("denoise_floor", v);
    });

    this.bindSlider("deesser_intensity", (val) => {
      const v = parseFloat(val);
      setElText("deesser_intensity_label", `${v.toFixed(3)}`);
      setElText("deesserVal", `${v.toFixed(3)}`);
      this.markManual("deesser_intensity", v);
    });

    this.bindSlider("comp_thresh", (val) => {
      const v = parseFloat(val);
      setElText("comp_thresh_label", `${v.toFixed(1)} dB`);
      this.updateCompDisplay();
      this.markManual("comp_thresh", v);
    });

    this.bindSlider("comp_ratio", (val) => {
      const v = parseFloat(val);
      setElText("comp_ratio_label", `${v.toFixed(1)}:1`);
      this.updateCompDisplay();
      this.markManual("comp_ratio", v);
    });

    this.bindSlider("limiter_ceiling", (val) => {
      const v = parseFloat(val);
      setElText("limiter_ceiling_label", `${v.toFixed(2)} dBTP`);
      setElText("limiterVal", `${v.toFixed(2)} dBTP`);
      this.markManual("limiter_ceiling", v);
    });

    this.bindSlider("cut_duration_min", (val) => {
      setElText("cut_duration_min_label", `${val}s`);
      this.updateCutSummary();
    });

    this.bindSlider("cut_duration_max", (val) => {
      setElText("cut_duration_max_label", `${val}s`);
      this.updateCutSummary();
    });

    const lufsSelect = document.getElementById("loudnorm_lufs");
    if (lufsSelect) {
      lufsSelect.addEventListener("change", (e) => {
        setElText("loudnormVal", `${e.target.value} LUFS`);
      });
    }

    const btnNewSeed = document.getElementById("btnNewSeed");
    if (btnNewSeed) {
      btnNewSeed.addEventListener("click", () => {
        const seed = Math.floor(Math.random() * 900000) + 100000;
        const seedInput = document.getElementById("randomSeedInput");
        if (seedInput) seedInput.value = seed;
      });
    }

    // Preset selector
    const presetSelect = document.getElementById("presetSelect");
    if (presetSelect) {
      presetSelect.addEventListener("change", (e) => {
        if (e.target.value === "custom") return;
        this.resetAllToDefaults();
      });
    }
  }

  bindSlider(id, callback) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", (e) => {
      callback(e.target.value);
    });
  }

  updateCompDisplay() {
    const thresh = document.getElementById("comp_thresh")?.value || -18;
    const ratio = document.getElementById("comp_ratio")?.value || 2.5;
    setElText("compVal", `${thresh}dB / ${ratio}:1`);
  }

  updateCutSummary() {
    const min = document.getElementById("cut_duration_min")?.value || 50;
    const max = document.getElementById("cut_duration_max")?.value || 70;
    setElText("cutDurationVal", `${min}s – ${max}s`);
  }

  markManual(paramKey, val) {
    const def = DEFAULT_AUDIO_CHARACTERISTICS[paramKey] ?? 0;
    let pct = 0;
    if (Math.abs(def) > 0.001) {
      pct = ((val - def) / Math.abs(def)) * 100.0;
    } else {
      pct = val * 33.3; // operational scale
    }

    this.paramStates[paramKey] = {
      value: val,
      pct: roundVal(pct, 1),
      isRandom: false,
      isManual: true,
      defaultValue: def
    };
    this.updateParamBadge(paramKey);
  }

  updateParamBadge(key) {
    const badge = document.getElementById(`badge_${key}`);
    if (!badge) return;

    const st = this.paramStates[key];
    if (!st || (!st.isRandom && !st.isManual)) {
      badge.textContent = "Default";
      badge.className = "param-status-badge default";
    } else if (st.isRandom) {
      const sign = st.pct >= 0 ? "+" : "";
      badge.textContent = `🎲 ${sign}${st.pct}%`;
      badge.className = "param-status-badge random";
    } else if (st.isManual) {
      badge.textContent = "✏️ Manual";
      badge.className = "param-status-badge manual";
    }
  }

  async randomizeAllCharacteristics() {
    try {
      const current = this.getCurrentValues();
      const res = await fetch("/api/randomize-params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_params: current })
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || "Randomization failed");

      for (const [k, info] of Object.entries(data.randomized)) {
        this.paramStates[k] = info;
        this.applyValueToUI(k, info.value);
        this.updateParamBadge(k);
      }

      if (window.App && window.App.addLog) {
        window.App.addLog("🎲 Intelligent 3%–7% audio characteristic randomization applied across V1 Master.", "info");
      }
    } catch (err) {
      console.error("Error randomizing all params:", err);
    }
  }

  async randomizeSingleCharacteristic(paramKey) {
    try {
      const current = this.getCurrentValues();
      const res = await fetch("/api/randomize-params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_params: current, specific_key: paramKey })
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || "Single randomization failed");

      const info = data.randomized[paramKey];
      if (info) {
        this.paramStates[paramKey] = info;
        this.applyValueToUI(paramKey, info.value);
        this.updateParamBadge(paramKey);
        if (window.App && window.App.addLog) {
          const sign = info.pct >= 0 ? "+" : "";
          window.App.addLog(`🎲 ${paramKey} randomized: ${info.value} (${sign}${info.pct}%)`, "info");
        }
      }
    } catch (err) {
      console.error(`Error randomizing ${paramKey}:`, err);
    }
  }

  resetAllToDefaults() {
    this.initDefaultParamStates();
    for (const [k, val] of Object.entries(DEFAULT_AUDIO_CHARACTERISTICS)) {
      this.applyValueToUI(k, val);
      this.updateParamBadge(k);
    }
    if (window.App && window.App.addLog) {
      window.App.addLog("↺ All audio characteristics restored to Studio Defaults.", "info");
    }
  }

  async applyMasterSettingsToAll() {
    try {
      const payload = this.getPayload();
      const res = await fetch("/api/apply-master-v1", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: payload })
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || "Failed to apply master settings");

      this.masterLocked = true;
      const masterTag = document.getElementById("masterV1Tag");
      if (masterTag) {
        masterTag.innerHTML = `👑 <strong>MASTER APPROVED:</strong> Applied to all files in queue (V2, V3...)`;
        masterTag.style.color = "var(--accent-emerald)";
      }

      if (window.App && window.App.addLog) {
        window.App.addLog("✓ V1 Master Configuration LOCKED & PROPAGATED to all queue files.", "success");
      }
      alert("✓ Master V1 Audio Characteristics APPROVED!\n\nAll settings (pitch, speed, EQ, compression, loudness, and cuts) are now locked and ready to apply to all other voiceovers (V2, V3...).");
    } catch (err) {
      console.error("Error applying master V1:", err);
      alert("Error applying master V1: " + err.message);
    }
  }

  applyValueToUI(key, val) {
    const el = document.getElementById(key);
    if (!el) return;

    if (el.type === "checkbox") {
      el.checked = !!val;
    } else {
      el.value = val;
      el.dispatchEvent(new Event("input"));
    }
  }

  getCurrentValues() {
    const payload = this.getPayload();
    const res = {};
    for (const k of Object.keys(DEFAULT_AUDIO_CHARACTERISTICS)) {
      res[k] = payload[k] !== undefined ? payload[k] : DEFAULT_AUDIO_CHARACTERISTICS[k];
    }
    return res;
  }

  getPayload() {
    const getNum = (id, def = 0) => {
      const el = document.getElementById(id);
      return el ? parseFloat(el.value) : def;
    };
    const getBool = (id, def = true) => {
      const el = document.getElementById(id);
      return el ? el.checked : def;
    };
    const getStr = (id, def = "") => {
      const el = document.getElementById(id);
      return el ? el.value : def;
    };

    const seedInput = getStr("randomSeedInput").trim();
    const seedVal = seedInput ? parseInt(seedInput, 10) : null;

    return {
      master_locked: this.masterLocked,
      randomize_per_file: false, // V1 acts as fixed master when approved
      seed: seedVal,
      cut_duration_min: getNum("cut_duration_min", 50),
      cut_duration_max: getNum("cut_duration_max", 70),
      pitch_semitones: getNum("pitch_semitones", 0.0),
      speed_factor: getNum("speed_factor", 1.0),
      volume_db: getNum("volume_db", 0.0),
      bass_gain: getNum("bass_gain", 0.0),
      mid_gain: getNum("mid_gain", 0.0),
      treble_gain: getNum("treble_gain", 0.0),
      highpass_freq: getNum("highpass_freq", 75.0),
      lowpass_freq: getNum("lowpass_freq", 18500.0),
      denoise_enabled: getBool("denoise_enabled", true),
      denoise_floor: getNum("denoise_floor", -38.0),
      deesser_enabled: getBool("deesser_enabled", true),
      deesser_intensity: getNum("deesser_intensity", 0.14),
      comp_enabled: getBool("comp_enabled", true),
      comp_thresh: getNum("comp_thresh", -18.0),
      comp_ratio: getNum("comp_ratio", 2.5),
      comp_makeup: 2.0,
      loudnorm_enabled: getBool("loudnorm_enabled", true),
      loudnorm_lufs: getNum("loudnorm_lufs", -16.0),
      loudnorm_tp: -1.0,
      limiter_enabled: getBool("limiter_enabled", true),
      limiter_ceiling: getNum("limiter_ceiling", -1.0)
    };
  }
}

function roundVal(num, dec) {
  const m = Math.pow(10, dec);
  return Math.round(num * m) / m;
}

window.AudioDSP = new AudioDSPManager();
