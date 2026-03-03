const voiceButton = document.querySelector("#voiceButton");
const stateLabel = document.querySelector("#stateLabel");
const errorText = document.querySelector("#errorText");
const audioPlayer = document.querySelector("#audioPlayer");
const characterCanvas = document.querySelector("#characterCanvas");
const characterFallback = document.querySelector("#characterFallback");

const dashboard = document.querySelector("#dashboard");
const currentTaskPanel = document.querySelector("#currentTask .panel-content");
const pinboardPanel = document.querySelector("#pinboard .panel-content");
const workNotesPanel = document.querySelector("#workNotes .panel-content");

const IDLE_MAIN_FRAME_KEY = "idleMain";
const IDLE_SMILE_FRAME_KEY = "idleSmile";
const PAUSE_FRAME_KEY = "speakPMN";
const VISEME_PAUSE = "__pause__";
const FRAME_SOURCES = {
  idleMain: "/assets/character/rick_main.jpeg",
  idleSmile: "/assets/character/rick_smiling.jpeg",
  speakAE: "/assets/character/rick_speak_ae.jpeg",
  speakLTSCH: "/assets/character/rick_speak_ltsch.jpeg",
  speakO: "/assets/character/rick_speak_o.jpeg",
  speakPMN: "/assets/character/rick_speak_pmn.jpeg",
  work0: "/assets/character/rick_work_a.jpeg",
  work1: "/assets/character/rick_work_b.jpeg",
  work2: "/assets/character/rick_work_c.jpeg",
  work3: "/assets/character/rick_work_d.jpeg",
};
const SPEAK_FRAME_KEYS = ["speakAE", "speakLTSCH", "speakO", "speakPMN"];
const WORK_FRAME_KEYS = ["work0", "work1", "work2", "work3"];
const WORK_TRANSITION_FRAME_KEY = "work3";
const WORK_TYPING_FRAME_KEYS = ["work0", "work1", "work2"];
const WORK_TRANSITION_MS = 180;
const WORK_TYPING_INTERVAL_MS = 145;
const RICK_CHROMA_KEY_PATTERN = /\/assets\/character\/rick_/;
const CHROMA_KEY_MIN_GREEN = 78;
const CHROMA_KEY_MIN_DOMINANCE = 18;
const CHROMA_KEY_SOFT_RANGE = 58;
const CHROMA_KEY_MAX_RED = 210;
const CHROMA_KEY_MAX_BLUE = 210;
const CHROMA_KEY_DESPILL = 0.68;
const SILENCE_THRESHOLD_RMS = 0.022;
const SILENCE_HOLD_MS = 150;
const MAX_TURN_WAIT_MS = 12 * 60 * 1000;

let mediaRecorder = null;
let mediaStream = null;
let chunks = [];
let isRecording = false;
let conversationId = null;
let speakText = "";

let speakingAnimationTimer = null;
let speakingSequence = [];
let speakingSequenceIndex = 0;
let isSpeechPaused = false;

let audioContext = null;
let analyserNode = null;
let mediaElementSourceNode = null;
let analyserData = null;
let monitorAnimationFrame = null;
let lastNonSilentAt = 0;
let currentAudioObjectUrl = null;
let isFinalPlaybackActive = false;
const ackAudioPlayer = new Audio();
let currentAckAudioObjectUrl = null;
let isAckPlaybackActive = false;
let currentPendingTurnId = null;
let abortTurnRequested = false;

let rendererReady = false;
let canvasContext = null;
let currentFrameKey = "";
const frameSurfaces = new Map();
let availableSpeakFrameKeys = [];
let activeTurnMetrics = null;
let workingAnimationTimer = null;
let workingTransitionTimer = null;
let workingFrameIndex = 0;
let availableWorkFrameKeys = [];
let currentTurnWs = null;
let idleAnimationTimer = null;
let idleUseSmileFrame = false;

function formatError(error) {
  if (!error) return "Unbekannter Fehler.";
  const name = error.name ? `${error.name}: ` : "";
  const message = error.message || String(error);
  return `${name}${message}`;
}

function showError(message) {
  errorText.hidden = false;
  errorText.textContent = message;
}

function clearError() {
  errorText.hidden = true;
  errorText.textContent = "";
}

function initTurnMetrics() {
  activeTurnMetrics = { requestStartedAt: performance.now() };
}

