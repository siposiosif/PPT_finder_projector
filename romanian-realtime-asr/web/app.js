const ui = {
  recordButton: document.querySelector("#record-button"),
  recordLabel: document.querySelector("#record-label"),
  clearButton: document.querySelector("#clear-button"),
  systemState: document.querySelector("#system-state"),
  systemStateText: document.querySelector("#system-state-text"),
  emptyState: document.querySelector("#empty-state"),
  finalTranscript: document.querySelector("#final-transcript"),
  partialTranscript: document.querySelector("#partial-transcript"),
  transcriptScroll: document.querySelector("#transcript-scroll"),
  listeningLabel: document.querySelector("#listening-label"),
  largeStatus: document.querySelector("#large-status"),
  statusDetail: document.querySelector("#status-detail"),
  gpuValue: document.querySelector("#gpu-value"),
  modelValue: document.querySelector("#model-value"),
  latencyValue: document.querySelector("#latency-value"),
  rtfValue: document.querySelector("#rtf-value"),
  sessionValue: document.querySelector("#session-value"),
  waveform: document.querySelector("#waveform"),
  testSource: document.querySelector(".test-source"),
  audioFile: document.querySelector("#audio-file"),
  audioPlayback: document.querySelector("#audio-playback"),
  samplePlayer: document.querySelector("#sample-player"),
  samplePlayerName: document.querySelector("#sample-player-name"),
  playbackAnalysis: document.querySelector("#playback-analysis"),
  playbackAnalysisLabel: document.querySelector("#playback-analysis-label"),
  playbackAnalysisStatus: document.querySelector("#playback-analysis-status"),
  playbackSeconds: document.querySelector("#playback-seconds"),
  playbackSecondsValue: document.querySelector("#playback-seconds-value"),
  playbackSilence: document.querySelector("#playback-silence"),
  playbackSilenceValue: document.querySelector("#playback-silence-value"),
  playbackThreshold: document.querySelector("#playback-threshold"),
  playbackThresholdValue: document.querySelector("#playback-threshold-value"),
  playbackLevel: document.querySelector("#playback-level"),
  playbackLevelValue: document.querySelector("#playback-level-value"),
  youtubeForm: document.querySelector("#youtube-form"),
  youtubeUrl: document.querySelector("#youtube-url"),
  modelSelect: document.querySelector("#model-select"),
  modelApplyButton: document.querySelector("#model-apply-button"),
  modelInstallForm: document.querySelector("#model-install-form"),
  modelInstallInput: document.querySelector("#model-install-input"),
  modelManagerStatus: document.querySelector("#model-manager-status"),
  bibleResults: document.querySelector("#bible-results"),
  bibleEmpty: document.querySelector("#bible-empty"),
  matchCount: document.querySelector("#match-count"),
};

let socket = null;
let stream = null;
let audioContext = null;
let sourceNode = null;
let workletNode = null;
let analyser = null;
let animationFrame = null;
let recording = false;
let connecting = false;
let stopping = false;
let finishPromise = null;
let connectionTimer = null;
let serverReady = false;
let statusTimer = null;
let statusInitialized = false;
let sampleBusy = false;
let sessionStartedAt = null;
let sessionTimer = null;
let stopTimer = null;
let bibleSearchController = null;
let bibleSearchTimer = null;
let lastBibleQuery = "";
let playbackObjectUrl = null;
let playbackSocket = null;
let playbackAudioContext = null;
let playbackSourceNode = null;
let playbackWorkletNode = null;
let playbackGeneration = 0;
let playbackHasStarted = false;
let playbackWasPlayingBeforeSeek = false;
let playbackSearchChunks = [];
let settingsLoaded = false;
let settingsSaveTimer = null;
let modelLoading = false;
const bibleMatches = new Map();

function setSystemState(kind, text) {
  ui.systemState.className = `system-state ${kind}`;
  ui.systemStateText.textContent = text;
}

function setStatus(title, detail) {
  ui.largeStatus.textContent = title;
  ui.statusDetail.textContent = detail;
}