function logTurnMetrics(eventName) {
  if (!activeTurnMetrics) {
    return;
  }
  const report = { event: eventName };
  const now = performance.now();

  if (typeof activeTurnMetrics.requestStartedAt === "number") {
    report.client_elapsed_total = Math.round(now - activeTurnMetrics.requestStartedAt);
  }
  if (typeof activeTurnMetrics.responseReceivedAt === "number") {
    report.client_fetch = Math.round(activeTurnMetrics.responseReceivedAt - activeTurnMetrics.requestStartedAt);
  }
  if (typeof activeTurnMetrics.payloadParsedAt === "number") {
    report.client_json_parse = Math.round(activeTurnMetrics.payloadParsedAt - activeTurnMetrics.responseReceivedAt);
  }
  if (typeof activeTurnMetrics.playbackStartedAt === "number") {
    report.client_to_playback = Math.round(activeTurnMetrics.playbackStartedAt - activeTurnMetrics.requestStartedAt);
  }
  if (typeof activeTurnMetrics.playbackEndedAt === "number") {
    report.client_playback_duration = Math.round(
      activeTurnMetrics.playbackEndedAt - activeTurnMetrics.playbackStartedAt
    );
  }
  if (activeTurnMetrics.serverTimingHeader) {
    report.server_timing_header = activeTurnMetrics.serverTimingHeader;
  }
  if (activeTurnMetrics.serverTimings) {
    report.server = activeTurnMetrics.serverTimings;
  }
  console.info("[voice-bridge][latency_ms]", report);
}

function getFrameSurface(frameKey) {
  return frameSurfaces.get(frameKey) || frameSurfaces.get(IDLE_MAIN_FRAME_KEY) || null;
}

function drawFrame(frameKey) {
  if (!rendererReady || !canvasContext) {
    return;
  }
  const nextFrameKey = frameSurfaces.has(frameKey) ? frameKey : IDLE_MAIN_FRAME_KEY;
  if (nextFrameKey === currentFrameKey) {
    return;
  }
  const surface = getFrameSurface(nextFrameKey);
  if (!surface) {
    return;
  }
  // Prevent ghosting: transparent pixels from the new frame must not reveal previous frame content.
  canvasContext.clearRect(0, 0, characterCanvas.width, characterCanvas.height);
  canvasContext.drawImage(surface, 0, 0, characterCanvas.width, characterCanvas.height);
  currentFrameKey = nextFrameKey;
}

async function decodeFrameToSurface(sourceUrl) {
  try {
    const response = await fetch(sourceUrl, { cache: "force-cache" });
    if (!response.ok) {
      return null;
    }
    const blob = await response.blob();
    let sourceSurface = null;
    if ("createImageBitmap" in window) {
      sourceSurface = await createImageBitmap(blob);
    } else {
      sourceSurface = await new Promise((resolve) => {
        const objectUrl = URL.createObjectURL(blob);
        const image = new Image();
        image.decoding = "async";
        image.onload = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(image);
        };
        image.onerror = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(null);
        };
        image.src = objectUrl;
      });
    }
    if (!sourceSurface) {
      return null;
    }

    const width = sourceSurface.width || 2048;
    const height = sourceSurface.height || 2048;
    const workCanvas = document.createElement("canvas");
    workCanvas.width = width;
    workCanvas.height = height;
    const workContext = workCanvas.getContext("2d", { alpha: true, willReadFrequently: true });
    if (!workContext) {
      return sourceSurface;
    }

    workContext.drawImage(sourceSurface, 0, 0, width, height);
    if (RICK_CHROMA_KEY_PATTERN.test(sourceUrl)) {
      const imageData = workContext.getImageData(0, 0, width, height);
      const pixels = imageData.data;
      for (let index = 0; index < pixels.length; index += 4) {
        const r = pixels[index];
        const g = pixels[index + 1];
        const b = pixels[index + 2];
        const a = pixels[index + 3];
        if (a === 0) {
          continue;
        }
        if (g < CHROMA_KEY_MIN_GREEN || r > CHROMA_KEY_MAX_RED || b > CHROMA_KEY_MAX_BLUE) {
          continue;
        }
        const dominance = g - Math.max(r, b);
        if (dominance < CHROMA_KEY_MIN_DOMINANCE) {
          continue;
        }

        const dominanceWeight = Math.min(1, (dominance - CHROMA_KEY_MIN_DOMINANCE) / CHROMA_KEY_SOFT_RANGE);
        const greenWeight = Math.min(1, (g - CHROMA_KEY_MIN_GREEN) / 120);
        const keyStrength = dominanceWeight * (0.45 + greenWeight * 0.55);
        if (keyStrength <= 0) {
          continue;
        }

        const targetAlpha = Math.max(0, Math.round(a * (1 - keyStrength)));
        pixels[index + 3] = targetAlpha;

        const neutral = Math.max(r, b);
        const despillStrength = CHROMA_KEY_DESPILL * keyStrength;
        pixels[index + 1] = Math.max(
          0,
          Math.min(255, Math.round(g - (g - neutral) * despillStrength))
        );
      }
      workContext.putImageData(imageData, 0, 0);
    }

    if ("createImageBitmap" in window) {
      return await createImageBitmap(workCanvas);
    }
    return workCanvas;
  } catch {
    return null;
  }
}

function warmupFrameSurfaces() {
  const offscreenCanvas = document.createElement("canvas");
  offscreenCanvas.width = characterCanvas.width;
  offscreenCanvas.height = characterCanvas.height;
  const context = offscreenCanvas.getContext("2d", { alpha: true });
  if (!context) {
    return;
  }
  [IDLE_MAIN_FRAME_KEY, IDLE_SMILE_FRAME_KEY, ...availableSpeakFrameKeys, ...availableWorkFrameKeys].forEach((frameKey) => {
    const surface = getFrameSurface(frameKey);
    if (!surface) {
      return;
    }
    context.drawImage(surface, 0, 0, offscreenCanvas.width, offscreenCanvas.height);
  });
}

async function prepareCharacterRenderer() {
  const frameEntries = Object.entries(FRAME_SOURCES);
  const decodedFrames = await Promise.all(
    frameEntries.map(async ([frameKey, sourceUrl]) => [frameKey, await decodeFrameToSurface(sourceUrl)])
  );

  decodedFrames.forEach(([frameKey, surface]) => {
    if (surface) {
      frameSurfaces.set(frameKey, surface);
    }
  });

  const idleSurface = getFrameSurface(IDLE_MAIN_FRAME_KEY);
  if (!idleSurface) {
    rendererReady = false;
    return;
  }

  availableSpeakFrameKeys = SPEAK_FRAME_KEYS.filter((frameKey) => frameSurfaces.has(frameKey));
  availableWorkFrameKeys = WORK_FRAME_KEYS.filter((frameKey) => frameSurfaces.has(frameKey));
  canvasContext = characterCanvas.getContext("2d", { alpha: true, desynchronized: true });
  if (!canvasContext) {
    rendererReady = false;
    return;
  }
  characterCanvas.width = idleSurface.width || 1024;
  characterCanvas.height = idleSurface.height || 1024;
  canvasContext.imageSmoothingEnabled = true;
  canvasContext.clearRect(0, 0, characterCanvas.width, characterCanvas.height);
  rendererReady = true;
  warmupFrameSurfaces();
  drawFrame(IDLE_MAIN_FRAME_KEY);
  characterCanvas.classList.add("ready");
  characterFallback.hidden = true;
  setState(stateLabel.textContent || "idle");
}

function stopAudioLevelMonitor() {
  if (monitorAnimationFrame) {
    window.cancelAnimationFrame(monitorAnimationFrame);
    monitorAnimationFrame = null;
  }
  isSpeechPaused = false;
}

function startAudioLevelMonitor() {
  stopAudioLevelMonitor();
  if (!audioContext || !analyserNode || !analyserData) {
    return;
  }
  lastNonSilentAt = performance.now();
  isSpeechPaused = false;
  const loop = () => {
    analyserNode.getByteTimeDomainData(analyserData);
    let sumSquares = 0;
    for (let index = 0; index < analyserData.length; index += 1) {
      const normalized = (analyserData[index] - 128) / 128;
      sumSquares += normalized * normalized;
    }
    const rms = Math.sqrt(sumSquares / analyserData.length);
    const now = performance.now();
    if (rms >= SILENCE_THRESHOLD_RMS) {
      lastNonSilentAt = now;
      isSpeechPaused = false;
    } else {
      isSpeechPaused = now - lastNonSilentAt >= SILENCE_HOLD_MS;
    }
    monitorAnimationFrame = window.requestAnimationFrame(loop);
  };
  monitorAnimationFrame = window.requestAnimationFrame(loop);
}