function formatSession() {
  if (!sessionStartedAt) return "00:00";
  const seconds = Math.floor((Date.now() - sessionStartedAt) / 1000);
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const rest = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function startSessionClock() {
  sessionStartedAt = Date.now();
  ui.sessionValue.textContent = "00:00";
  sessionTimer = window.setInterval(() => {
    ui.sessionValue.textContent = formatSession();
  }, 1000);
}

function stopSessionClock() {
  window.clearInterval(sessionTimer);
  sessionTimer = null;
}

function updateMetrics(message) {
  if (typeof message.inference_ms === "number") {
    ui.latencyValue.textContent = `${message.inference_ms} ms`;
  }
  if (typeof message.rtf === "number") {
    ui.rtfValue.textContent = message.rtf.toFixed(2);
  }
}

function setModelManagerStatus(message, kind = "") {
  ui.modelManagerStatus.textContent = message;
  ui.modelManagerStatus.dataset.kind = kind;
}

function currentVoiceSettings() {
  return {
    max_segment_seconds: Number(ui.playbackSeconds.value),
    silence_seconds: Number(ui.playbackSilence.value),
    voice_threshold: Number(ui.playbackThreshold.value),
  };
}

function applyVoiceSettings(settings = {}) {
  const maxSeconds = Number(settings.max_segment_seconds);
  const silenceSeconds = Number(settings.silence_seconds);
  const threshold = Number(settings.voice_threshold);
  if (Number.isFinite(maxSeconds)) ui.playbackSeconds.value = String(maxSeconds);
  if (Number.isFinite(silenceSeconds)) ui.playbackSilence.value = String(silenceSeconds);
  if (Number.isFinite(threshold)) ui.playbackThreshold.value = String(threshold);
  ui.playbackSecondsValue.textContent = `${ui.playbackSeconds.value} s`;
  ui.playbackSilenceValue.textContent = `${Number(ui.playbackSilence.value).toFixed(2)} s`;
  ui.playbackThresholdValue.textContent = Number(ui.playbackThreshold.value).toFixed(3);
  ui.playbackLevel.low = Number(ui.playbackThreshold.value);
}

async function loadVoiceSettings() {
  try {
    const response = await fetch("/api/settings", {cache: "no-store"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Setările audio nu pot fi citite.");
    applyVoiceSettings(payload.settings);
    settingsLoaded = true;
  } catch (error) {
    setStatus("Setări audio", error.message || "Setările audio nu pot fi citite.");
  }
}

function saveVoiceSettingsSoon() {
  if (!settingsLoaded) return;
  window.clearTimeout(settingsSaveTimer);
  settingsSaveTimer = window.setTimeout(saveVoiceSettings, 350);
}

async function saveVoiceSettings() {
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(currentVoiceSettings()),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Setările audio nu au putut fi salvate.");
    applyVoiceSettings(payload.settings);
    ui.playbackAnalysisStatus.textContent = "Setările audio au fost salvate pentru microfon live și eșantioane.";
  } catch (error) {
    ui.playbackAnalysisStatus.textContent = error.message || "Setările audio nu au putut fi salvate.";
  }
}

function updateAudioSettingLabels() {
  ui.playbackSecondsValue.textContent = `${ui.playbackSeconds.value} s`;
  ui.playbackSilenceValue.textContent = `${Number(ui.playbackSilence.value).toFixed(2)} s`;
  const threshold = Number(ui.playbackThreshold.value);
  ui.playbackThresholdValue.textContent = threshold.toFixed(3);
  ui.playbackLevel.low = threshold;
  ui.playbackAnalysisLabel.textContent = "GPU / SEGMENTE PE TĂCERE";
}

function renderModelOptions(config) {
  const models = Array.isArray(config.models) ? config.models : [];
  const selected = config.selected || config.active;
  ui.modelSelect.replaceChildren(...models.map((model) => {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = `${model.label || model.id} — ${model.id}`;
    if (model.id === selected) option.selected = true;
    return option;
  }));
  modelLoading = config.loading === true;
  ui.modelSelect.disabled = !models.length || modelLoading;
  ui.modelApplyButton.disabled = !models.length || modelLoading || recording || connecting || stopping || sampleBusy;
  if (modelLoading) {
    setModelManagerStatus("Modelul se încarcă pe GPU. Așteaptă confirmarea.", "warning");
  } else if (config.selected === config.active) {
    setModelManagerStatus("Modelul curent este activ.", "ready");
  } else {
    setModelManagerStatus("Alege un model și apasă Folosește modelul. Se schimbă live, fără restart.", "warning");
  }
}

async function loadModels() {
  try {
    const response = await fetch("/api/models", {cache: "no-store"});
    if (!response.ok) throw new Error("Lista de modele nu poate fi citită.");
    renderModelOptions(await response.json());
  } catch (error) {
    setModelManagerStatus(error.message || "Lista de modele nu poate fi citită.", "error");
  }
}

async function selectModel() {
  const model = ui.modelSelect.value;
  if (!model) return;
  ui.modelApplyButton.disabled = true;
  ui.modelSelect.disabled = true;
  serverReady = false;
  modelLoading = true;
  ui.recordButton.disabled = true;
  ui.youtubeForm.querySelector("button").disabled = true;
  setModelManagerStatus("Se încarcă modelul selectat pe GPU. Așteaptă confirmarea înainte de microfon.", "warning");
  setStatus("Schimbare model", "Modelul vocal se încarcă live. Microfonul va porni după confirmare.");
  try {
    const response = await fetch("/api/models/select", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Modelul nu a putut fi selectat.");
    setModelManagerStatus(`Model activ: ${payload.active || model}`, "ready");
    serverReady = true;
    modelLoading = false;
    await loadModels();
    await loadStatus();
  } catch (error) {
    serverReady = false;
    modelLoading = false;
    setModelManagerStatus(error.message || "Modelul nu a putut fi selectat.", "error");
    await loadStatus();
  } finally {
    ui.modelSelect.disabled = !ui.modelSelect.options.length || modelLoading;
    ui.modelApplyButton.disabled = modelLoading || recording || connecting || stopping || sampleBusy || !ui.modelSelect.options.length;
  }
}

async function installModel(event) {
  event.preventDefault();
  const model = ui.modelInstallInput.value.trim();
  if (!model) return;
  const button = ui.modelInstallForm.querySelector("button");
  button.disabled = true;
  ui.modelInstallInput.disabled = true;
  setModelManagerStatus("Se instalează sau se verifică modelul. Poate dura câteva minute la prima descărcare.", "warning");
  try {
    const response = await fetch("/api/models/install", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Modelul nu a putut fi instalat.");
    ui.modelInstallInput.value = "";
    setModelManagerStatus("Model adăugat în listă. Alege-l din meniu și apasă Folosește modelul.", "ready");
    await loadModels();
  } catch (error) {
    setModelManagerStatus(error.message, "error");
  } finally {
    button.disabled = false;
    ui.modelInstallInput.disabled = false;
  }
}

function appendFinal(text, findMatches = true) {
  if (!text) return;
  ui.emptyState.classList.add("hidden");
  const phrase = document.createElement("span");
  phrase.className = "phrase new";
  phrase.textContent = text;
  ui.finalTranscript.appendChild(phrase);
  ui.partialTranscript.textContent = "";
  ui.transcriptScroll.scrollTop = ui.transcriptScroll.scrollHeight;
  if (findMatches) searchBible(text);
}

function renderBibleMatches(matches, replace = false) {
  if (replace) bibleMatches.clear();
  for (const match of matches || []) bibleMatches.set(match.url, match);
  ui.bibleResults.replaceChildren();
  ui.matchCount.textContent = String(bibleMatches.size);
  if (!bibleMatches.size) {
    const empty = document.createElement("p");
    empty.id = "bible-empty";
    empty.textContent = "Nicio potrivire biblică sigură încă.";
    ui.bibleResults.appendChild(empty);
    return;
  }
  for (const match of bibleMatches.values()) {
    const link = document.createElement("a");
    link.className = "verse-match";
    link.href = match.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    const reference = document.createElement("strong");
    reference.textContent = match.reference;
    const quote = document.createElement("q");
    quote.textContent = match.text;
    link.append(reference, quote);
    ui.bibleResults.appendChild(link);
  }
}

async function searchBible(text) {
  if (!text || text.trim().length < 12) return;
  bibleSearchController?.abort();
  bibleSearchController = new AbortController();
  try {
    const response = await fetch("/api/bible-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: bibleSearchController.signal,
    });
    if (!response.ok) return;
    const payload = await response.json();
    renderBibleMatches(payload.matches);
  } catch (error) {
    if (error.name !== "AbortError") console.warn("Bible search failed", error);
  }
}

function maybeSearchBible(text) {
  const query = String(text || "").trim();
  if (query.length < 12 || !/(capitol|verset|romani|matei|ioan|timote|corint|petru|psalm|ieremia)/i.test(query)) return;
  if (query === lastBibleQuery) return;
  window.clearTimeout(bibleSearchTimer);
  bibleSearchTimer = window.setTimeout(() => {
    lastBibleQuery = query;
    searchBible(query);
  }, 250);
}

function setTestBusy(busy, sourceName = "") {
  sampleBusy = busy;
  ui.testSource.classList.toggle("busy", busy);
  ui.recordButton.disabled = busy || !serverReady;
  ui.youtubeForm.querySelector("button").disabled = busy || recording || !serverReady;
  if (busy) {
    setStatus("Procesează eșantionul", `${sourceName} este transcris pe GPU.`);
    setSystemState("", "Transcriere în curs…");
  }
}

function showPlaybackSource(source, name) {
  stopPlaybackTranscription(false);
  playbackGeneration += 1;
  playbackHasStarted = false;
  ui.audioPlayback.src = source;
  ui.samplePlayerName.textContent = name;
  ui.samplePlayer.hidden = false;
  ui.playbackAnalysis.classList.remove("active");
  ui.playbackAnalysisStatus.textContent = "Apasă redare pentru transcriere pe măsură ce asculți.";
  ui.audioPlayback.load();
}

function formatPlaybackTime(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function appendPlaybackChunk(message) {
  ui.emptyState.classList.add("hidden");
  ui.partialTranscript.textContent = "";
  ui.finalTranscript.classList.add("playback-mode");
  const chunk = document.createElement("div");
  chunk.className = "playback-chunk";
  const time = document.createElement("span");
  time.className = "chunk-time";
  time.textContent = `${formatPlaybackTime(message.start_seconds)}–${formatPlaybackTime(message.end_seconds)}`;
  const words = document.createElement("span");
  words.className = `chunk-words${message.text ? "" : " empty"}`;
  words.textContent = message.text || "fără cuvinte recunoscute";
  chunk.append(time, words);
  ui.finalTranscript.appendChild(chunk);
  ui.transcriptScroll.scrollTop = ui.transcriptScroll.scrollHeight;
  ui.playbackAnalysisStatus.textContent = `Segmentul ${message.sequence} · ${time.textContent}`;
  setStatus("Redare + transcriere", `Segmentul ${message.sequence} a fost procesat pe GPU.`);
  updateMetrics(message);
  if (message.text) {
    playbackSearchChunks.push(message.text);
    playbackSearchChunks = playbackSearchChunks.slice(-5);
    const searchWindow = playbackSearchChunks.join(" ");
    maybeSearchBible(searchWindow);
  }
}

async function ensurePlaybackPipeline() {
  if (playbackAudioContext) {
    if (playbackAudioContext.state === "suspended") await playbackAudioContext.resume();
    return;
  }
  playbackAudioContext = new AudioContext({ latencyHint: "interactive" });
  await playbackAudioContext.audioWorklet.addModule("/audio-worklet.js?v=2");
  playbackSourceNode = playbackAudioContext.createMediaElementSource(ui.audioPlayback);
  playbackWorkletNode = new AudioWorkletNode(playbackAudioContext, "pcm-capture");
  const silentOutput = playbackAudioContext.createGain();
  silentOutput.gain.value = 0;
  playbackSourceNode.connect(playbackAudioContext.destination);
  playbackSourceNode.connect(playbackWorkletNode);
  playbackWorkletNode.connect(silentOutput).connect(playbackAudioContext.destination);
  playbackWorkletNode.port.onmessage = (event) => {
    const samples = event.data instanceof Float32Array ? event.data : new Float32Array(event.data);
    if (samples.length) {
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      const level = Math.sqrt(sum / samples.length);
      ui.playbackLevel.value = Math.min(Number(ui.playbackLevel.max), level);
      ui.playbackLevelValue.textContent = level.toFixed(3);
    }
    if (playbackSocket?.readyState === WebSocket.OPEN && !ui.audioPlayback.paused) {
      if (playbackSocket.bufferedAmount > playbackAudioContext.sampleRate * 4 * 5) {
        stopPlaybackTranscription(false);
        ui.playbackAnalysisStatus.textContent = "Transcrierea nu mai ține pasul. Pune redarea pe pauză și reia.";
        return;
      }
      playbackSocket.send(samples.buffer);
    }
  };
}

function stopPlaybackTranscription(flush = true) {
  ui.playbackAnalysis.classList.remove("active");
  const activeSocket = playbackSocket;
  if (!activeSocket) return;
  if (flush && activeSocket.readyState === WebSocket.OPEN) {
    activeSocket.send(JSON.stringify({ type: "stop" }));
    window.setTimeout(() => activeSocket.close(), 15000);
  } else {
    activeSocket.close();
  }
  playbackSocket = null;
}

async function startPlaybackTranscription() {
  if (!ui.audioPlayback.src) return;
  if (!serverReady || sampleBusy || recording || connecting || stopping) {
    ui.audioPlayback.pause();
    setStatus("Redare în așteptare", "Oprește sesiunea activă și așteaptă ca modelul vocal să fie pregătit.");
    return;
  }
  try {
    await ensurePlaybackPipeline();
  } catch (error) {
    ui.audioPlayback.pause();
    setStatus("Redare indisponibilă", error.message || "Sunetul nu poate fi conectat la transcriere.");
    return;
  }
  if (ui.audioPlayback.paused || recording || connecting || stopping || !serverReady) return;

  stopPlaybackTranscription(false);
  const generation = ++playbackGeneration;
  if (!playbackHasStarted) {
    playbackHasStarted = true;
    ui.finalTranscript.replaceChildren();
    ui.partialTranscript.textContent = "";
    ui.finalTranscript.classList.add("playback-mode");
    renderBibleMatches([], true);
    playbackSearchChunks = [];
    lastBibleQuery = "";
  }

  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/playback-transcribe`);
  socket.binaryType = "arraybuffer";
  playbackSocket = socket;
  ui.playbackAnalysis.classList.add("active");
  const chunkSeconds = Number(ui.playbackSeconds.value);
  const silenceSeconds = Number(ui.playbackSilence.value);
  const threshold = Number(ui.playbackThreshold.value);
  ui.playbackAnalysisStatus.textContent = "Ascult vocea până la următoarea pauză…";
  setStatus("Ascultă redarea", `Pauză de ${silenceSeconds.toFixed(2)} s · prag ${threshold.toFixed(3)}.`);

  socket.addEventListener("open", () => {
    if (generation !== playbackGeneration || playbackSocket !== socket) {
      socket.close();
      return;
    }
    socket.send(JSON.stringify({
      type: "start",
      sample_rate: playbackAudioContext.sampleRate,
      max_segment_seconds: chunkSeconds,
      silence_seconds: silenceSeconds,
      voice_threshold: threshold,
      offset_seconds: ui.audioPlayback.currentTime,
    }));
  });
  socket.addEventListener("message", (event) => {
    if (generation !== playbackGeneration) return;
    let message;
    try {
      message = JSON.parse(event.data);
      if (!message || typeof message !== "object") throw new Error("Răspuns invalid");
    } catch {
      ui.playbackAnalysisStatus.textContent = "Răspuns invalid de la serverul vocal.";
      stopPlaybackTranscription(false);
      return;
    }
    if (message.type === "partial") {
      showPartial(message.text);
      updateMetrics(message);
      maybeSearchBible([...playbackSearchChunks, message.text].join(" "));
      setStatus("Redare + transcriere", "Cuvintele se actualizează în timp real…");
    }
    if (message.type === "chunk") appendPlaybackChunk(message);
    if (message.type === "error") {
      ui.playbackAnalysisStatus.textContent = message.message || "Transcrierea redării s-a oprit.";
      ui.playbackAnalysis.classList.remove("active");
      stopPlaybackTranscription(false);
    }
    if (message.type === "stopped") socket.close();
  });
  socket.addEventListener("close", () => {
    if (generation !== playbackGeneration) return;
    if (playbackSocket === socket) playbackSocket = null;
    ui.playbackAnalysis.classList.remove("active");
    if (ui.audioPlayback.paused && !ui.audioPlayback.ended) {
      ui.playbackAnalysisStatus.textContent = "Pauză · apasă redare pentru a continua cu un segment nou.";
    } else if (!ui.audioPlayback.paused) {
      ui.playbackAnalysisStatus.textContent = "Conexiunea vocală s-a închis. Reia redarea pentru a reconecta transcrierea.";
    }
  });
  socket.addEventListener("error", () => {
    if (generation !== playbackGeneration) return;
    ui.playbackAnalysisStatus.textContent = "Serverul vocal nu este disponibil. Reia redarea după reconectare.";
    stopPlaybackTranscription(false);
  });
}

function applySampleResult(result) {
  ui.finalTranscript.classList.remove("playback-mode");
  appendFinal(result.text || "(nu a fost recunoscut text)", false);
  renderBibleMatches(result.matches || [], true);
  updateMetrics(result);
  setStatus("Eșantion finalizat", result.source || "Transcriere completă");
  setSystemState("ready", "GPU pregătit");
}

async function transcribeSample(url, options, sourceName) {
  if (!serverReady || sampleBusy) return;
  if (recording || connecting || stopping) await finishStop();
  if (!ui.audioPlayback.paused) ui.audioPlayback.pause();
  stopPlaybackTranscription(false);
  setTestBusy(true, sourceName);
  try {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Eșantionul nu a putut fi procesat.");
    if (typeof payload.text !== "string") throw new Error("Serverul a trimis un rezultat incomplet.");
    applySampleResult(payload);
  } catch (error) {
    setSystemState("error", "Eșantion nereușit");
    setStatus("Nu pot transcrie", error.message);
  } finally {
    setTestBusy(false);
    ui.recordButton.disabled = !serverReady;
  }
}

function showPartial(text) {
  ui.emptyState.classList.add("hidden");
  ui.partialTranscript.textContent = text || "";
  ui.transcriptScroll.scrollTop = ui.transcriptScroll.scrollHeight;
}

function handleServerMessage(message) {
  switch (message.type) {
    case "calibrating":
      setStatus("Calibrare", "Păstrează liniștea pentru o secundă.");
      ui.listeningLabel.textContent = "CALIBRARE";
      break;
    case "ready":
      setStatus("Ascultă", "Vorbește normal în limba română.");
      ui.listeningLabel.textContent = "ASCULTARE LIVE";
      ui.listeningLabel.classList.add("active");
      break;
    case "speech_start":
      setStatus("Transcrie", "Vocea este procesată pe GPU.");
      break;
    case "partial":
      showPartial(message.text);
      updateMetrics(message);
      break;
    case "final":
      appendFinal(message.text);
      updateMetrics(message);
      setStatus("Ascultă", "Continuă să vorbești când ești gata.");
      break;
    case "stopped":
      finishStop();
      break;
    case "error":
      finishStop(message.message || "Transcrierea s-a oprit.");
      break;
  }
}

function fitCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const rect = ui.waveform.getBoundingClientRect();
  ui.waveform.width = Math.max(1, Math.floor(rect.width * ratio));
  ui.waveform.height = Math.max(1, Math.floor(rect.height * ratio));
}

function drawWaveform() {
  if (!analyser) return;
  const data = new Uint8Array(analyser.fftSize);
  const context = ui.waveform.getContext("2d");
  const render = () => {
    analyser.getByteTimeDomainData(data);
    const width = ui.waveform.width;
    const height = ui.waveform.height;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = "#56dff3";
    context.lineWidth = Math.max(1.5, window.devicePixelRatio || 1);
    context.beginPath();
    for (let index = 0; index < data.length; index += 1) {
      const x = (index / (data.length - 1)) * width;
      const y = (data[index] / 255) * height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.stroke();
    animationFrame = window.requestAnimationFrame(render);
  };
  render();
}

async function startRecording() {
  if (recording || connecting || stopping || !serverReady || sampleBusy) return;
  connecting = true;
  if (!ui.audioPlayback.paused) ui.audioPlayback.pause();
  stopPlaybackTranscription(false);
  ui.recordButton.disabled = true;
  setStatus("Conectare", "Se solicită accesul la microfon.");

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (!connecting || !serverReady) {
      await releaseAudio();
      return;
    }

    audioContext = new AudioContext({ latencyHint: "interactive" });
    await audioContext.resume();
    await audioContext.audioWorklet.addModule("/audio-worklet.js?v=2");
    if (!connecting || !serverReady || !audioContext || !stream) {
      await releaseAudio();
      return;
    }
    sourceNode = audioContext.createMediaStreamSource(stream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-capture");
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    const silentOutput = audioContext.createGain();
    silentOutput.gain.value = 0;

    sourceNode.connect(analyser);
    sourceNode.connect(workletNode);
    workletNode.connect(silentOutput).connect(audioContext.destination);

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const captureSocket = new WebSocket(`${protocol}//${location.host}/ws/transcribe`);
    socket = captureSocket;
    captureSocket.binaryType = "arraybuffer";
    connectionTimer = window.setTimeout(() => {
      if (socket === captureSocket && connecting) finishStop("Conexiunea la serverul vocal a expirat. Încearcă din nou.");
    }, 10000);

    captureSocket.addEventListener("open", () => {
      if (socket !== captureSocket || !audioContext || !workletNode) {
        captureSocket.close();
        return;
      }
      window.clearTimeout(connectionTimer);
      connecting = false;
      const liveSettings = currentVoiceSettings();
      captureSocket.send(JSON.stringify({ type: "start", sample_rate: audioContext.sampleRate, ...liveSettings }));
      workletNode.port.onmessage = (event) => {
        if (socket !== captureSocket || stopping || captureSocket.readyState !== WebSocket.OPEN) return;
        if (captureSocket.bufferedAmount > audioContext.sampleRate * 4 * 5) {
          finishStop("Transcrierea nu mai ține pasul cu sunetul. Pornește o sesiune nouă.");
          return;
        }
        captureSocket.send(event.data.buffer);
      };
      recording = true;
      ui.recordButton.disabled = false;
      ui.recordButton.classList.add("recording");
      ui.recordLabel.textContent = "Oprește microfonul";
      ui.youtubeUrl.disabled = true;
      ui.youtubeForm.querySelector("button").disabled = true;
      setSystemState("ready", "GPU și microfon active");
      setStatus("Ascultă", `Microfon live · ${liveSettings.max_segment_seconds}s max · pauză ${liveSettings.silence_seconds.toFixed(2)}s · prag ${liveSettings.voice_threshold.toFixed(3)}.`);
      startSessionClock();
      fitCanvas();
      drawWaveform();
    });

    captureSocket.addEventListener("message", (event) => {
      if (socket !== captureSocket) return;
      try {
        const message = JSON.parse(event.data);
        if (!message || typeof message !== "object") throw new Error("Răspuns invalid");
        handleServerMessage(message);
      } catch {
        finishStop("Serverul vocal a trimis un răspuns invalid. Încearcă din nou.");
      }
    });

    captureSocket.addEventListener("close", () => {
      if (socket === captureSocket) finishStop(stopping ? "" : "Conexiunea vocală s-a închis. Textul afișat a fost păstrat.");
    });
    captureSocket.addEventListener("error", () => {
      if (socket === captureSocket) finishStop("Serverul vocal nu este disponibil. Verifică aplicația vocală și încearcă din nou.");
    });
  } catch (error) {
    await finishStop(error.message || "Accesul la microfon a fost refuzat.");
  }
}

async function releaseAudio() {
  window.cancelAnimationFrame(animationFrame);
  animationFrame = null;
  analyser = null;
  try { workletNode?.disconnect(); } catch { /* Already disconnected. */ }
  try { sourceNode?.disconnect(); } catch { /* Already disconnected. */ }
  workletNode = null;
  sourceNode = null;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  const activeContext = audioContext;
  audioContext = null;
  if (activeContext && activeContext.state !== "closed") await activeContext.close().catch(() => {});
}

function stopRecording() {
  if (!recording || stopping) return;
  stopping = true;
  stream?.getTracks().forEach((track) => track.stop());
  ui.recordButton.disabled = true;
  setStatus("Finalizează", "Ultimele cuvinte sunt procesate.");
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop" }));
    stopTimer = window.setTimeout(() => finishStop("Ultimele cuvinte nu au fost confirmate de server. Textul deja afișat a fost păstrat."), 15000);
  } else {
    finishStop();
  }
}

function finishStop(errorDetail = "") {
  if (finishPromise) return finishPromise;
  finishPromise = completeStop(errorDetail).finally(() => { finishPromise = null; });
  return finishPromise;
}

async function completeStop(errorDetail) {
  window.clearTimeout(stopTimer);
  window.clearTimeout(connectionTimer);
  stopTimer = null;
  recording = false;
  connecting = false;
  stopping = true;
  const activeSocket = socket;
  socket = null;
  activeSocket?.close();
  await releaseAudio();
  stopping = false;
  stopSessionClock();
  ui.recordButton.disabled = !serverReady || sampleBusy;
  ui.recordButton.classList.remove("recording");
  ui.recordLabel.textContent = "Pornește microfonul";
  ui.audioFile.disabled = false;
  ui.youtubeUrl.disabled = false;
  ui.youtubeForm.querySelector("button").disabled = !serverReady || sampleBusy;
  ui.listeningLabel.textContent = "MICROFON OPRIT";
  ui.listeningLabel.classList.remove("active");
  if (errorDetail) {
    setSystemState("error", "Sesiune oprită");
    setStatus("Transcriere oprită", errorDetail);
  } else {
    setSystemState(serverReady ? "ready" : "", serverReady ? "GPU pregătit" : "Server indisponibil");
    setStatus("În așteptare", "Pornește microfonul pentru o sesiune nouă.");
  }
}

async function loadStatus() {
  window.clearTimeout(statusTimer);
  const wasReady = serverReady;
  try {
    const response = await fetch("/api/status", { signal: AbortSignal.timeout(5000), cache: "no-store" });
    if (!response.ok) throw new Error("Server indisponibil");
    const status = await response.json();
    if (status.service !== "romanian-realtime-asr") throw new Error("Alt serviciu folosește portul aplicației vocale.");
    serverReady = status.ready === true;
    if (!settingsLoaded && status.settings) {
      applyVoiceSettings(status.settings);
      settingsLoaded = true;
    }
    ui.gpuValue.textContent = status.gpu?.replace("NVIDIA GeForce ", "") || "CUDA";
    ui.modelValue.textContent = status.model || "model ASR local";
    if (!serverReady) {
      if (recording || connecting) await finishStop(status.error || "Modelul vocal nu este pregătit.");
      setSystemState(status.state === "error" ? "error" : "", status.state === "error" ? "Model indisponibil" : "Modelul se încarcă…");
      setStatus(status.state === "error" ? "Pornire nereușită" : "Pregătire GPU", status.error || "Așteaptă încărcarea modelului. PPT Finder funcționează independent.");
    } else if (!wasReady && !recording && !connecting && !sampleBusy) {
      setSystemState("ready", "GPU pregătit");
      setStatus("În așteptare", "Pornește microfonul pentru a începe.");
    }
    ui.recordButton.disabled = !serverReady || connecting || stopping || sampleBusy;
    ui.youtubeForm.querySelector("button").disabled = !serverReady || recording || connecting || stopping || sampleBusy;
    ui.modelApplyButton.disabled = modelLoading || recording || connecting || stopping || sampleBusy || !ui.modelSelect.options.length;
    if (serverReady && !statusInitialized && status.sample) {
      showPlaybackSource(status.sample.audio_url, status.sample.name);
      const previousResponse = await fetch("/api/last-sample-result", { signal: AbortSignal.timeout(5000) });
      if (previousResponse.ok) applySampleResult(await previousResponse.json());
    }
    if (serverReady) statusInitialized = true;
  } catch (error) {
    serverReady = false;
    if (recording || connecting) await finishStop("Conexiunea la aplicația vocală s-a pierdut. Textul afișat a fost păstrat.");
    ui.recordButton.disabled = true;
    ui.youtubeForm.querySelector("button").disabled = true;
    ui.modelApplyButton.disabled = true;
    setSystemState("error", "Server indisponibil");
    setStatus("Deconectat", "Pornește din nou aplicația vocală. Reconectarea se verifică automat.");
  } finally {
    statusTimer = window.setTimeout(loadStatus, serverReady ? 10000 : 2500);
  }
}

ui.recordButton.addEventListener("click", () => {
  if (recording) stopRecording();
  else startRecording();
});

ui.modelApplyButton.addEventListener("click", selectModel);
ui.modelInstallForm.addEventListener("submit", installModel);

ui.clearButton.addEventListener("click", () => {
  stopPlaybackTranscription(false);
  playbackGeneration += 1;
  playbackHasStarted = false;
  ui.finalTranscript.replaceChildren();
  ui.finalTranscript.classList.remove("playback-mode");
  ui.partialTranscript.textContent = "";
  ui.emptyState.classList.remove("hidden");
  ui.playbackAnalysisStatus.textContent = "Alege un fișier sau lipește un link YouTube, apoi apasă redare pentru analiză.";
  renderBibleMatches([], true);
});

ui.audioPlayback.addEventListener("play", () => {
  startPlaybackTranscription();
});

ui.playbackSeconds.addEventListener("input", () => {
  updateAudioSettingLabels();
  saveVoiceSettingsSoon();
});
ui.playbackSilence.addEventListener("input", () => {
  updateAudioSettingLabels();
  saveVoiceSettingsSoon();
});
ui.playbackThreshold.addEventListener("input", () => {
  updateAudioSettingLabels();
  saveVoiceSettingsSoon();
});
function restartPlaybackForSettingChange() {
  playbackHasStarted = false;
  playbackSearchChunks = [];
  lastBibleQuery = "";
  if (!ui.audioPlayback.paused) startPlaybackTranscription();
}
ui.playbackSeconds.addEventListener("change", restartPlaybackForSettingChange);
ui.playbackSilence.addEventListener("change", restartPlaybackForSettingChange);
ui.playbackThreshold.addEventListener("change", restartPlaybackForSettingChange);

ui.audioPlayback.addEventListener("pause", () => {
  if (!ui.audioPlayback.ended) stopPlaybackTranscription(true);
});

ui.audioPlayback.addEventListener("seeking", () => {
  playbackWasPlayingBeforeSeek = !ui.audioPlayback.paused;
  stopPlaybackTranscription(false);
  playbackGeneration += 1;
  playbackSearchChunks = [];
  lastBibleQuery = "";
});

ui.audioPlayback.addEventListener("seeked", () => {
  if (playbackWasPlayingBeforeSeek && !ui.audioPlayback.paused) startPlaybackTranscription();
  playbackWasPlayingBeforeSeek = false;
});

ui.audioPlayback.addEventListener("ended", () => {
  stopPlaybackTranscription(true);
  ui.playbackAnalysisStatus.textContent = "Redarea și transcrierea pe segmente s-au încheiat.";
  setStatus("Redare finalizată", "Toate segmentele ascultate au fost procesate.");
});

ui.audioFile.addEventListener("change", async () => {
  const file = ui.audioFile.files?.[0];
  if (!file) return;
  if (recording || connecting || stopping) await finishStop();
  if (playbackObjectUrl) URL.revokeObjectURL(playbackObjectUrl);
  playbackObjectUrl = URL.createObjectURL(file);
  showPlaybackSource(playbackObjectUrl, file.name);
  setStatus("Fișier pregătit", "Apasă redare pentru transcriere în timp real.");
  setSystemState("ready", "GPU pregătit");
  ui.audioFile.value = "";
});

ui.youtubeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const url = ui.youtubeUrl.value.trim();
  if (!url) return;
  transcribeSample(
    "/api/transcribe-youtube",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    },
    "Eșantionul YouTube",
  );
});

window.addEventListener("resize", fitCanvas);
window.addEventListener("beforeunload", () => {
  window.clearTimeout(statusTimer);
  window.clearTimeout(settingsSaveTimer);
  stream?.getTracks().forEach((track) => track.stop());
  socket?.close();
  stopPlaybackTranscription(false);
  playbackAudioContext?.close();
  if (playbackObjectUrl) URL.revokeObjectURL(playbackObjectUrl);
});

loadVoiceSettings();
loadModels();
loadStatus();