function ensureAudioAnalysis() {
  if (!window.AudioContext && !window.webkitAudioContext) {
    return false;
  }
  if (!audioContext) {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    audioContext = new AudioContextCtor();
  }
  if (!analyserNode) {
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 1024;
    analyserNode.smoothingTimeConstant = 0.22;
  }
  if (!mediaElementSourceNode) {
    mediaElementSourceNode = audioContext.createMediaElementSource(audioPlayer);
    mediaElementSourceNode.connect(analyserNode);
    analyserNode.connect(audioContext.destination);
  }
  if (!analyserData) {
    analyserData = new Uint8Array(analyserNode.fftSize);
  }
  return true;
}

function revokeCurrentAudioObjectUrl() {
  if (!currentAudioObjectUrl) {
    return;
  }
  URL.revokeObjectURL(currentAudioObjectUrl);
  currentAudioObjectUrl = null;
}

function revokeCurrentAckAudioObjectUrl() {
  if (!currentAckAudioObjectUrl) {
    return;
  }
  URL.revokeObjectURL(currentAckAudioObjectUrl);
  currentAckAudioObjectUrl = null;
}

function setAudioSourceFromBase64(audioBase64, audioMime) {
  const binaryText = window.atob(audioBase64);
  const binaryLength = binaryText.length;
  const bytes = new Uint8Array(binaryLength);
  for (let index = 0; index < binaryLength; index += 1) {
    bytes[index] = binaryText.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: audioMime || "audio/mpeg" });
  revokeCurrentAudioObjectUrl();
  currentAudioObjectUrl = URL.createObjectURL(blob);
  audioPlayer.src = currentAudioObjectUrl;
}

function getPauseFrameKey() {
  return frameSurfaces.has(PAUSE_FRAME_KEY) ? PAUSE_FRAME_KEY : IDLE_MAIN_FRAME_KEY;
}

function resolveSpeechFrameKey(key) {
  if (frameSurfaces.has(key)) {
    return key;
  }
  if (availableSpeakFrameKeys.length > 0) {
    return availableSpeakFrameKeys[0];
  }
  return IDLE_MAIN_FRAME_KEY;
}

function buildSpeechFrameSequence(text) {
  const normalized = (text || "").toLowerCase();
  if (!normalized) {
    return [getPauseFrameKey()];
  }

  const sequence = [];
  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
    const next = normalized[index + 1] || "";
    const nextTwo = normalized.slice(index, index + 3);

    if (/\s/.test(char)) {
      sequence.push(VISEME_PAUSE);
      continue;
    }
    if (/[.,!?;:]/.test(char)) {
      sequence.push(VISEME_PAUSE, VISEME_PAUSE);
      continue;
    }
    if (nextTwo === "sch") {
      sequence.push(resolveSpeechFrameKey("speakLTSCH"));
      index += 2;
      continue;
    }
    if ((char === "c" && next === "h") || (char === "s" && next === "h") || (char === "t" && next === "s")) {
      sequence.push(resolveSpeechFrameKey("speakLTSCH"));
      index += 1;
      continue;
    }
    if (/[aeiyäêéè]/.test(char)) {
      sequence.push(resolveSpeechFrameKey("speakAE"));
      continue;
    }
    if (/[ouöüw]/.test(char)) {
      sequence.push(resolveSpeechFrameKey("speakO"));
      continue;
    }
    if (/[bmpn]/.test(char)) {
      sequence.push(resolveSpeechFrameKey("speakPMN"));
      continue;
    }
    if (/[ltszdxjcrqkgfhv]/.test(char)) {
      sequence.push(resolveSpeechFrameKey("speakLTSCH"));
      continue;
    }
    sequence.push(VISEME_PAUSE);
  }

  return sequence.length > 0 ? sequence : [getPauseFrameKey()];
}

function stopSpeakingAnimation() {
  if (speakingAnimationTimer) {
    window.clearInterval(speakingAnimationTimer);
    speakingAnimationTimer = null;
  }
  stopAudioLevelMonitor();
  speakingSequence = [];
  speakingSequenceIndex = 0;
}

function startSpeakingAnimation() {
  stopSpeakingAnimation();
  if (!rendererReady) {
    drawFrame(IDLE_MAIN_FRAME_KEY);
    return;
  }
  speakingSequence = buildSpeechFrameSequence(speakText);
  speakingSequenceIndex = 0;
  drawFrame(getPauseFrameKey());
  speakingAnimationTimer = window.setInterval(() => {
    if (isSpeechPaused) {
      drawFrame(getPauseFrameKey());
      return;
    }
    const currentToken = speakingSequence[speakingSequenceIndex] || VISEME_PAUSE;
    if (currentToken === VISEME_PAUSE) {
      drawFrame(getPauseFrameKey());
    } else {
      drawFrame(currentToken);
    }
    speakingSequenceIndex = (speakingSequenceIndex + 1) % speakingSequence.length;
  }, 95);
}

function stopIdleAnimation() {
  if (idleAnimationTimer) {
    window.clearInterval(idleAnimationTimer);
    idleAnimationTimer = null;
  }
}

function startIdleAnimation() {
  stopIdleAnimation();
  if (!rendererReady) {
    return;
  }
  if (!frameSurfaces.has(IDLE_SMILE_FRAME_KEY)) {
    drawFrame(IDLE_MAIN_FRAME_KEY);
    return;
  }
  idleUseSmileFrame = false;
  drawFrame(IDLE_MAIN_FRAME_KEY);
  idleAnimationTimer = window.setInterval(() => {
    idleUseSmileFrame = !idleUseSmileFrame;
    drawFrame(idleUseSmileFrame ? IDLE_SMILE_FRAME_KEY : IDLE_MAIN_FRAME_KEY);
  }, 2200);
}

function stopWorkingAnimation() {
  if (workingTransitionTimer) {
    window.clearTimeout(workingTransitionTimer);
    workingTransitionTimer = null;
  }
  if (workingAnimationTimer) {
    window.clearInterval(workingAnimationTimer);
    workingAnimationTimer = null;
  }
}

function startWorkingAnimation() {
  stopWorkingAnimation();
  if (!rendererReady || availableWorkFrameKeys.length === 0) {
    drawFrame(IDLE_MAIN_FRAME_KEY);
    return;
  }
  const typingFrames = WORK_TYPING_FRAME_KEYS.filter((frameKey) => frameSurfaces.has(frameKey));
  const fallbackFrames = availableWorkFrameKeys.filter((frameKey) => frameKey !== WORK_TRANSITION_FRAME_KEY);
  const loopFrames = typingFrames.length > 0 ? typingFrames : fallbackFrames;
  const transitionFrame = frameSurfaces.has(WORK_TRANSITION_FRAME_KEY) ? WORK_TRANSITION_FRAME_KEY : null;

  if (loopFrames.length === 0) {
    drawFrame(transitionFrame || IDLE_MAIN_FRAME_KEY);
    return;
  }

  const startTypingLoop = () => {
    workingFrameIndex = 0;
    drawFrame(loopFrames[0]);
    workingAnimationTimer = window.setInterval(() => {
      drawFrame(loopFrames[workingFrameIndex]);
      workingFrameIndex = (workingFrameIndex + 1) % loopFrames.length;
    }, WORK_TYPING_INTERVAL_MS);
  };

  if (transitionFrame) {
    drawFrame(transitionFrame);
    workingTransitionTimer = window.setTimeout(() => {
      workingTransitionTimer = null;
      startTypingLoop();
    }, WORK_TRANSITION_MS);
    return;
  }

  startTypingLoop();
}

function updateDashboard(panels) {
  if (!panels) {
    return;
  }
  dashboard.hidden = false;

  if (panels.current_task && panels.current_task.title) {
    let html = `<div class="task-title">${escapeHtml(panels.current_task.title)}</div>`;
    if (panels.current_task.steps && panels.current_task.steps.length > 0) {
      html += "<ul>" + panels.current_task.steps.map((s) => `<li>${escapeHtml(s)}</li>`).join("") + "</ul>";
    }
    currentTaskPanel.innerHTML = html;
  } else {
    currentTaskPanel.innerHTML = '<span class="empty">Keine aktive Aufgabe</span>';
  }

  if (panels.pinboard && panels.pinboard.length > 0) {
    pinboardPanel.innerHTML = "<ul>" + panels.pinboard.map((item) => `<li>${escapeHtml(item)}</li>`).join("") + "</ul>";
  } else {
    pinboardPanel.innerHTML = '<span class="empty">Leer</span>';
  }

  if (panels.work_notes && panels.work_notes.length > 0) {
    workNotesPanel.innerHTML =
      "<ul>" + panels.work_notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("") + "</ul>";
  } else {
    workNotesPanel.innerHTML = '<span class="empty">Keine Notizen</span>';
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function setState(state) {
  stateLabel.textContent = state;
  voiceButton.classList.remove("idle", "listening", "thinking", "speaking");
  voiceButton.classList.add(state);
  stopWorkingAnimation();
  stopSpeakingAnimation();
  stopIdleAnimation();
  if (state === "speaking") {
    startSpeakingAnimation();
  } else if (state === "thinking") {
    startWorkingAnimation();
  } else if (state === "listening") {
    drawFrame(frameSurfaces.has(IDLE_SMILE_FRAME_KEY) ? IDLE_SMILE_FRAME_KEY : IDLE_MAIN_FRAME_KEY);
  } else {
    startIdleAnimation();
  }
}

function stopPlayback() {
  if (activeTurnMetrics) {
    logTurnMetrics("playback_stopped");
  }
  isFinalPlaybackActive = false;
  speakText = "";
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  revokeCurrentAudioObjectUrl();
  stopAckPlayback();
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  stopWorkingAnimation();
  stopIdleAnimation();
  stopSpeakingAnimation();
  drawFrame(IDLE_MAIN_FRAME_KEY);
  activeTurnMetrics = null;
}

function speakWithBrowserVoice(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !text) {
      resolve(false);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "de-DE";
    utterance.onend = () => resolve(true);
    utterance.onerror = () => resolve(false);
    setState("speaking");
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  });
}

async function startRecording() {
  clearError();
  stopPlayback();
  if (!window.isSecureContext) {
    throw new Error("Mikrofon braucht HTTPS (sicherer Kontext).");
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Dein Browser unterstuetzt keine Mikrofonaufnahme.");
  }
  if (!window.MediaRecorder) {
    throw new Error("MediaRecorder wird in diesem Browser nicht unterstuetzt.");
  }

  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const preferredMime = "audio/webm;codecs=opus";
  const recorderOptions = MediaRecorder.isTypeSupported(preferredMime)
    ? { mimeType: preferredMime }
    : undefined;
  mediaRecorder = new MediaRecorder(mediaStream, recorderOptions);
  chunks = [];

  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) {
      chunks.push(event.data);
    }
  });

  mediaRecorder.addEventListener("stop", async () => {
    const outputType = mediaRecorder.mimeType || "audio/webm";
    const blob = new Blob(chunks, { type: outputType });
    await sendTurn(blob);
    mediaRecorder = null;
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
  });

  mediaRecorder.start();
  isRecording = true;
  setState("listening");
}

function stopRecording() {
  if (!mediaRecorder || !isRecording) {
    return;
  }
  isRecording = false;
  setState("thinking");
  mediaRecorder.stop();
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function playAckSpeech(ackText) {
  const normalized = (ackText || "").trim();
  if (!normalized) {
    return Promise.resolve(false);
  }
  return speakWithBrowserVoice(normalized)
    .catch(() => false)
    .finally(() => {
      if (!isRecording && !isFinalPlaybackActive) {
        setState("thinking");
      }
    });
}

function stopAckPlayback() {
  isAckPlaybackActive = false;
  ackAudioPlayer.pause();
  ackAudioPlayer.removeAttribute("src");
  ackAudioPlayer.load();
  revokeCurrentAckAudioObjectUrl();
}

function playAckAudioFromBase64(audioBase64, audioMime) {
  if (!audioBase64) {
    return Promise.resolve(false);
  }
  stopAckPlayback();
  return new Promise((resolve) => {
    const binaryText = window.atob(audioBase64);
    const bytes = new Uint8Array(binaryText.length);
    for (let index = 0; index < binaryText.length; index += 1) {
      bytes[index] = binaryText.charCodeAt(index);
    }
    const blob = new Blob([bytes], { type: audioMime || "audio/mpeg" });
    currentAckAudioObjectUrl = URL.createObjectURL(blob);
    ackAudioPlayer.src = currentAckAudioObjectUrl;
    ackAudioPlayer.preload = "auto";

    const finalize = (ok) => {
      ackAudioPlayer.onended = null;
      ackAudioPlayer.onerror = null;
      isAckPlaybackActive = false;
      stopAckPlayback();
      if (!isRecording && !isFinalPlaybackActive) {
        setState("thinking");
      }
      resolve(ok);
    };

    isAckPlaybackActive = true;
    setState("speaking");
    ackAudioPlayer.onended = () => finalize(true);
    ackAudioPlayer.onerror = () => finalize(false);
    ackAudioPlayer.play().catch(() => finalize(false));
  });
}

function waitForTurnResultWs(turnId) {
  return new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/ws/turn/${encodeURIComponent(turnId)}`);
    currentTurnWs = ws;

    const timeout = window.setTimeout(() => {
      ws.close();
      reject(new Error("Antwort dauert zu lange (WebSocket Timeout)."));
    }, MAX_TURN_WAIT_MS);

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "progress") {
        console.info("[voice-bridge][ws]", msg.stage, msg.message);
      }
      if (msg.type === "result") {
        window.clearTimeout(timeout);
        currentTurnWs = null;
        ws.close();
        resolve(msg);
      }
      if (msg.type === "error") {
        window.clearTimeout(timeout);
        currentTurnWs = null;
        ws.close();
        reject(new Error(msg.message || "Turn fehlgeschlagen."));
      }
    };

    ws.onerror = () => {
      window.clearTimeout(timeout);
      currentTurnWs = null;
      console.warn("[voice-bridge] WebSocket Fehler, fallback auf Polling");
      waitForTurnResultPolling(turnId).then(resolve).catch(reject);
    };

    ws.onclose = (event) => {
      window.clearTimeout(timeout);
      currentTurnWs = null;
    };
  });
}

async function waitForTurnResultPolling(turnId) {
  const startedAt = performance.now();
  const intervalMs = 1200;
  while (true) {
    if (abortTurnRequested) {
      await fetch(`/api/voice/turn/cancel/${encodeURIComponent(turnId)}`, {
        method: "POST",
      }).catch(() => {});
      throw new Error("Vorgang wurde abgebrochen.");
    }
    const response = await fetch(`/api/voice/turn/status/${encodeURIComponent(turnId)}`, {
      method: "GET",
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.message || "Turn-Status fehlgeschlagen.");
    }
    if (payload.status === "completed" && payload.result) {
      return {
        type: "result",
        turn_id: payload.turn_id,
        conversation_id: payload.result.conversation_id,
        voice_response: payload.result.speak_text,
        panels: payload.result.panels || null,
        audio_base64: payload.result.audio_base64,
        audio_mime: payload.result.audio_mime,
      };
    }
    if (payload.status === "failed") {
      throw new Error(payload.error?.message || payload.error?.error_class || "Turn fehlgeschlagen.");
    }
    if (payload.status === "cancelled") {
      throw new Error(payload.progress_message || "Vorgang wurde abgebrochen.");
    }
    if (performance.now() - startedAt > MAX_TURN_WAIT_MS) {
      throw new Error("Antwort dauert zu lange (Timeout beim Polling).");
    }
    await sleep(intervalMs);
  }
}

async function sendTurn(blob) {
  const form = new FormData();
  form.append("audio", blob, "voice-turn.webm");
  if (conversationId) {
    form.append("conversation_id", conversationId);
  }

  try {
    abortTurnRequested = false;
    initTurnMetrics();
    const startResponse = await fetch("/api/voice/turn/start", {
      method: "POST",
      body: form,
    });
    const startPayload = await startResponse.json();
    if (!startResponse.ok) {
      throw new Error(startPayload.message || startPayload.error_class || "Unbekannter Fehler");
    }

    if (activeTurnMetrics) {
      activeTurnMetrics.turnId = startPayload.turn_id || null;
      logTurnMetrics("turn_accepted");
    }

    conversationId = startPayload.conversation_id;
    currentPendingTurnId = startPayload.turn_id;
    setState("thinking");
    stopAckPlayback();

    const wsResult = await waitForTurnResultWs(startPayload.turn_id);
    currentPendingTurnId = null;

    if (activeTurnMetrics) {
      activeTurnMetrics.responseReceivedAt = performance.now();
      activeTurnMetrics.payloadParsedAt = performance.now();
      activeTurnMetrics.serverTimings = null;
      activeTurnMetrics.serverTimingHeader = "";
      logTurnMetrics("response_received");
    }

    conversationId = wsResult.conversation_id;
    speakText = wsResult.voice_response || "";
    updateDashboard(wsResult.panels);
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    stopAckPlayback();
    isFinalPlaybackActive = true;

    if (wsResult.audio_base64 && wsResult.audio_mime) {
      setAudioSourceFromBase64(wsResult.audio_base64, wsResult.audio_mime);
      setState("speaking");
      if (ensureAudioAnalysis()) {
        audioContext.resume().catch(() => {});
        startAudioLevelMonitor();
      }
      try {
        await audioPlayer.play();
        if (activeTurnMetrics) {
          activeTurnMetrics.playbackStartedAt = performance.now();
          logTurnMetrics("playback_started");
        }
      } catch (playError) {
        stopAudioLevelMonitor();
        revokeCurrentAudioObjectUrl();
        if (activeTurnMetrics) {
          activeTurnMetrics.playbackStartedAt = performance.now();
          logTurnMetrics("playback_started_fallback");
        }
        const fallbackOk = await speakWithBrowserVoice(speakText);
        if (fallbackOk && activeTurnMetrics) {
          activeTurnMetrics.playbackEndedAt = performance.now();
          logTurnMetrics("playback_ended_fallback");
          activeTurnMetrics = null;
        }
        isFinalPlaybackActive = false;
        setState("idle");
        if (!fallbackOk) {
          throw playError;
        }
      }
    } else {
      if (activeTurnMetrics) {
        activeTurnMetrics.playbackStartedAt = performance.now();
        logTurnMetrics("playback_started_fallback");
      }
      const fallbackOk = await speakWithBrowserVoice(speakText);
      if (fallbackOk && activeTurnMetrics) {
        activeTurnMetrics.playbackEndedAt = performance.now();
        logTurnMetrics("playback_ended_fallback");
        activeTurnMetrics = null;
      }
      isFinalPlaybackActive = false;
      setState("idle");
      if (!fallbackOk) {
        showError("Server-TTS nicht verfuegbar und Browser-Stimme fehlgeschlagen.");
        activeTurnMetrics = null;
      }
    }
  } catch (error) {
    if (activeTurnMetrics) {
      logTurnMetrics("request_failed");
    }
    isFinalPlaybackActive = false;
    currentPendingTurnId = null;
    setState("idle");
    showError(error.message || "Request fehlgeschlagen.");
    activeTurnMetrics = null;
  }
}

async function cancelPendingTurn() {
  if (!currentPendingTurnId) {
    return;
  }
  abortTurnRequested = true;
  if (currentTurnWs) {
    currentTurnWs.close();
    currentTurnWs = null;
  }
  try {
    await fetch(`/api/voice/turn/cancel/${encodeURIComponent(currentPendingTurnId)}`, {
      method: "POST",
    });
  } catch {
    // best effort
  }
}

voiceButton.addEventListener("click", async () => {
  try {
    if (stateLabel.textContent === "thinking") {
      await cancelPendingTurn();
      return;
    }
    if (isRecording) {
      stopRecording();
    } else {
      await startRecording();
    }
  } catch (error) {
    setState("idle");
    showError(formatError(error));
  }
});

audioPlayer.addEventListener("ended", () => {
  stopAudioLevelMonitor();
  revokeCurrentAudioObjectUrl();
  isFinalPlaybackActive = false;
  if (activeTurnMetrics && typeof activeTurnMetrics.playbackStartedAt === "number") {
    activeTurnMetrics.playbackEndedAt = performance.now();
    logTurnMetrics("playback_ended");
  }
  activeTurnMetrics = null;
  currentPendingTurnId = null;
  setState("idle");
});

audioPlayer.addEventListener("error", () => {
  stopAudioLevelMonitor();
  revokeCurrentAudioObjectUrl();
  isFinalPlaybackActive = false;
  if (activeTurnMetrics) {
    logTurnMetrics("audio_error");
  }
  if (!speakText) {
    setState("idle");
    showError("Audio konnte nicht abgespielt werden.");
    activeTurnMetrics = null;
    return;
  }
  speakWithBrowserVoice(speakText)
    .then((ok) => {
      if (!ok) {
        setState("idle");
        showError("Audio konnte nicht abgespielt werden.");
      }
    })
    .catch(() => {
      setState("idle");
      showError("Audio konnte nicht abgespielt werden.");
    })
    .finally(() => {
      activeTurnMetrics = null;
      currentPendingTurnId = null;
    });
});

prepareCharacterRenderer().catch(() => {
  rendererReady = false;
});
setState("idle");
if (!window.isSecureContext) {
  showError("Unsicherer Kontext: bitte ueber HTTPS oeffnen.");
}
