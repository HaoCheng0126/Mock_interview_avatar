/* Interview page logic — served at /interview.js, loaded by interview.html.
   Layout: avatar video stays on the left; the right panel switches between the
   prep form (data-view=prep) and the live conversation (data-view=interview);
   the final report is full-screen (data-view=finished). The prep page shows a
   static interviewer poster; the real avatar session starts only when the
   candidate clicks "开始面试". */

// Audio/text answers are accepted only after the interviewer has completely
// finished speaking and the backend explicitly opens the answer floor.
const LISTENING_STATES = new Set(["listening"]);
const TYPING_STATES = new Set([
  "analyzing",
  "deciding_followup",
  "planning_followup",
  "transitioning",
  "closing",
]);
const TERMINAL_STATES = new Set(["completed", "terminated"]);
const REPORT_STATES = new Set(["report_generating", "report_error", "closing"]);
const IDLE_RELEASE_MS = 8 * 60 * 1000;
const URL_PARAMS = new URLSearchParams(location.search);
const DEBUG = URL_PARAMS.has("debug");
const DEBUG_REPORT_ONLY = DEBUG && URL_PARAMS.get("report") === "1";
const ENTERPRISE_MODE = location.pathname === "/enterprise";

// Live mic metering threshold (RMS of the time-domain signal, 0..1).
const SPEECH_RMS = 0.045;   // above this the waveform is "voiced" and animates

const TERMINATION_TEXT = {
  user_stopped: "你主动结束了本场面试",
  too_many_no_answer_timeouts: "多道题未收到回答，面试提前结束",
  insufficient_effective_answers: "有效回答不足，面试提前结束",
};
const SKIP_REASON_TEXT = { hard_timeout_no_answer: "该题超时未作答，已跳过" };

const S = {
  client: null,
  sessionReady: false,
  preheat: "idle",
  asrAvailable: false,
  directAsrAvailable: false,
  sessionId: "",
  closedSessionId: "",
  directAsrWs: null,
  directAsrConnectPromise: null,
  directAsrStream: null,
  directAsrAudioCtx: null,
  directAsrSource: null,
  directAsrNode: null,
  directAsrSink: null,
  directAsrPrepared: false,
  directAsrCapturing: false,
  directAsrStartResolve: null,
  directAsrStartReject: null,
  directAsrStartExchangeId: "",
  activeAsrTransport: null,
  activeCaptureExchangeId: "",
  browserAsrExchangeId: "",
  browserSubmitTimer: null,
  useBrowserAsr: false,
  voiceReplyEnabled: true,
  micDesired: false,
  micTransition: Promise.resolve(),
  interviewStarted: false,
  interviewOver: false,
  endingInterview: false,
  reportDismissed: false,
  viewingReport: false,
  reportInterviewId: localStorage.getItem("lastReportInterviewId") || "",
  backgroundReportStatus: null,
  lastCompletedReportStatus: null,
  inputMode: localStorage.getItem("interviewInputMode") || "voice",
  micOn: false,
  pollTimer: null,
  idleTimer: null,
  levelRaf: null,
  starting: false,
  questionCount: 0,
  lastStatus: null,
  roster: null,
  selectedAvatar: null,
  appliedAvatarDefaults: { role: "", jd: "" },
  // live mic metering (Web Audio)
  meterStream: null,
  audioCtx: null,
  analyser: null,
  timeData: null,
  micLevel: 0,
  captureStartedAt: 0,
  captureVoicedMs: 0,
  // Browser Web Speech API
  speechRecognition: null,
  isListening: false,
  interimText: "",
  collectedTranscript: "",
  // One visual answer may contain several ASR utterances. Keep all finalized
  // utterances here so provider segmentation never creates/replaces bubbles.
  voiceCaptionCommitted: "",
  voiceCaptionInterim: "",
  prepPromptPlayed: false,
  prepSpeech: null,
  prepAudio: null,
};
const renderedTurns = new Map();
let profileDirty = false;
let _startInflight = null;

function $(id) { return document.getElementById(id); }
function setView(view) { document.body.dataset.view = view; }

function toast(msg, ok = true) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "show " + (ok ? "ok" : "err");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ""; }, ok ? 2600 : 6000);
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

/* ---------------- platform error mapping (by name, then code) ---------------- */

function mapPlatformError(raw) {
  const text = String(raw || "");
  if (text.includes("上一场面试报告尚未生成完成")) {
    return {
      title: "上一场报告仍待处理",
      hint: "请从右上角报告入口继续生成；如果上一场没有正式开始，请刷新页面后重试。",
      retry: "manual",
      detail: text,
    };
  }
  const m = text.match(/\[(\d+)\]\s*([A-Z_]+)/);
  const code = m ? m[1] : "";
  const name = m ? m[2] : "";
  const byName = {
    SESSION_START_FAILED: { title: "数字人尚未就绪", hint: "请在 LiveAvatar 控制台确认该数字人形象已「发布并共享」后重试。", retry: "manual" },
    PRINCIPAL_UNIDENTIFIED: { title: "平台 API Key 无效", hint: "请在控制台（localhost:8000）检查平台 API Key。", retry: "none" },
    CONCURRENCY_LIMIT_EXCEEDED: { title: "并发会话已达上限", hint: "请关闭其他进行中的会话后重试，或升级套餐。", retry: "manual" },
    QUOTA_EXHAUSTED: { title: "使用额度已用尽", hint: "请在平台控制台充值，或切换到沙箱环境。", retry: "none" },
    SESSION_ACCESS_DENIED: { title: "无权访问该会话", hint: "请检查凭证归属后重试。", retry: "none" },
  };
  const byCode = {
    40001: { title: "平台资源繁忙", hint: "调度层暂时满载，稍等片刻重试。", retry: "manual" },
    40002: { title: "平台资源繁忙", hint: "渲染层暂时满载，稍等片刻重试。", retry: "manual" },
  };
  const entry = byName[name] || byCode[code];
  if (entry) return { ...entry, detail: text };
  return { title: "连接失败", hint: "请检查网络与后端服务后重试。", retry: "manual", detail: text };
}

/* ---------------- readiness / prep error ---------------- */

function setReadiness(state, text) {
  S.preheat = state;
  const chip = $("readiness");
  chip.dataset.state = state;
  chip.querySelector("span").textContent = text;
}

function setStageTag(text) {
  $("stage-tag-text").textContent = text || "面试准备";
}

function setAvatarHint(text, live = false) {
  if (live) { document.body.dataset.avatar = "live"; return; }
  document.body.removeAttribute("data-avatar");
  $("avatar-hint-text").textContent = text;
}

function getAvatarBySlug(slug) {
  return (S.roster?.avatars || []).find((avatar) => avatar.slug === slug) || null;
}

function avatarBadgeText(name, slug) {
  const source = String(name || slug || "AI").trim();
  const compact = source.replace(/\s+/g, "");
  return compact.slice(0, 2).toUpperCase();
}

function renderAvatarPoster(slug) {
  const avatar = getAvatarBySlug(slug) || {};
  const title = avatar.name || slug || "AI 面试官";
  const direction = avatar.direction || "综合面试";
  const copy = avatar.prepText || "请先填写岗位名称、岗位 JD 和简历，准备好后开始面试。";
  const stage = $("avatar-stage");
  const posterUrl = String(avatar.posterUrl || avatar.poster_url || "").trim();
  if (posterUrl) {
    stage.innerHTML = `
      <div class="avatar-poster has-image">
        <img class="avatar-poster-image" src="${escapeHtml(posterUrl)}" alt="${escapeHtml(title)}">
        <div class="avatar-poster-shade"></div>
        <div class="avatar-poster-meta">
          <div class="avatar-poster-name">${escapeHtml(title)}</div>
          <div class="avatar-poster-dir">${escapeHtml(direction)}</div>
          <div class="avatar-poster-copy">${escapeHtml(copy)}</div>
        </div>
      </div>
    `;
  } else {
    stage.innerHTML = `
      <div class="avatar-poster">
        <div class="avatar-poster-card">
          <div class="avatar-poster-badge">${escapeHtml(avatarBadgeText(title, slug))}</div>
          <div>
            <div class="avatar-poster-name">${escapeHtml(title)}</div>
            <div class="avatar-poster-dir">${escapeHtml(direction)}</div>
          </div>
          <div class="avatar-poster-copy">${escapeHtml(copy)}</div>
        </div>
      </div>
    `;
  }
  document.body.dataset.avatar = "static";
  $("conn-dot").dataset.state = "off";
  setStageTag(title);
}

function stopPrepSpeech() {
  S.prepSpeech = null;
  if (S.prepAudio) {
    try {
      S.prepAudio.pause();
      S.prepAudio.currentTime = 0;
    } catch (e) { /* ignore */ }
    S.prepAudio = null;
  }
  if (!window.speechSynthesis) return;
  try { window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
}

function speakPrepPrompt(avatar, opts = {}) {
  if (!avatar) return;
  const text = String(avatar.prepText || "").trim();
  stopPrepSpeech();
  const prepAudioUrl = String(avatar.prepAudioUrl || "").trim();
  if (prepAudioUrl) {
    try {
      const audio = new Audio(prepAudioUrl);
      audio.preload = "auto";
      audio.volume = 1;
      S.prepAudio = audio;
      audio.play().catch(() => {
        S.prepAudio = null;
        fallbackSpeakPrepText(text);
      });
      return;
    } catch (e) {
      S.prepAudio = null;
    }
  }
  fallbackSpeakPrepText(text);
}

function fallbackSpeakPrepText(text) {
  if (!text || !window.speechSynthesis || typeof window.SpeechSynthesisUtterance !== "function") return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  utterance.rate = 1;
  utterance.pitch = 1;
  utterance.volume = 1;
  S.prepSpeech = utterance;
  try { window.speechSynthesis.speak(utterance); } catch (e) { /* ignore autoplay/user-gesture issues */ }
}

function showPrepError(err) {
  const info = mapPlatformError(err && err.message ? err.message : String(err));
  $("prep-error-title").textContent = info.title;
  $("prep-error-hint").textContent = info.hint;
  $("prep-error-detail").textContent = info.detail;
  $("prep-error").classList.add("show");
  setAvatarHint(info.title);
}

function clearPrepError() { $("prep-error").classList.remove("show"); }

/* ---------------- session preheat & lifecycle ---------------- */

function directAsrSocketUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/interview/asr?sessionId=${encodeURIComponent(S.sessionId)}`;
}

function currentExchangeId(status = S.lastStatus) {
  return String(status?.currentExchange?.exchangeId || "");
}

function captureAllowedFor(status, exchangeId) {
  return Boolean(
    status?.captureAllowed === true &&
    LISTENING_STATES.has(status.state) &&
    exchangeId &&
    currentExchangeId(status) === exchangeId
  );
}

function ensureDirectAsrSocket() {
  if (
    S.directAsrWs &&
    S.directAsrWs.readyState === WebSocket.OPEN
  ) return Promise.resolve(S.directAsrWs);
  if (S.directAsrConnectPromise) return S.directAsrConnectPromise;
  if (!S.directAsrAvailable || !S.sessionId) {
    return Promise.reject(new Error("直连 ASR 尚未就绪"));
  }

  const socket = new WebSocket(directAsrSocketUrl());
  socket.binaryType = "arraybuffer";
  S.directAsrWs = socket;
  let settled = false;
  let timer;
  const promise = new Promise((resolve, reject) => {
    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error instanceof Error ? error : new Error(String(error || "ASR 连接失败")));
    };
    timer = setTimeout(() => {
      try { socket.close(); } catch (e) { /* ignore */ }
      fail(new Error("ASR 直连超时"));
    }, 4000);
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (e) { return; }
      if (message.type === "ready") {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          debugLog("direct ASR ready");
          resolve(socket);
        }
        return;
      }
      if (message.type === "started") {
        if (
          S.directAsrStartResolve &&
          String(message.exchangeId || "") === S.directAsrStartExchangeId
        ) {
          const resolveStarted = S.directAsrStartResolve;
          S.directAsrStartResolve = null;
          S.directAsrStartReject = null;
          S.directAsrStartExchangeId = "";
          resolveStarted(message);
        }
        return;
      }
      if (
        (message.type === "interim" || message.type === "final") &&
        typeof message.text === "string" &&
        captureAllowedFor(S.lastStatus, String(message.exchangeId || ""))
      ) {
        updateVoiceCaption(message.text, message.type === "final");
        return;
      }
      if (message.type === "error") {
        debugLog("direct ASR error: " + String(message.message || "unknown"));
        if (S.directAsrCapturing) {
          toast("实时语音识别异常：" + String(message.message || "未知错误"), false);
        }
      }
    };
    socket.onerror = () => fail(new Error("ASR 直连失败"));
    socket.onclose = () => {
      fail(new Error("ASR 直连已断开"));
      if (S.directAsrStartReject) {
        S.directAsrStartReject(new Error("ASR 直连已断开"));
        S.directAsrStartResolve = null;
        S.directAsrStartReject = null;
        S.directAsrStartExchangeId = "";
      }
      if (S.directAsrWs === socket) S.directAsrWs = null;
      if (S.directAsrStream) {
        S.directAsrStream.getTracks().forEach((track) => track.stop());
      }
      if (S.directAsrAudioCtx) S.directAsrAudioCtx.close().catch(() => {});
      S.directAsrStream = null;
      S.directAsrAudioCtx = null;
      S.directAsrSource = null;
      S.directAsrNode = null;
      S.directAsrSink = null;
      S.directAsrPrepared = false;
      if (S.directAsrCapturing) {
        S.directAsrCapturing = false;
        S.activeAsrTransport = null;
        S.micOn = false;
        stopWave();
        updateMicUI();
        toast("实时语音连接已断开，请重新打开麦克风", false);
      }
    };
  });
  S.directAsrConnectPromise = promise;
  promise.then(
    () => {
      if (S.directAsrConnectPromise === promise) S.directAsrConnectPromise = null;
    },
    () => {
      if (S.directAsrConnectPromise === promise) S.directAsrConnectPromise = null;
    }
  );
  return promise;
}

async function prepareDirectAsrPipeline() {
  if (
    S.directAsrPrepared &&
    S.directAsrStream?.getTracks().some((track) => track.readyState === "live") &&
    S.directAsrNode
  ) return true;
  const socket = await ensureDirectAsrSocket();
  let stream;
  let audioCtx;
  let source;
  let node;
  let sink;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    stream.getAudioTracks().forEach((track) => { track.enabled = false; });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx({ latencyHint: "interactive" });
    await audioCtx.audioWorklet.addModule("/asr-worklet.js");
    source = audioCtx.createMediaStreamSource(stream);
    node = new AudioWorkletNode(audioCtx, "pcm16k-capture");
    sink = audioCtx.createGain();
    sink.gain.value = 0;
    source.connect(node);
    node.connect(sink);
    sink.connect(audioCtx.destination);
    node.port.onmessage = (event) => {
      const exchangeId = S.activeCaptureExchangeId;
      if (
        S.directAsrCapturing &&
        captureAllowedFor(S.lastStatus, exchangeId) &&
        S.directAsrWs === socket &&
        socket.readyState === WebSocket.OPEN
      ) socket.send(event.data);
    };
    S.directAsrStream = stream;
    S.directAsrAudioCtx = audioCtx;
    S.directAsrSource = source;
    S.directAsrNode = node;
    S.directAsrSink = sink;
    S.directAsrPrepared = true;
    debugLog(`direct ASR pipeline prepared (${audioCtx.sampleRate}Hz → 16000Hz)`);
    return true;
  } catch (error) {
    try { if (node) node.disconnect(); } catch (e) { /* ignore */ }
    try { if (source) source.disconnect(); } catch (e) { /* ignore */ }
    try { if (sink) sink.disconnect(); } catch (e) { /* ignore */ }
    try { if (audioCtx) await audioCtx.close(); } catch (e) { /* ignore */ }
    if (stream) stream.getTracks().forEach((track) => track.stop());
    S.directAsrPrepared = false;
    throw error;
  }
}

async function startDirectAsrCapture(exchangeId) {
  if (S.directAsrCapturing && S.activeCaptureExchangeId === exchangeId) return true;
  if (!captureAllowedFor(S.lastStatus, exchangeId)) return false;
  await prepareDirectAsrPipeline();
  const socket = S.directAsrWs;
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  S.activeCaptureExchangeId = exchangeId;
  S.directAsrCapturing = true;
  S.directAsrStream.getAudioTracks().forEach((track) => { track.enabled = true; });
  if (S.directAsrAudioCtx.state === "suspended") await S.directAsrAudioCtx.resume();
  const started = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      if (S.directAsrStartExchangeId !== exchangeId) return;
      S.directAsrStartResolve = null;
      S.directAsrStartReject = null;
      S.directAsrStartExchangeId = "";
      reject(new Error("ASR 开放收音确认超时"));
    }, 1200);
    S.directAsrStartExchangeId = exchangeId;
    S.directAsrStartResolve = (message) => { clearTimeout(timer); resolve(message); };
    S.directAsrStartReject = (error) => { clearTimeout(timer); reject(error); };
  });
  socket.send(JSON.stringify({ type: "start", sampleRate: 16000, exchangeId }));
  try {
    await started;
  } catch (error) {
    S.directAsrCapturing = false;
    S.directAsrStream.getAudioTracks().forEach((track) => { track.enabled = false; });
    throw error;
  }
  setupMeter(S.directAsrStream);
  debugLog(`direct ASR capture started for ${exchangeId}`);
  return true;
}

async function stopDirectAsrCapture({ closeSocket = false, flushFinal = false } = {}) {
  const wasCapturing = S.directAsrCapturing;
  S.directAsrCapturing = false;
  if (S.directAsrStream) {
    S.directAsrStream.getAudioTracks().forEach((track) => { track.enabled = false; });
  }
  if (
    wasCapturing &&
    S.directAsrWs &&
    S.directAsrWs.readyState === WebSocket.OPEN
  ) {
    S.directAsrWs.send(JSON.stringify({ type: "stop", flushFinal }));
  }
  if (closeSocket) {
    if (S.directAsrNode) {
      S.directAsrNode.port.onmessage = null;
      try { S.directAsrNode.disconnect(); } catch (e) { /* ignore */ }
    }
    try { S.directAsrSource?.disconnect(); } catch (e) { /* ignore */ }
    try { S.directAsrSink?.disconnect(); } catch (e) { /* ignore */ }
    const ctx = S.directAsrAudioCtx;
    const stream = S.directAsrStream;
    S.directAsrNode = null;
    S.directAsrSource = null;
    S.directAsrSink = null;
    S.directAsrAudioCtx = null;
    S.directAsrStream = null;
    S.directAsrPrepared = false;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    if (ctx) {
      try { await ctx.close(); } catch (e) { /* already closed */ }
    }
  }
  if (closeSocket && S.directAsrWs) {
    const socket = S.directAsrWs;
    S.directAsrWs = null;
    try { socket.close(1000, "page session closed"); } catch (e) { /* ignore */ }
  }
}

function scheduleIdleRelease() {
  clearTimeout(S.idleTimer);
  S.idleTimer = setTimeout(async () => {
    if (S.interviewStarted || document.body.dataset.view !== "prep") return;
    await teardownSession();
    setReadiness("released", "面试间已释放，开始时将重新连接");
    setAvatarHint("面试间已释放");
  }, IDLE_RELEASE_MS);
}

async function teardownSession() {
  clearTimeout(S.idleTimer);
  stopPrepSpeech();
  await stopDirectAsrCapture({ closeSocket: true });
  S.activeAsrTransport = null;
  if (S.client) {
    try { await S.client.disconnect(); } catch (e) { /* gone */ }
    S.client = null;
  }
  const closingSessionId = S.sessionId;
  S.sessionReady = false;
  S.sessionId = "";
  S.directAsrAvailable = false;
  S.prepPromptPlayed = false;
  setAvatarHint("面试官连接中…");
  const stopUrl = closingSessionId
    ? `/api/stop-session?sessionId=${encodeURIComponent(closingSessionId)}&release=1`
    : "/api/stop-session?release=1";
  try { await postJSON(stopUrl); } catch (e) { /* offline */ }
}

function buildClient(data) {
  const container = $("avatar-stage");
  container.innerHTML = "";
  const client = LivekitSDK.createClient({
    connectConfig: { type: "direct", config: { sfuUrl: data.sfuUrl, userToken: data.userToken } },
    video: { containerElement: container, fitMode: "contain" },
    audio: {
      output: { enabled: true, volume: 1.0, muted: true },
      // Keep the platform's expected 24 kHz capture — the backend resampler normalizes
      // any uplink rate to the ASR's 16 kHz, so we don't force 16 kHz here (it broke media setup).
      input: { noiseSuppression: true, voiceIsolation: true, sampleRate: 24000, constraints: { echoCancellation: true, autoGainControl: true } },
    },
  });
  client.events.on("sdk:connected", () => {
    $("conn-dot").dataset.state = "on";
    setStageTag((getAvatarBySlug(S.selectedAvatar) || {}).name || "数字人面试官");
    setAvatarHint("", true);
    $("reconnect-banner").classList.remove("show");
  });
  client.events.on("sdk:disconnected", async () => {
    $("conn-dot").dataset.state = "off";
    document.body.removeAttribute("data-avatar");
    S.sessionReady = false;
    if (S.interviewStarted && !S.interviewOver && !S.endingInterview) {
      $("reconnect-banner").classList.add("show");
      try {
        await client.reconnect();
        S.sessionReady = true;
        // 重连成功后清空 chat-log，让服务端的新 transcript
        // （已过滤掉 thinking_check / answer_acknowledgement /
        // question_skip_transition）从源头重新渲染。
        renderedTurns.clear();
        $("chat-log").innerHTML = "";
        const status = await fetchStatus();
        renderHeader(status);
        renderTranscript(status.transcript || []);
      } catch (e) { /* banner stays */ }
    }
  });
  client.events.on("sdk:error", (info) => debugLog("sdk:error " + JSON.stringify(info)));
  client.events.on("conversation:asr:chunk", (chunk) => {
    // Direct captions arrive earlier on our own WebSocket. Ignore the delayed
    // platform copy so it cannot overwrite the draft with an older partial.
    if (
      S.activeAsrTransport !== "direct" &&
      captureAllowedFor(S.lastStatus, S.activeCaptureExchangeId) &&
      chunk &&
      typeof chunk.text === "string"
    ) upsertDraftBubble(chunk.text);
  });
  return client;
}

async function connectWithTimeout(client, timeoutMs) {
  let timer;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("连接超时（15s）")), timeoutMs); });
  try { await Promise.race([client.connect(), timeout]); }
  finally { clearTimeout(timer); }
}

// De-duped: preheat + "开始面试" share one in-flight session start.
function startSession(avatarSlug) {
  if (!_startInflight) {
    _startInflight = _doStartSession(avatarSlug).finally(() => { _startInflight = null; });
  }
  return _startInflight;
}

async function _doStartSession(avatarSlug) {
  const { ok, data } = await postJSON("/api/start-session", {
    avatar: avatarSlug || null,
    enterprise: ENTERPRISE_MODE,
  });
  if (!ok || !data.success) throw new Error(data.error || "start-session failed");
  S.asrAvailable = Boolean(data.asrAvailable);
  S.directAsrAvailable = Boolean(data.directAsrAvailable);
  S.sessionId = String(data.sessionId || "");
  S.closedSessionId = "";
  S.client = buildClient(data);
  const avatarConnect = connectWithTimeout(S.client, 15000);
  // Establish the caption/audio WebSocket during room preheat so opening the mic
  // later does not pay an extra network handshake.
  if (S.directAsrAvailable) {
    ensureDirectAsrSocket().catch((error) => {
      debugLog("direct ASR preconnect failed: " + error);
    });
  }
  await avatarConnect;
  S.sessionReady = true;
}

async function preheat(avatarSlug) {
  if (S.preheat === "connecting" || S.sessionReady) return;
  const slug = avatarSlug != null ? avatarSlug : S.selectedAvatar;
  setReadiness("connecting", "正在准备面试间…");
  $("conn-dot").dataset.state = "off";
  setStageTag((getAvatarBySlug(slug) || {}).name || "数字人面试官");
  setAvatarHint("正在连接面试官…");
  try {
    await startSession(slug);
    setReadiness("ready", "面试间已就绪，可以开始");
    clearPrepError();
    scheduleIdleRelease();
    playPrepPrompt();
  } catch (err) {
    const info = mapPlatformError(err && err.message);
    setReadiness("failed", info.title + "（点此重试）");
    showPrepError(err);
    debugLog("preheat failed: " + info.detail);
  }
}

async function playPrepPrompt() {
  if (S.prepPromptPlayed || S.interviewStarted || document.body.dataset.view !== "prep") return;
  S.prepPromptPlayed = true;
  await waitForAvatarVideoReady(1400);
  await new Promise((r) => setTimeout(r, 120));
  if (S.client && S.client.isMuted) {
    try { S.client.unmute(); } catch (e) { /* ignore */ }
  }
  try { await postJSON("/api/interview/prep-say"); } catch (e) { /* ignore */ }
}

function waitForAvatarVideoReady(timeoutMs) {
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const container = $("avatar-stage");
      const video = container ? container.querySelector("video") : null;
      const ready = Boolean(video && video.readyState >= 2 && video.videoWidth > 0);
      if (ready || Date.now() - started >= (timeoutMs || 0)) return resolve();
      requestAnimationFrame(tick);
    };
    tick();
  });
}

/* ---------------- avatar roster / picker ---------------- */

// Fetch the roster and decide whether the candidate picks an interviewer. The
// prep page preheats the selected interviewer immediately so the real avatar and
// prep voice are already ready before the candidate clicks "开始面试".
async function loadRoster() {
  if (ENTERPRISE_MODE) {
    const token = new URLSearchParams(location.search).get("token");
    if (token) {
      const redeemed = await postJSON("/api/enterprise/redeem", { token });
      history.replaceState({}, "", "/enterprise");
      if (!redeemed.ok || !redeemed.data.success) {
        showPrepError(new Error(redeemed.data.error || "企业邀请无效"));
        return;
      }
    }
    const res = await fetch("/api/enterprise/context");
    const context = await res.json();
    if (!res.ok || !context.success) {
      showPrepError(new Error(context.error || "企业邀请无效"));
      return;
    }
    if (context.record.status === "completed") {
      showEnterpriseComplete();
      return;
    }
    if (!context.record.target_role) {
      showPrepError(new Error("该企业邀请尚未绑定应聘岗位，请联系招聘方重新生成邀请"));
      return;
    }
    if (!context.record.candidate_ready) {
      showPrepError(new Error("该邀请未绑定完整的候选人简历，请联系招聘方重新创建面试任务"));
      return;
    }
    $("enterprise-identity").hidden = false;
    $("candidate-job-fields").hidden = true;
    $("candidate-resume-fields").hidden = true;
    $("avatar-picker").hidden = true;
    $("prep-description").textContent = "本次岗位、JD、面试官与题库均已由招聘方配置。请确认姓名后直接开始面试。";
    $("prep-candidate-name").value = context.record.candidate_name || "";
    $("prep-candidate-contact").value = context.record.candidate_contact || "";
    $("prep-role").value = context.record.target_role || "";
    $("prep-jd").value = "";
    $("enterprise-position-name").textContent = context.record.target_role;
    $("check-role").querySelector("span:last-child").textContent =
      `应聘岗位：${context.record.target_role}`;
    $("check-jd").hidden = true;
    $("check-resume").hidden = true;
    $("start-hint").textContent = "招聘信息已确认，可以开始面试";
    const avatar = context.record.avatar;
    S.roster = {selection_mode:"locked", locked_avatar:avatar.slug, avatars:[avatar]};
    await selectAvatar(avatar.slug, {initial:true});
    updateStartGate();
    return;
  }
  let roster;
  try { roster = await (await fetch("/api/roster")).json(); }
  catch (err) { roster = { selection_mode: "candidate_choice", avatars: [] }; }
  S.roster = roster;
  const avatars = roster.avatars || [];
  // "试面"/deep link: ?avatar=<slug> forces that interviewer, no picker.
  const forced = new URLSearchParams(location.search).get("avatar");
  if (forced && avatars.some((a) => a.slug === forced)) {
    await selectAvatar(forced, { initial: true });
    return;
  }
  const pickerNeeded = roster.selection_mode === "candidate_choice" && avatars.length > 1;
  if (pickerNeeded) {
    renderAvatarPicker(avatars);
    $("avatar-picker").hidden = false;
    await selectAvatar(roster.locked_avatar || avatars[0].slug, { initial: true });
  } else {
    const slug = roster.selection_mode === "locked"
      ? roster.locked_avatar
      : (avatars[0] && avatars[0].slug) || null;
    if (slug) await selectAvatar(slug, { initial: true });
  }
}

function renderAvatarPicker(avatars) {
  const wrap = $("picker-cards");
  wrap.innerHTML = "";
  for (const a of avatars) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "avatar-card";
    card.dataset.slug = a.slug;
    card.setAttribute("aria-pressed", "false");
    card.innerHTML = '<div class="ac-name"></div><div class="ac-dir"></div>';
    card.querySelector(".ac-name").textContent = a.name || a.slug;
    card.querySelector(".ac-dir").textContent = a.direction || "";
    card.onclick = () => selectAvatar(a.slug);
    wrap.appendChild(card);
  }
}

function highlightSelectedAvatar(slug) {
  document.querySelectorAll("#picker-cards .avatar-card").forEach((card) => {
    card.setAttribute("aria-pressed", card.dataset.slug === slug ? "true" : "false");
  });
}

function applyAvatarDefaults(avatar) {
  if (ENTERPRISE_MODE || !avatar) return;
  const role = String(avatar.defaultRole || "").trim();
  const jd = String(avatar.defaultJd || "").trim();
  const locked = Boolean(avatar.profileLocked);
  const previous = S.appliedAvatarDefaults || { role: "", jd: "" };
  const roleInput = $("prep-role");
  const jdInput = $("prep-jd");
  const replaceRole = locked || !roleInput.value.trim() || roleInput.value === previous.role;
  const replaceJd = locked || !jdInput.value.trim() || jdInput.value === previous.jd;
  let changed = false;
  if (replaceRole && roleInput.value !== role) {
    roleInput.value = role;
    changed = true;
  }
  if (replaceJd && jdInput.value !== jd) {
    jdInput.value = jd;
    changed = true;
  }
  roleInput.readOnly = locked;
  jdInput.readOnly = locked;
  roleInput.dataset.locked = locked ? "1" : "0";
  jdInput.dataset.locked = locked ? "1" : "0";
  const roleTip = $("prep-role-tip");
  const jdTip = $("prep-jd-tip");
  if (roleTip) roleTip.hidden = !locked;
  if (jdTip) {
    jdTip.textContent = locked
      ? "岗位 JD 已由该面试官预设，本场不可修改。"
      : "没有 JD 可以留空；系统不会自动生成或补全。";
  }
  S.appliedAvatarDefaults = {
    role: replaceRole ? role : "",
    jd: replaceJd ? jd : "",
  };
  if (changed) profileDirty = true;
  updateStartGate();
}

async function selectAvatar(slug, opts = {}) {
  if (S.selectedAvatar === slug && !opts.initial) return;
  const changed = !opts.initial && S.selectedAvatar && S.selectedAvatar !== slug;
  S.selectedAvatar = slug;
  highlightSelectedAvatar(slug);
  applyAvatarDefaults(getAvatarBySlug(slug));
  clearPrepError();
  if (changed) {
    try { await _startInflight; } catch (err) { /* ignore */ }
    await teardownSession();
    S.sessionReady = false;
    S.preheat = "idle";
  }
  preheat(slug);
}

/* ---------------- start interview ---------------- */

// Practice requires role + resume. Enterprise invitations already contain the
// recruiter-defined role/JD/interview config, so candidates only confirm name.
function updateStartGate() {
  const role = $("prep-role").value.trim();
  const jd = $("prep-jd").value.trim();
  const resume =
    $("prep-resume-file").files.length > 0 || $("prep-resume-text").value.trim();
  const candidateName = ENTERPRISE_MODE ? $("prep-candidate-name").value.trim() : "practice";
  const resumeReady = ENTERPRISE_MODE || Boolean(resume);
  const ready = Boolean(role && resumeReady && candidateName);
  $("start-btn").disabled = !ready;
  const hint = $("start-hint");
  if (hint) {
    if (ready) {
      hint.style.display = "none";
    } else {
      const missing = [];
      if (!role) missing.push("岗位名称");
      if (!ENTERPRISE_MODE && !resume) missing.push("简历");
      if (!candidateName) missing.push("姓名");
      hint.textContent = "还差：" + missing.join("、");
      hint.style.display = "";
    }
  }
  const setOk = (id, ok) => {
    const el = $(id);
    if (!el) return;
    el.dataset.ok = ok ? "1" : "0";
  };
  setOk("check-role", Boolean(role));
  setOk("check-jd", Boolean(jd) || Boolean(role));
  setOk("check-resume", resumeReady);
}

async function onStartInterview() {
  if (S.starting) return;
  if ($("start-btn").disabled) return;
  S.starting = true;
  const btn = $("start-btn");
  const origLabel = btn.textContent;
  btn.disabled = true;
  btn.classList.add("loading");
  const setBtn = (t) => { btn.textContent = t; };
  clearPrepError();
  try {
    stopPrepSpeech();
    setBtn(ENTERPRISE_MODE ? "正在加载企业面试配置…" : "正在分析岗位与简历…");
    await saveProfileIfDirty();
    if (!S.sessionReady) {
      setReadiness("connecting", "正在连接面试间…");
      setBtn("正在连接面试间…");
      await startSession(S.selectedAvatar);
      setReadiness("ready", "面试间已就绪");
    }
    if (S.directAsrAvailable) {
      try {
        setBtn("正在准备麦克风…");
        await prepareDirectAsrPipeline();
      } catch (error) {
        debugLog("direct ASR prewarm failed, will retry on first question: " + error);
      }
    }
    // Profile intake has finished its one-shot analysis; planning now uses only its brief.
    setReadiness("connecting", "正在生成面试题目…");
    setBtn("正在生成面试题目…");
    const { ok, data } = await postJSON("/api/interview/start");
    if (!ok || !data.success) throw new Error(data.error || "面试启动失败");
    enterInterview();
  } catch (err) {
    setReadiness("failed", "连接失败（点此重试）");
    showPrepError(err);
  } finally {
    S.starting = false;
    btn.classList.remove("loading");
    btn.textContent = origLabel;
    updateStartGate();  // re-enable for retry if the form is still valid
  }
}

function enterInterview() {
  clearTimeout(S.idleTimer);
  stopPrepSpeech();
  S.interviewStarted = true;
  S.interviewOver = false;
  S.endingInterview = false;
  S.reportDismissed = false;
  S.viewingReport = false;
  S.questionCount = 0;
  renderedTurns.clear();
  $("chat-log").innerHTML = "";
  setView("interview");
  if (S.client && S.client.isMuted) {
    try { S.client.unmute(); } catch (e) { /* ignore */ }
  }

  // Voice mode is preferred when ASR exists, but capture remains OFF until the
  // backend enters LISTENING after the interviewer has finished speaking.
  const hasAsr = S.asrAvailable || (S.useBrowserAsr && S.speechRecognition);
  S.voiceReplyEnabled = Boolean(hasAsr);
  S.micDesired = false;
  $("mic-btn").disabled = true;

  if (!hasAsr) {
    setComposerMode("text");
    $("answer-input").focus();
  }
  startPolling();
}

/* ---------------- status polling & chat rendering ---------------- */

function startPolling() { stopPolling(); S.pollTimer = setInterval(refreshStatus, 180); refreshStatus(); }
function stopPolling() { if (S.pollTimer) clearInterval(S.pollTimer); S.pollTimer = null; }

async function refreshStatus() {
  let status;
  const statusUrl = S.viewingReport && S.reportInterviewId
    ? `/api/interview/status?interviewId=${encodeURIComponent(S.reportInterviewId)}`
    : "/api/interview/status";
  try { status = await (await fetch(statusUrl)).json(); }
  catch (err) { $("state-pill").textContent = "连接后端失败，重试中…"; return; }
  if (status.latestReportStatus && !S.viewingReport) {
    S.backgroundReportStatus = status.latestReportStatus;
    updatePrepReportEntry(status.latestReportStatus);
  }
  if (
    status.interviewId
    && (
      REPORT_STATES.has(status.state)
      || status.finalReport
      || ["generating", "retrying", "error", "completed"].includes(
        status.reportGeneration?.state
      )
    )
  ) {
    S.reportInterviewId = String(status.interviewId);
    localStorage.setItem("lastReportInterviewId", S.reportInterviewId);
  }
  S.lastStatus = status;
  if (DEBUG) $("debug-status").textContent = JSON.stringify(status, null, 2);
  renderHeader(status);
  renderTranscript(status.transcript || []);
  renderTyping(status);
  gateComposer(status);
  syncMicWithInterviewState(status);
  if (status.finalReport || REPORT_STATES.has(status.state) || status.reportGeneration?.state === "generating" || status.reportGeneration?.state === "retrying" || status.reportGeneration?.state === "error") {
    S.backgroundReportStatus = status;
    updatePrepReportEntry(status);
    if (!S.reportDismissed && !TERMINAL_STATES.has(status.state)) enterReportGeneration(status);
  }
  if (S.interviewStarted && TERMINAL_STATES.has(status.state)) finishInterview(status);
}

function renderHeader(status) {
  $("progress-fill").style.width = (status.stageProgressPercent ?? status.progressPercent ?? 0) + "%";
  $("progress-label").textContent = status.stageLabel || "面试进行中";
  const pill = $("state-pill");
  pill.textContent = LISTENING_STATES.has(status.state)
    ? "正在聆听你的回答"
    : "面试官说话中";
  pill.dataset.kind = LISTENING_STATES.has(status.state) ? "listen" : TYPING_STATES.has(status.state) ? "think" : "info";
}

// 这些 type 在产品里都已被关闭，前端不再渲染任何具体文案
const HIDDEN_TURN_TYPES = new Set([
  "answer_acknowledgement",
  "thinking_check",
  "question_skip_transition",
]);

function turnElement(turn) {
  // 新规则：答后衔接 / 思考中提示 / 跳题话术均已关闭，收到这些 type 时直接跳过
  if (HIDDEN_TURN_TYPES.has(turn.type || "")) {
    return null;
  }
  const el = document.createElement("div");
  const type = turn.type || "";
  if (turn.role === "system") {
    el.className = "sys-note";
    el.textContent = SKIP_REASON_TEXT[(turn.metadata || {}).reason] || SKIP_REASON_TEXT[turn.text] || turn.text;
    return el;
  }
  const mine = turn.role === "candidate";
  el.className = "bubble " + (mine ? "me" : "them");
  if (type === "main_question") { S.questionCount += 1; el.dataset.badge = "第 " + S.questionCount + " 题"; }
  else if (type === "follow_up") { el.dataset.badge = "追问"; }
  const body = document.createElement("div");
  body.className = "bubble-text";
  body.textContent = turn.text;
  el.appendChild(body);
  return el;
}

function renderTranscript(turns, targetLog) {
  const log = targetLog || $("chat-log");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  for (const turn of turns) {
    if (!targetLog && renderedTurns.has(turn.turnId)) continue;
    if (["closing", "termination"].includes(turn.type)) {
      const sep = document.createElement("div");
      sep.className = "chat-sep";
      sep.textContent = "面试结束";
      log.appendChild(sep);
    }
    const el = turnElement(turn);
    if (el) log.appendChild(el);
    if (!targetLog && el) {
      renderedTurns.set(turn.turnId, el);
      if (turn.role === "candidate") {
        resetVoiceCaption();
        removePendingBubbles();
      }
    }
  }
  if (!targetLog && nearBottom) log.scrollTop = log.scrollHeight;
}

function renderTyping(status) {
  const typing = $("typing");
  const show = TYPING_STATES.has(status.state);
  typing.classList.toggle("show", show);
  if (show) {
    typing.querySelector(".typing-text").textContent = status.candidateMessage || "正在分析…";
    const log = $("chat-log");
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;
  }
}

// Live transcript of what the candidate is saying, streamed as ONE continuously
// growing message. ASR providers split long speech into several final utterances;
// those segments must accumulate in the same draft bubble until the backend emits
// the single authoritative candidate answer.
function appendCaptionSegment(baseText, nextText) {
  const base = String(baseText || "").trim();
  const next = String(nextText || "").trim();
  if (!base) return next;
  if (!next || base === next || base.endsWith(next)) return base;
  if (next.startsWith(base)) return next;

  // Avoid duplicated words when a provider repeats the tail of the previous
  // finalized utterance at the start of the next interim result.
  const limit = Math.min(32, base.length, next.length);
  let overlap = 0;
  for (let size = limit; size > 0; size--) {
    if (base.slice(-size) === next.slice(0, size)) {
      overlap = size;
      break;
    }
  }
  const suffix = next.slice(overlap);
  if (!suffix) return base;
  const separator = overlap || /[\s，。！？、,.!?;；:]$/.test(base) ? "" : " ";
  return base + separator + suffix;
}

function updateVoiceCaption(text, isFinal = false) {
  const segment = String(text || "").trim();
  if (!segment) return;
  if (isFinal) {
    S.voiceCaptionCommitted = appendCaptionSegment(
      S.voiceCaptionCommitted,
      segment,
    );
    S.voiceCaptionInterim = "";
  } else {
    S.voiceCaptionInterim = segment;
  }
  const displayText = appendCaptionSegment(
    S.voiceCaptionCommitted,
    S.voiceCaptionInterim,
  );
  upsertDraftBubble(displayText);
}

function upsertDraftBubble(text) {
  if (!text) return;
  const log = $("chat-log");
  let draft = $("draft-bubble");
  if (!draft) {
    draft = document.createElement("div");
    draft.id = "draft-bubble";
    draft.className = "bubble me draft";
    draft.innerHTML = '<div class="bubble-text"></div>';
    log.appendChild(draft);
  }
  draft.querySelector(".bubble-text").textContent = text;
  log.scrollTop = log.scrollHeight;
}
function removeDraftBubble() {
  const draft = $("draft-bubble");
  if (draft) draft.remove();
}
function resetVoiceCaption() {
  S.voiceCaptionCommitted = "";
  S.voiceCaptionInterim = "";
  removeDraftBubble();
}
function removePendingBubbles() { document.querySelectorAll(".bubble.pending").forEach((el) => el.remove()); }

/* ---------------- composer ---------------- */

// The composer is one bar: text mode shows the textarea, voice mode turns the
// whole row into a live audio waveform. Mic on = voice; mic off = text.
function setComposerMode(mode) {
  $("composer-bar").dataset.mode = mode;
}

function hasVoiceAsr() {
  return Boolean(S.asrAvailable || (S.useBrowserAsr && S.speechRecognition));
}

function requestMic(on) {
  S.micDesired = Boolean(on);
  S.micTransition = S.micTransition
    .catch(() => {})
    .then(async () => {
      if (S.micDesired !== S.micOn) await setMic(S.micDesired);
    });
  return S.micTransition;
}

function syncMicWithInterviewState(status) {
  const exchangeId = currentExchangeId(status);
  const shouldCapture = captureAllowedFor(status, exchangeId) && S.voiceReplyEnabled && hasVoiceAsr();
  if (S.micOn && S.activeCaptureExchangeId !== exchangeId) {
    S.micDesired = shouldCapture;
    S.micTransition = S.micTransition
      .catch(() => {})
      .then(async () => {
        if (S.micOn) await setMic(false);
        if (S.micDesired && captureAllowedFor(S.lastStatus, currentExchangeId(S.lastStatus))) {
          await setMic(true);
        }
      });
    return;
  }
  requestMic(shouldCapture);
}

async function setMic(on) {
  if (!S.client || !S.sessionReady) { S.micOn = false; updateMicUI(); return; }
  try {
    if (on && !S.micOn) {
      const exchangeId = currentExchangeId(S.lastStatus);
      if (!captureAllowedFor(S.lastStatus, exchangeId)) {
        S.micOn = false;
        updateMicUI();
        return;
      }
      resetVoiceCaption();
      if (S.asrAvailable) {
        let directStarted = false;
        if (S.directAsrAvailable) {
          try {
            directStarted = await startDirectAsrCapture(exchangeId);
          } catch (error) {
            debugLog("direct ASR unavailable, falling back to platform audio: " + error);
          }
        }
        if (directStarted) {
          S.activeAsrTransport = "direct";
        } else {
          // Compatibility fallback for browsers without AudioWorklet or when the
          // direct WebSocket cannot be established.
          const result = await postJSON("/api/interview/audio-input", {
            enabled: true,
            exchangeId,
          });
          if (!result.ok || !result.data.success) {
            throw new Error(result.data.error || "当前题目尚未开放收音");
          }
          await S.client.startAudioCapture();
          setupMeterFromClient();
          S.activeAsrTransport = "platform";
        }
        S.micOn = true;
        startWave();
      } else if (S.useBrowserAsr && S.speechRecognition) {
        // Fallback to browser ASR
        setupMeterFromClient();
        startBrowserAsr(exchangeId);
        S.activeAsrTransport = "browser";
        S.micOn = true;
        startWave();
      }
      S.activeCaptureExchangeId = exchangeId;
    } else if (!on && S.micOn) {
      if (S.activeAsrTransport === "direct") {
        await stopDirectAsrCapture();
        S.micOn = false;
        stopWave();
      } else if (S.activeAsrTransport === "platform") {
        await postJSON("/api/interview/audio-input", { enabled: false });
        await S.client.stopAudioCapture();
        S.micOn = false;
        stopWave();
      } else if (S.activeAsrTransport === "browser") {
        // Stop browser ASR
        stopBrowserAsr();
        S.micOn = false;
        stopWave();
      }
      S.activeAsrTransport = null;
      S.activeCaptureExchangeId = "";
      // Keep the complete draft visible while the backend finalizes this answer.
      // renderTranscript() replaces it with the authoritative candidate bubble.
    }
  } catch (err) {
    await stopDirectAsrCapture();
    S.activeAsrTransport = null;
    S.activeCaptureExchangeId = "";
    S.micOn = false;
    stopWave();
    stopBrowserAsr();
    resetVoiceCaption();
    toast("麦克风不可用：" + (err && err.message ? err.message : err), false);
  }
  updateMicUI();
}

// Meter the exact mic track the SDK publishes, so the waveform proves capture and
// there is no second getUserMedia to contend with. Falls back to an independent
// stream only if the SDK's track can't be reached.
function setupMeterFromClient() {
  try {
    const track = S.client?._liveKitService?.getMicrophoneTrack?.()?.mediaStreamTrack;
    if (track) { setupMeter(new MediaStream([track])); return; }
  } catch (err) { debugLog("client mic track unavailable: " + err); }
  navigator.mediaDevices
    .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false } })
    .then((stream) => { S.meterStream = stream; setupMeter(stream); })
    .catch((err) => debugLog("meter fallback getUserMedia failed: " + err));
}

// A real level meter: an AnalyserNode over the mic stream. getMicrophoneAudioLevel()
// from the SDK returns a constant 0.5 whenever LiveKit hasn't computed a volume, which
// made the waveform "breathe" even in silence — this reads the actual signal instead.
function setupMeter(stream) {
  teardownMeter();
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    S.audioCtx = new Ctx();
    const src = S.audioCtx.createMediaStreamSource(stream);
    S.analyser = S.audioCtx.createAnalyser();
    S.analyser.fftSize = 1024;
    S.analyser.smoothingTimeConstant = 0.55;
    S.timeData = new Uint8Array(S.analyser.fftSize);
    src.connect(S.analyser);
    // Chrome creates the context "suspended" when setMic runs after the click's
    // gesture window closed; without resuming, the analyser only ever reads silence.
    resumeMeter();
  } catch (err) { debugLog("meter setup failed: " + err); }
}

function resumeMeter() {
  if (S.audioCtx && S.audioCtx.state === "suspended") S.audioCtx.resume().catch(() => {});
}

function teardownMeter() {
  if (S.audioCtx) { try { S.audioCtx.close(); } catch (e) { /* already closed */ } S.audioCtx = null; }
  S.analyser = null; S.timeData = null;
  if (S.meterStream) { S.meterStream.getTracks().forEach((t) => t.stop()); S.meterStream = null; }
  S.micLevel = 0;
}

// Root-mean-square of the time-domain waveform → 0..1 loudness of what the mic hears.
function readMicLevel() {
  if (!S.analyser || !S.timeData) return 0;
  S.analyser.getByteTimeDomainData(S.timeData);
  let sum = 0;
  for (let i = 0; i < S.timeData.length; i++) {
    const v = (S.timeData[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / S.timeData.length);
}

function updateMicUI() {
  $("mic-btn").dataset.on = S.micOn ? "1" : "0";
  setComposerMode(S.micOn ? "voice" : "text");
  $("mic-btn").title = S.micOn ? "点击停止语音作答" : "点击开始语音作答";
  if (!S.micOn) $("answer-input").focus();
}

// Drive the waveform bars from the real mic level. When the mic hears nothing the
// bars sit flat and gray; a per-bar sine wobble only kicks in once there's voice.
function startWave() {
  if (S.levelRaf) cancelAnimationFrame(S.levelRaf);   // restart the loop, keep the meter
  const bars = Array.from(document.querySelectorAll("#wave-bars i"));
  const wrap = $("wave-bars");
  S.captureStartedAt = performance.now();
  S.captureVoicedMs = 0;
  let previousAt = S.captureStartedAt;
  const loop = () => {
    if (!S.micOn) return;
    if (S.audioCtx && S.audioCtx.state === "suspended") resumeMeter();
    const level = readMicLevel();
    const now = performance.now();
    S.micLevel = level;
    const voiced = level > SPEECH_RMS;
    if (voiced) S.captureVoicedMs += Math.max(0, now - previousAt);
    previousAt = now;
    const loud = Math.min(1, level * 7.5);
    wrap.classList.toggle("silent", !voiced);
    const t = now / 150;
    bars.forEach((bar, i) => {
      if (!voiced) { bar.style.height = "10%"; return; }
      const wobble = 0.5 + 0.5 * Math.abs(Math.sin(t + i * 0.5));
      bar.style.height = (10 + loud * 88 * wobble).toFixed(0) + "%";
    });
    S.levelRaf = requestAnimationFrame(loop);
  };
  S.levelRaf = requestAnimationFrame(loop);
}
function stopWave() {
  if (S.levelRaf) cancelAnimationFrame(S.levelRaf);
  S.levelRaf = null;
  teardownMeter();
  const wrap = $("wave-bars");
  if (wrap) wrap.classList.remove("silent");
  document.querySelectorAll("#wave-bars i").forEach((b) => { b.style.height = "10%"; });
}

function gateComposer(status) {
  const listening = captureAllowedFor(status, currentExchangeId(status));
  const input = $("answer-input");
  input.disabled = !listening;
  $("send-btn").disabled = !listening || !input.value.trim();
  input.placeholder = listening
    ? "正在聆听你的回答"
    : "面试官说话中";
  $("mic-btn").disabled = !listening || !hasVoiceAsr();
  $("wave-hint").textContent = listening
    ? "正在聆听你的回答"
    : "面试官说话中";
}

async function sendTextAnswer() {
  const input = $("answer-input");
  const text = input.value.trim();
  if (!text || !S.client) return;
  const log = $("chat-log");
  const el = document.createElement("div");
  el.className = "bubble me pending";
  el.innerHTML = '<div class="bubble-text"></div>';
  el.querySelector(".bubble-text").textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  input.value = "";
  gateComposer(S.lastStatus || { state: "listening" });
  try {
    await S.client.sendTextQuestion(text);
  } catch (err) {
    el.remove(); input.value = text;
    toast("发送失败：" + (err && err.message ? err.message : err), false);
  }
}

/* ---------------- finish & report ---------------- */

function onEndInterview() { $("confirm-modal").classList.add("show"); }

function showReportLoading(progress = null) {
  const loading = $("report-loading");
  const report = $("report");
  if (loading) loading.hidden = false;
  if (report) report.hidden = true;
  const finished = $("view-finished");
  if (finished) finished.dataset.reportView = "loading";
  updateReportProgress(progress || {
    state: "generating", stage: "preprocessing", message: "正在整理面试记录",
    percent: 3, completedSteps: 0, totalSteps: 0,
  });
}

function hideReportLoading() {
  const loading = $("report-loading");
  const report = $("report");
  if (loading) loading.hidden = true;
  if (report) report.hidden = false;
  const finished = $("view-finished");
  if (finished) finished.dataset.reportView = "report";
}

function enterReportGeneration(status) {
  if (document.body.dataset.view !== "finished") setView("finished");
  S.voiceReplyEnabled = false;
  requestMic(false);
  showReportLoading(status?.reportGeneration || null);
}

function getReportEntryState(status) {
  if (!status) return "";
  if (status.finalReport) return "completed";
  const generation = status.reportGeneration || {};
  if (generation.state === "error" || status.state === "report_error") return "error";
  if (["generating", "retrying"].includes(generation.state) || REPORT_STATES.has(status.state)) return "generating";
  return "";
}

function updatePrepReportEntry(status) {
  const entry = $("prep-report-entry");
  if (!entry || ENTERPRISE_MODE) return;
  const state = getReportEntryState(status);
  if (!state) {
    entry.hidden = true;
    return;
  }
  const labels = {
    generating: "报告生成中",
    completed: "查看面试报告",
    error: "报告待继续",
  };
  entry.hidden = false;
  entry.dataset.state = state;
  $("prep-report-entry-text").textContent = labels[state];
  entry.setAttribute("aria-label", labels[state]);
}

async function loadLatestReportStatus() {
  const interviewId = S.reportInterviewId;
  if (!interviewId || ENTERPRISE_MODE) return;
  try {
    const response = await fetch(
      `/api/interview/status?interviewId=${encodeURIComponent(interviewId)}`
    );
    if (!response.ok) return;
    const status = await response.json();
    if (!getReportEntryState(status)) return;
    S.backgroundReportStatus = status;
    if (status.finalReport) S.lastCompletedReportStatus = status;
    updatePrepReportEntry(status);
  } catch (error) {
    debugLog("load latest report status failed: " + error);
  }
}

function dismissReportToPrep() {
  S.reportDismissed = true;
  S.viewingReport = false;
  S.interviewStarted = false;
  S.endingInterview = false;
  profileDirty = true;
  stopPolling();
  setView("prep");
  renderAvatarPoster(S.selectedAvatar);
  const state = getReportEntryState(S.backgroundReportStatus || S.lastStatus);
  if (state === "generating") setReadiness("connecting", "AI 报告正在后台生成");
  else if (state === "error") setReadiness("failed", "AI 报告尚未生成完成");
  else setReadiness("released", "上一场面试已完成");
  updatePrepReportEntry(S.backgroundReportStatus || S.lastCompletedReportStatus || S.lastStatus);
  updateStartGate();
}

function openReportFromPrep() {
  const status = S.lastCompletedReportStatus || S.backgroundReportStatus || S.lastStatus;
  if (!status) return;
  S.reportDismissed = false;
  S.viewingReport = true;
  setView("finished");
  if (status.finalReport) {
    renderReport(status);
    hideReportLoading();
    return;
  }
  showReportLoading(status.reportGeneration || null);
  startPolling();
}

function updateReportProgress(progress = {}) {
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
  const stage = progress.stage || "preprocessing";
  const state = progress.state || "generating";
  if (["generating", "retrying", "error"].includes(state) && S.interviewStarted) {
    S.backgroundReportStatus = {
      ...(S.backgroundReportStatus || S.lastStatus || {}),
      state: state === "error" ? "report_error" : "report_generating",
      reportGeneration: { ...progress, state, stage },
    };
    updatePrepReportEntry(S.backgroundReportStatus);
    updateStartGate();
  }
  const titleByStage = {
    preprocessing: "正在整理本场问答",
    preparing: "正在整理本场问答",
    chunk_analysis: "正在逐组分析回答",
    overview: "正在生成 AI 综合结论",
    validating: "正在核验报告完整性",
    completed: "AI 报告已生成",
  };
  $("report-loading-title").textContent = state === "error"
    ? "AI 报告生成未完成"
    : (titleByStage[stage] || "正在生成 AI 面试报告");
  $("report-loading-message").textContent = state === "retrying"
    ? "AI 服务响应较慢，正在继续生成，请稍候。"
    : (progress.message || "AI 正在分析本场面试。");
  $("report-progress-fill").style.width = `${percent}%`;
  $("report-progress-percent").textContent = `${Math.round(percent)}%`;
  const completed = Number(progress.completedSteps ?? progress.completed_steps ?? 0);
  const total = Number(progress.totalSteps ?? progress.total_steps ?? 0);
  $("report-progress-detail").textContent = state === "retrying"
    ? "AI 正在继续生成"
    : total > 0
      ? `已完成 ${completed}/${total} 个生成步骤`
      : "准备中";
  const order = ["preprocessing", "chunk_analysis", "overview", "validating"];
  const activeIndex = Math.max(0, order.indexOf(stage));
  document.querySelectorAll("[data-report-stage]").forEach((item, index) => {
    item.classList.toggle("done", state === "completed" || index < activeIndex);
    item.classList.toggle("active", state !== "error" && state !== "completed" && index === activeIndex);
  });
  const error = $("report-generation-error");
  const retry = $("report-retry-btn");
  if (state === "error") {
    const finished = $("view-finished");
    if (finished) finished.dataset.reportView = "error";
    error.hidden = false;
    error.textContent = "报告尚未全部由 AI 生成完成。你可以继续生成，或先返回准备页稍后查看。";
    retry.hidden = false;
  } else {
    error.hidden = true;
    error.textContent = "";
    retry.hidden = true;
  }
}

async function retryReportGeneration() {
  const btn = $("report-retry-btn");
  btn.disabled = true;
  btn.textContent = "正在继续…";
  S.reportDismissed = false;
  updateReportProgress({
    state: "retrying", stage: "chunk_analysis", message: "正在继续处理未完成部分",
    percent: Math.max(10, Number(S.lastStatus?.reportGeneration?.percent) || 10),
  });
  startPolling();
  try {
    const result = await postJSON("/api/interview/report/retry", {
      interviewId: S.reportInterviewId,
    });
    if (!result.ok || !result.data.success) {
      updateReportProgress(result.data.status?.reportGeneration || {
        state: "error", stage: "overview", percent: 0,
        error: result.data.error || "AI 报告重新生成失败",
      });
      return;
    }
    const status = result.data.status || await (await fetch(
      `/api/interview/status?interviewId=${encodeURIComponent(S.reportInterviewId)}`
    )).json();
    S.lastStatus = status;
    S.backgroundReportStatus = status;
    updateReportProgress(status.reportGeneration || {
      state: "generating", stage: "chunk_analysis", percent: 10,
      message: "正在继续处理未完成部分",
    });
  } catch (err) {
    updateReportProgress({
      state: "error", stage: "overview", percent: 0,
      error: "重新生成失败：" + (err?.message || err),
    });
  } finally {
    btn.disabled = false;
    btn.textContent = "继续生成";
  }
}

async function confirmEnd() {
  const btn = $("confirm-end");
  btn.disabled = true; btn.textContent = "正在生成报告…";
  // 立刻进入独立整页 loading；后端返回 202 后继续后台生成。
  stopWave(); clearTimeout(S.idleTimer);
  S.voiceReplyEnabled = false;
  requestMic(false);
  stopBrowserAsr();
  S.interviewOver = false;
  S.endingInterview = true;
  S.viewingReport = true;
  S.reportDismissed = false;
  showReportLoading();
  setView("finished");
  $("confirm-modal").classList.remove("show");
  if (S.client) { S.client.disconnect().catch(() => {}); S.client = null; }
  S.sessionReady = false;
  startPolling();

  try {
    const endingSessionId = S.sessionId;
    const result = await postJSON("/api/interview/stop", {
      sessionId: endingSessionId,
    });
    if (!result.ok || !result.data.success) {
      if (result.data.retryable) {
        updateReportProgress(result.data.status?.reportGeneration || {
          state: "error", stage: "overview", percent: 0,
          error: result.data.error || "AI 综合结论生成失败",
        });
        return;
      }
      throw new Error(result.data.error || "报告生成失败");
    }
    const status = result.data.status || await (await fetch("/api/interview/status")).json();
    S.reportInterviewId = String(result.data.interviewId || status.interviewId || "");
    if (S.reportInterviewId) {
      localStorage.setItem("lastReportInterviewId", S.reportInterviewId);
    }
    S.closedSessionId = endingSessionId;
    S.sessionId = "";
    if (ENTERPRISE_MODE) {
      showEnterpriseComplete();
      return;
    }
    S.lastStatus = status;
    S.backgroundReportStatus = status;
    updatePrepReportEntry(status);
    showReportLoading(status.reportGeneration || null);
    updateStartGate();
  } catch (err) {
    updateReportProgress({
      state: "error", stage: "overview", percent: 0,
      error: "报告生成失败：" + (err && err.message || err),
    });
  } finally {
    $("confirm-modal").classList.remove("show");
    btn.disabled = false; btn.textContent = "结束并生成报告";
  }
}

function finishInterview(status) {
  if (S.interviewOver) return;
  const generationSource = status?.finalReport?.generationSource || "";
  if (status?.finalReport && !["llm", "llm_partial"].includes(generationSource)) {
    const invalidStatus = {
      ...(status || {}),
      reportGeneration: {
        state: "error", stage: "overview", percent: 0,
        error: "本次综合结论不是由 AI 生成，请重新生成报告。",
      },
    };
    S.backgroundReportStatus = invalidStatus;
    updatePrepReportEntry(invalidStatus);
    if (!S.reportDismissed) enterReportGeneration(invalidStatus);
    return;
  }
  S.interviewOver = true;
  S.endingInterview = false;
  stopPolling(); stopWave(); clearTimeout(S.idleTimer);
  S.voiceReplyEnabled = false;
  requestMic(false);
  stopBrowserAsr();
  if (S.client) { S.client.disconnect().catch(() => {}); S.client = null; }
  S.sessionReady = false;
  const sessionToClose = S.sessionId || S.closedSessionId;
  const stopUrl = sessionToClose
    ? `/api/stop-session?sessionId=${encodeURIComponent(sessionToClose)}`
    : "/api/stop-session";
  postJSON(stopUrl).catch(() => {});
  if (ENTERPRISE_MODE || status?.enterprise) {
    setView("finished");
    showEnterpriseComplete();
    return;
  }

  // 情况 A：报告已经在 status 里 → 直接渲染
  if (status && status.finalReport) {
    S.backgroundReportStatus = status;
    S.lastCompletedReportStatus = status;
    profileDirty = true;
    updatePrepReportEntry(status);
    renderReport(status);
    hideReportLoading();
    if (S.reportDismissed) {
      setView("prep");
      renderAvatarPoster(S.selectedAvatar);
      setReadiness("released", "上一场面试已完成");
      updateStartGate();
    } else {
      setView("finished");
    }
    return;
  }

  // 兼容旧服务：终态暂时没有报告时继续等待后端明确给出完成或错误。
  showReportLoading();
  if (!S.reportDismissed) setView("finished");
  startPolling();
}

const REPORT_DIMENSION_META = {
  communication_clarity: { label: "表达能力", radar: "表达能力", desc: "表达是否清楚、有条理，是否便于理解" },
  problem_solving: { label: "逻辑能力", radar: "逻辑能力", desc: "拆解问题、分析问题与提出方案的能力" },
  outcome_orientation: { label: "结果导向", radar: "结果导向", desc: "是否具备目标意识、推进意识与复盘意识，关注产出效果和持续优化" },
  project_execution: { label: "项目展现力", radar: "项目展现力", desc: "是否清晰呈现项目背景、职责、动作与结果" },
  role_alignment: { label: "岗位契合度", radar: "岗位契合度", desc: "与目标岗位要求、工作场景和能力预期的匹配度" },
};

function renderReport(status) {
  const report = status.finalReport || {};
  $("report-ai-source").textContent = report.generationSource === "llm_partial"
    ? "AI 生成 · 逐题部分降级"
    : "AI 生成";
  // 新规则：综合分 0~100。兼容旧 0~5 字段（*20）。
  const rawOverall = Number(report.overallScore ?? report.cover?.score ?? 0);
  const overall = rawOverall <= 5 ? Math.round(rawOverall * 20) : Math.max(0, Math.min(100, rawOverall));
  const ring = $("cover-score-fill");
  const circ = 2 * Math.PI * 40;
  ring.style.strokeDasharray = circ.toFixed(1);
  ring.style.strokeDashoffset = (circ * (1 - overall / 100)).toFixed(1);
  $("cover-score-num").textContent = String(Math.round(overall));
  $("report-title").textContent = report.cover?.title || status.reportLabel || "模拟面试报告";
  $("cover-type").textContent = report.cover?.interviewType || status.coverType || "综合面试";
  $("cover-duration").textContent = report.cover?.durationText || status.coverDuration || estimateInterviewDuration(status.transcript || []);
  $("cover-time").textContent = report.cover?.generatedAt || status.coverTime || formatReportTime();
  $("report-summary").textContent = report.summary || "本场面试已完成，但暂未生成完整点评。";

  const radarData = buildRadarData(report);
  renderRadarChart(radarData);
  renderDimensionList(radarData, report.dimensionCommentaries || []);
  renderPlanSection(report, radarData);

  fillList("weaknesses-list", report.highlights?.alerts || report.weaknesses);
  fillList("recs-list", report.highlights?.advice || report.recommendations);
  renderQaList(report.qaAnalyses || [], status.transcript || [], radarData);
}

function scoreToTen(score) {
  // 兼容旧 0~5 维度分；新规则下 0~10 原样返回
  const value = Number(score) || 0;
  return Math.max(0, Math.min(10, value <= 5 ? value * 2 : value));
}

function formatReportTime() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function estimateInterviewDuration(transcript) {
  const answers = (transcript || []).filter((turn) => turn.role === "candidate" && turn.type === "answer").length;
  const minutes = Math.max(10, answers * 6 + 12);
  return `${minutes}分钟`;
}

function buildRadarData(report) {
  const dims = report.dimensions || {};
  return [
    buildRadarEntry("communication_clarity", dims.communication_clarity, 3.8),
    buildRadarEntry("problem_solving", dims.problem_solving || dims.structured_thinking, 3.8),
    buildRadarEntry("outcome_orientation", dims.outcome_orientation || dims.result_orientation || dims.technical_depth, 3.8),
    buildRadarEntry("project_execution", dims.project_execution || dims.execution_ownership, 3.8),
    buildRadarEntry("role_alignment", dims.role_alignment || dims.business_alignment || dims.business_understanding, 3.8),
  ];
}

function buildRadarEntry(key, dim, fallback) {
  const meta = REPORT_DIMENSION_META[key];
  const score = Math.max(0, Math.min(10, scoreToTen(Number(dim && dim.score) || fallback || 3)));
  return {
    key,
    label: meta.label,
    radar: meta.radar,
    desc: meta.desc,
    score,
    evidence: (dim && dim.evidence) || [],
    concerns: (dim && dim.concerns) || [],
    recommendations: (dim && dim.recommendations) || [],
  };
}

function renderRadarChart(items) {
  const svg = $("radar-chart");
  svg.innerHTML = "";
  const cx = 176;
  const cy = 158;
  const radius = 112;
  const steps = 5;
  const angles = items.map((_, index) => (-Math.PI / 2) + (Math.PI * 2 * index / items.length));

  for (let step = 1; step <= steps; step++) {
    const r = radius * (step / steps);
    const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    polygon.setAttribute("points", angles.map((angle) => pointAt(cx, cy, angle, r)).join(" "));
    polygon.setAttribute("class", "radar-grid-line");
    svg.appendChild(polygon);
  }
  angles.forEach((angle) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    const [x, y] = pointAt(cx, cy, angle, radius).split(",");
    line.setAttribute("x1", String(cx));
    line.setAttribute("y1", String(cy));
    line.setAttribute("x2", x);
    line.setAttribute("y2", y);
    line.setAttribute("class", "radar-spoke");
    svg.appendChild(line);
  });

  const area = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
  area.setAttribute(
    "points",
    items
      .map((item, index) => pointAt(cx, cy, angles[index], radius * (item.score / 10)))
      .join(" ")
  );
  area.setAttribute("class", "radar-area");
  svg.appendChild(area);

  items.forEach((item, index) => {
    const point = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    const [x, y] = pointAt(cx, cy, angles[index], radius * (item.score / 10)).split(",");
    point.setAttribute("cx", x);
    point.setAttribute("cy", y);
    point.setAttribute("r", "4.6");
    point.setAttribute("class", "radar-point");
    svg.appendChild(point);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    const [lx, ly] = pointAt(cx, cy, angles[index], radius + 28).split(",");
    label.setAttribute("x", lx);
    label.setAttribute("y", ly);
    label.setAttribute("text-anchor", labelAnchor(angles[index]));
    label.setAttribute("class", "radar-axis-label");
    label.textContent = item.radar;
    svg.appendChild(label);

    const scoreText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    const [sx, sy] = pointAt(cx, cy, angles[index], radius * (item.score / 10) + 18).split(",");
    scoreText.setAttribute("x", sx);
    scoreText.setAttribute("y", sy);
    scoreText.setAttribute("text-anchor", labelAnchor(angles[index]));
    scoreText.setAttribute("class", "radar-score-label");
    scoreText.textContent = `${Math.round(item.score)}/10`;
    svg.appendChild(scoreText);
  });
}

function pointAt(cx, cy, angle, radius) {
  return `${(cx + Math.cos(angle) * radius).toFixed(1)},${(cy + Math.sin(angle) * radius).toFixed(1)}`;
}

function labelAnchor(angle) {
  const cos = Math.cos(angle);
  if (cos > 0.35) return "start";
  if (cos < -0.35) return "end";
  return "middle";
}

function renderDimensionList(items, commentaries) {
  const wrap = $("dimension-list");
  wrap.innerHTML = "";
  const commentaryMap = new Map(
    (commentaries || []).map((item) => [String(item.key || ""), item])
  );
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "dimension-item";
    row.innerHTML = `
      <div class="dimension-name"></div>
      <div class="dimension-copy"></div>
      <div class="dimension-score"></div>
    `;
    row.querySelector(".dimension-name").textContent = item.label;
    row.querySelector(".dimension-copy").textContent = buildDimensionCopy(
      item,
      commentaryMap.get(item.key)
    );
    row.querySelector(".dimension-score").textContent = `${Math.round(item.score)}/10`;
    wrap.appendChild(row);
  }
}

function buildDimensionCopy(item, commentary) {
  if (commentary && commentary.commentary) return commentary.commentary;
  if (item.evidence && item.evidence.length) return item.evidence[0];
  if (item.concerns && item.concerns.length) return item.concerns[0];
  return item.desc;
}

function renderPlanSection(report, items) {
  const tagsWrap = $("plan-tags");
  tagsWrap.innerHTML = "";
  const explicitTags = report.learningPlan?.tags || [];
  const tags = explicitTags.length
    ? explicitTags
    : []
        .concat((report.weaknesses || []).slice(0, 2))
        .concat((report.recommendations || []).slice(0, 2))
        .filter(Boolean);
  for (const text of tags.slice(0, 4)) {
    const tag = document.createElement("span");
    tag.className = "plan-tag";
    tag.textContent = shorten(text, 18);
    tagsWrap.appendChild(tag);
  }
  const planWrap = $("plan-steps");
  planWrap.innerHTML = "";
  const phases = buildPlanPhases(report, items);
  for (const phase of phases) {
    const block = document.createElement("div");
    block.className = "plan-phase";
    block.innerHTML = `<h3></h3><ul></ul>`;
    block.querySelector("h3").innerHTML = `${phase.title}<span>${phase.period}</span>`;
    const ul = block.querySelector("ul");
    for (const point of phase.points) {
      const li = document.createElement("li");
      li.textContent = point;
      ul.appendChild(li);
    }
    planWrap.appendChild(block);
  }
}

function buildPlanPhases(report, items) {
  if (report.learningPlan?.phases?.length) {
    return report.learningPlan.phases.map((phase) => ({
      title: phase.title || "阶段计划",
      period: phase.window ? `(${phase.window})` : "",
      points: Array.isArray(phase.items) && phase.items.length ? phase.items : ["继续补充训练内容。"],
    }));
  }
  const weak = (report.weaknesses || [])[0] || "表达完整度";
  const rec = (report.recommendations || [])[0] || "按背景、目标、动作、结果的顺序复盘案例";
  const low = [...items].sort((a, b) => a.score - b.score)[0];
  return [
    {
      title: "立即行动",
      period: "(1-2周)",
      points: [
        `针对“${weak}”做一次专项复盘，整理 2 到 3 个可复用回答模板。`,
        `围绕${low ? low.label : "薄弱维度"}补充真实案例，确保每个案例都能讲出动作与结果。`,
        rec,
      ],
    },
    {
      title: "短期目标",
      period: "(1个月)",
      points: [
        "每周完整模拟一场面试，重点训练开场自我介绍、项目经历和追问应对。",
        "把高频问题整理成题库，逐步补齐结构化表达与业务视角。",
        "录音复盘自己的回答，检查是否存在过短、跳跃或缺证据的问题。",
      ],
    },
    {
      title: "中期规划",
      period: "(2-3个月)",
      points: [
        "形成 1 套稳定的代表项目讲述模板，覆盖背景、目标、关键动作、结果与复盘。",
        "针对目标岗位持续补充更有说服力的案例，提升岗位契合度。",
        "定期回看本报告中的低分维度，逐项修正并验证改进效果。",
      ],
    },
  ];
}

function shorten(text, max) {
  const value = String(text || "");
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function renderQaList(qaAnalyses, transcript, items) {
  const wrap = $("qa-list");
  wrap.innerHTML = "";
  const records = Array.isArray(qaAnalyses) && qaAnalyses.length
    ? qaAnalyses.map((item) => ({
        question: item.question || "",
        answer: item.answer || "",
        kind: "主问题",
        strengths: item.strengths || [],
        risks: item.risks || [],
        commentary: item.commentary || "",
        approach: item.approach || [],
        referenceAnswer: item.referenceAnswer || "",
        questionIndex: item.questionIndex || 0,
      }))
    : buildQaRecords(transcript || []);
  $("qa-total").textContent = `共 ${records.length} 题`;
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "qa-item";
    empty.textContent = "暂无问答记录。";
    wrap.appendChild(empty);
    return;
  }
  const weakest = [...items].sort((a, b) => a.score - b.score)[0];
  records.forEach((record, index) => {
    const score = estimateQaScore(record);
    const item = document.createElement("article");
    item.className = "qa-item";
    item.innerHTML = `
      <div class="qa-item-head">
        <span class="qa-item-badge"></span>
        <span class="qa-item-score"></span>
      </div>
      <div class="qa-prompt"></div>
      <div class="qa-answer-wrap"></div>
      <div class="qa-two-col">
        <div class="qa-chip-card good">
          <h4>优点</h4>
          <ul class="qa-good-list"></ul>
        </div>
        <div class="qa-chip-card warn">
          <h4>待改进</h4>
          <ul class="qa-warn-list"></ul>
        </div>
      </div>
      <div class="qa-analysis">
        <h4>面试官点评与参考思路</h4>
        <p class="qa-analysis-text"></p>
      </div>
    `;
    item.querySelector(".qa-item-badge").textContent = `第 ${record.questionIndex || index + 1} 题`;
    item.querySelector(".qa-item-score").textContent = `${score}/10分`;
    item.querySelector(".qa-prompt").textContent = `【${record.kind}】${record.question}`;
    item.querySelector(".qa-answer-wrap").textContent = `你的回答：${record.answer || "本题未形成有效回答。"}`;
    fillMiniList(item.querySelector(".qa-good-list"), buildQaStrengths(record));
    fillMiniList(item.querySelector(".qa-warn-list"), buildQaWeaknesses(record, weakest));
    item.querySelector(".qa-analysis-text").textContent = buildQaAnalysis(record, score);
    wrap.appendChild(item);
  });
}

function buildQaRecords(transcript) {
  const records = [];
  let current = null;
  for (const turn of transcript || []) {
    if (turn.role === "interviewer" && isQuestionTurn(turn.type)) {
      if (current) records.push(current);
      current = {
        question: turn.text || "",
        answerParts: [],
        kind: turn.type === "follow_up" ? "追问" : "主问题",
      };
      continue;
    }
    if (!current) continue;
    if (turn.role === "candidate" && turn.type === "answer") {
      current.answerParts.push(turn.text || "");
    }
  }
  if (current) records.push(current);
  return records.map((item) => ({
    question: item.question,
    answer: item.answerParts.join("\n").trim(),
    kind: item.kind,
  }));
}

function isQuestionTurn(type) {
  return ["main_question", "follow_up", "question", "self_intro"].includes(type);
}

function estimateQaScore(record) {
  if (typeof record.score === "number") return Math.max(0, Math.min(10, Math.round(record.score * 2)));
  const len = (record.answer || "").length;
  if (!len) return 0;
  if (len >= 120) return 8;
  if (len >= 70) return 6;
  if (len >= 30) return 4;
  return 2;
}

function buildQaStrengths(record) {
  if (record.strengths && record.strengths.length) return record.strengths;
  const result = [];
  if (record.answer) result.push("有正面回应题目，没有完全跳过。");
  if ((record.answer || "").length >= 50) result.push("回答中包含了一定信息量和细节。");
  if (record.kind === "追问") result.push("能够继续接住追问，说明有一定延展回答能力。");
  return result.length ? result : ["无"];
}

function buildQaWeaknesses(record, weakest) {
  if (record.risks && record.risks.length) return record.risks;
  const result = [];
  if (!record.answer) return ["本题没有形成有效回答。"];
  if ((record.answer || "").length < 40) result.push("回答偏短，信息密度不足。");
  if ((record.answer || "").length < 80) result.push("缺少背景、动作、结果的完整闭环。");
  if (weakest) result.push(`需要进一步补强“${weakest.label}”相关能力。`);
  return result.slice(0, 3);
}

function buildQaAnalysis(record, score) {
  // 真实评价 + 具体参考思路融合。后端已让 LLM 输出该字段，仅在 fallback 时由前端兜底。
  if (record.commentary) return record.commentary;
  const answer = (record.answer || "").trim();
  const question = (record.question || "").trim();
  const kind = record.kind || "主问题";
  if (!answer) {
    return `【面试官点评】本题候选人未形成有效回答，态度比较敷衍。\n\n` +
      `【参考思路】针对${kind}，建议先复述题目的考察点，再以「背景—目标—动作—结果—复盘」的结构给出一个真实案例；` +
      `若确实没有对应经历，也应说明当时的处理方式与思考过程，而不是直接沉默。`;
  }
  const answerLen = answer.length;
  const topicHint = question ? `围绕「${question.slice(0, 30)}${question.length > 30 ? "…" : ""}」` : "针对本题";
  if (score >= 8) {
    return `【面试官点评】候选人${topicHint}的回答相对完整，主线清楚、关键事实可被验证，` +
      `已具备一定的说服力与判断力；但在量化结果、方案取舍和复盘机制上还有可以再深一寸的地方。\n\n` +
      `【参考思路】可以继续沿用「背景—目标—动作—结果—复盘」的结构，下一次主动补齐三类信息：` +
      `① 当时面对的关键约束或资源限制；② 与替代方案的取舍依据；③ 上线后用哪类指标验证、后续又做了哪些调整。`;
  }
  if (score >= 5) {
    return `【面试官点评】候选人${topicHint}能够围绕问题作答，但信息密度、逻辑层次和细节深度都还停在基础层面，` +
      `关键判断和结果缺少显式证据，回答容易显得「在理但不可信」。\n\n` +
      `【参考思路】先把回答压缩到 2~3 句最核心结论，再补三类支撑：` +
      `① 真实场景/时间/角色；② 关键动作里你具体做了什么、为什么这样做；③ 用一个数字结果或用户反馈来证明。`;
  }
  if (answerLen < 40) {
    return `【面试官点评】候选人${topicHint}虽然有回应，但内容过短、信息量过少，暂时不足以支撑进一步评估。\n\n` +
      `【参考思路】即使是简答，也建议包含「背景、动作、结果」三个最小要素，至少让面试官能判断你做过这件事、怎么做的、结果如何。`;
  }
  return `【面试官点评】候选人${topicHint}有回应但缺少关键证据和完整闭环，回答里看不到背景、关键动作和结果之间的因果关系。\n\n` +
    `【参考思路】先确定一个最接近的真实案例，按「我面对什么情境 → 我做了什么关键决定 → 得到了什么结果」来讲；` +
    `同时补一个可以量化的结果或用户反馈，避免只停留在描述动作。`;
}

function fillMiniList(ul, items) {
  ul.innerHTML = "";
  for (const item of items || []) {
    const li = document.createElement("li");
    li.textContent = item;
    ul.appendChild(li);
  }
}

function fillList(id, items) {
  const ul = $(id);
  ul.innerHTML = "";
  for (const item of items || []) { const li = document.createElement("li"); li.textContent = item; ul.appendChild(li); }
  if (!ul.children.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "（无）"; ul.appendChild(li); }
}

/* ---------------- profile ---------------- */

// No profile memory yet: every session is fresh — the candidate fills the form each
// time and it's sent only for this interview. Persistent profiles arrive with the
// account system later. So there is no loadProfile()/prefill here on purpose.
async function saveProfileIfDirty() {
  const hasNewFile = !ENTERPRISE_MODE && $("prep-resume-file").files.length > 0;
  if (!profileDirty && !hasNewFile) return;
  const form = new FormData();
  if (!ENTERPRISE_MODE) {
    form.append("target_role", $("prep-role").value);
    form.append("jd_text", $("prep-jd").value);
  }
  if (ENTERPRISE_MODE) {
    form.append("enterprise", "true");
    form.append("candidate_name", $("prep-candidate-name").value);
    form.append("candidate_contact", $("prep-candidate-contact").value);
  } else {
    form.append("enterprise", "false");
    form.append("resume_text", $("prep-resume-text").value);
  }
  if (hasNewFile) form.append("resume_file", $("prep-resume-file").files[0]);
  const res = await fetch("/api/interview/profile", { method: "POST", body: form });
  const data = await res.json();
  if (!data.success) throw new Error(data.error || "资料保存失败");
  profileDirty = false;
}

function showEnterpriseComplete() {
  S.interviewOver = true;
  stopPolling();
  setView("finished");
  const complete = $("enterprise-complete");
  if (complete) complete.hidden = false;
  const loading = $("report-loading");
  if (loading) loading.hidden = true;
  const report = $("report");
  if (report) report.hidden = true;
}

/* ---------------- debug ---------------- */

function debugLog(line) {
  if (!DEBUG) return;
  const el = $("debug-log");
  el.textContent = (new Date()).toLocaleTimeString() + " " + line + "\n" + el.textContent.slice(0, 4000);
}

const FIXTURE_STATUS = {
  state: "completed", candidateMessage: "面试已完成，可以查看反馈报告。",
  reportLabel: "产品经理模拟面试报告",
  stageLabel: "面试收尾中",
  stageProgressPercent: 100,
  progressPercent: 100, questionsCompleted: 3, totalQuestions: 3, terminationReason: null,
  transcript: [
    { turnId: "t1", role: "interviewer", type: "opening", text: "你好，我是林面试官。今天我们围绕你和产品经理岗位的匹配度展开交流。" },
    { turnId: "t2", role: "interviewer", type: "main_question", text: "请你先介绍一个你主导过的产品项目，重点说说目标、你的职责和最终结果。" },
    { turnId: "t3", role: "candidate", type: "answer", text: "我主导过一个 AI 助手从 0 到 1 的产品化项目，负责需求梳理、MVP 设计、上线验证和数据复盘，上线后次周留存提升了 12%。" },
    { turnId: "t5", role: "interviewer", type: "follow_up", text: "我想顺着这里多问一句。你当时是如何判断这个需求值得优先做，以及如何验证效果的？" },
    { turnId: "t6", role: "candidate", type: "answer", text: "我先看了用户任务链路里的流失点，确认这是高频痛点，再通过小范围灰度验证转化率和留存变化，数据验证后再推动全量上线。" },
    { turnId: "t7", role: "system", type: "question_skipped", text: "hard_timeout_no_answer", metadata: { reason: "hard_timeout_no_answer" } },
    { turnId: "t8", role: "interviewer", type: "closing", text: "今天的模拟面试就到这里，稍后你可以查看完整反馈报告。" },
  ],
  finalReport: {
    summary: "候选人在项目叙述、需求判断和结果复盘上具备一定基础，能够说明产品目标与数据结果之间的关系；如果继续加强用户洞察深度与方案权衡表达，整体竞争力会更强。",
    overallScore: 4.1,
    strengths: ["项目目标、职责和结果交代比较完整。", "能够用数据说明上线后的效果变化。", "面对追问时能补充一定的验证思路。"],
    weaknesses: ["用户洞察过程说得还不够深入。", "方案取舍和优先级判断缺少更完整的推导过程。"],
    recommendations: ["补充 2 到 3 个用户研究与需求验证案例，形成稳定的表达模板。", "回答时强化“为什么做、为什么先做、为什么这样做”的推导链路。"],
    dimensions: {
      communication_clarity: { score: 4.2, evidence: ["回答主线比较清楚，关键结果表达明确"], concerns: ["少量回答仍偏结论先行"], recommendations: ["强化结构化表达"], confidence: "medium" },
      problem_solving: { score: 3.9, evidence: ["能给出基于数据验证的判断路径"], concerns: ["方案比较与取舍逻辑仍不够完整"], recommendations: ["强化方案取舍与优先级判断表达"], confidence: "medium" },
      outcome_orientation: { score: 4.1, evidence: ["能说明灰度验证、上线节奏和数据复盘的闭环"], concerns: ["部分回答更强调结论，复盘细节仍可展开"], recommendations: ["补充目标设定、推进动作和复盘机制"], confidence: "medium" },
      project_execution: { score: 4.4, evidence: ["主导 AI 助手产品从 0 到 1 并说明留存提升"], concerns: ["部分推进细节未充分展开"], recommendations: ["增加跨团队协同与推进阻力的描述"], confidence: "high" },
      role_alignment: { score: 4.0, evidence: ["能把用户痛点、优先级和指标变化关联起来"], concerns: ["用户价值洞察深度还可增强"], recommendations: ["补充更细的用户洞察过程"], confidence: "medium" },
    },
  },
};

function loadFixture() {
  S.interviewStarted = true; S.interviewOver = false;
  renderedTurns.clear(); $("chat-log").innerHTML = ""; S.questionCount = 0;
  setView("interview");
  renderHeader(FIXTURE_STATUS);
  renderTranscript(FIXTURE_STATUS.transcript);
  gateComposer({ state: "listening" });
  toast("已载入示例对话（debug）");
}
function loadFixtureReport(showToast = true) {
  S.interviewOver = false;
  renderReport(FIXTURE_STATUS);
  setView("finished");
  S.interviewOver = true;
  if (showToast) toast("已载入示例报告（debug）");
}

/* ---------------- init ---------------- */

function initBrowserAsr() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    S.speechRecognition = new SpeechRecognition();
    S.speechRecognition.continuous = true;
    S.speechRecognition.interimResults = true;
    S.speechRecognition.lang = 'zh-CN';

    // For browser ASR, collect every final utterance until the user presses Stop.
    S.collectedTranscript = "";

    S.speechRecognition.onresult = (event) => {
      if (!captureAllowedFor(S.lastStatus, S.browserAsrExchangeId)) return;
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += transcript;
          S.collectedTranscript = appendCaptionSegment(
            S.collectedTranscript,
            transcript,
          );
        } else {
          interim += transcript;
        }
      }
      // Always show current progress in the same infinitely growing bubble.
      const displayText = appendCaptionSegment(S.collectedTranscript, interim);
      if (displayText) {
        upsertDraftBubble(displayText);
      }
      if (final.trim()) scheduleBrowserAsrSubmit();
    };

    S.speechRecognition.onerror = (event) => {
      if (event.error !== 'no-speech') {
        toast("语音识别错误：" + event.error, false);
      }
      if (
        S.isListening &&
        !S.interviewOver &&
        captureAllowedFor(S.lastStatus, S.browserAsrExchangeId)
      ) {
        // Try to restart on error
        setTimeout(() => {
          if (
            S.isListening &&
            S.speechRecognition &&
            captureAllowedFor(S.lastStatus, S.browserAsrExchangeId)
          ) {
            try { S.speechRecognition.start(); } catch (e) {}
          }
        }, 100);
      }
    };

    S.speechRecognition.onend = () => {
      if (
        S.isListening &&
        !S.interviewOver &&
        captureAllowedFor(S.lastStatus, S.browserAsrExchangeId)
      ) {
        // Auto-restart for continuous listening
        try { S.speechRecognition.start(); } catch (e) {}
      }
    };

    S.speechRecognition.onstart = () => {
      S.isListening = true;
    };

    // Use browser ASR if no platform ASR available
    S.useBrowserAsr = true;
  }
}

function startBrowserAsr(exchangeId) {
  if (S.speechRecognition && !S.isListening) {
    if (!captureAllowedFor(S.lastStatus, exchangeId)) return;
    try {
      S.collectedTranscript = "";
      S.browserAsrExchangeId = exchangeId;
      S.speechRecognition.start();
      S.isListening = true;
    } catch (e) {
      console.error("Failed to start speech recognition", e);
    }
  }
}

function scheduleBrowserAsrSubmit() {
  clearTimeout(S.browserSubmitTimer);
  S.browserSubmitTimer = setTimeout(submitBrowserAsrAnswer, 900);
}

async function submitBrowserAsrAnswer() {
  S.browserSubmitTimer = null;
  const exchangeId = S.browserAsrExchangeId;
  const text = String(S.collectedTranscript || "").trim();
  const captureMs = S.captureStartedAt ? performance.now() - S.captureStartedAt : 0;
  if (
    !text ||
    captureMs < 240 ||
    S.captureVoicedMs < 180 ||
    !captureAllowedFor(S.lastStatus, exchangeId)
  ) return;
  try {
    const result = await postJSON("/api/interview/asr-answer", {
      text,
      exchangeId,
      requestId: `browser_asr_${Date.now()}`,
    });
    if (!result.ok || !result.data.success) {
      throw new Error(result.data.error || "语音回答已过期");
    }
    S.collectedTranscript = "";
    S.browserAsrExchangeId = "";
    S.micDesired = false;
    requestMic(false);
  } catch (err) {
    toast("发送失败：" + (err && err.message ? err.message : err), false);
  }
}

function stopBrowserAsr() {
  if (S.speechRecognition) {
    clearTimeout(S.browserSubmitTimer);
    S.browserSubmitTimer = null;
    try {
      S.speechRecognition.stop();
    } catch (e) {}
    S.isListening = false;
    S.collectedTranscript = "";
    S.browserAsrExchangeId = "";
  }
}

function init() {
  if (DEBUG) document.body.classList.add("debug");

  if (DEBUG_REPORT_ONLY) {
    $("again-btn").onclick = () => location.reload();
    if ($("dbg-fixture-report")) $("dbg-fixture-report").onclick = () => loadFixtureReport(true);
    if ($("dbg-toggle")) $("dbg-toggle").onclick = () => $("debug-body").classList.toggle("show");
    loadFixtureReport(false);
    return;
  }

  setView("prep");
  initBrowserAsr();
  loadRoster();
  loadLatestReportStatus();

  $("start-btn").onclick = onStartInterview;
  $("readiness").title = "连接失败后点此重试";
  $("readiness").onclick = () => {
    if (S.preheat === "failed" || S.preheat === "released") preheat();
  };
  for (const id of ["prep-role", "prep-jd", "prep-resume-text"]) {
    $(id).addEventListener("input", () => { profileDirty = true; updateStartGate(); });
  }
  for (const id of ["prep-candidate-name", "prep-candidate-contact"]) {
    $(id).addEventListener("input", () => { profileDirty = true; updateStartGate(); });
  }
  $("prep-resume-file").addEventListener("change", () => { profileDirty = true; updateStartGate(); });
  updateStartGate();  // start disabled until the required fields are filled

  $("mic-btn").onclick = () => toast("麦克风会在面试官说完后自动开启");
  $("wave-stop").onclick = () => toast("麦克风会随面试轮次自动控制");
  $("send-btn").onclick = sendTextAnswer;
  $("answer-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendTextAnswer(); } });
  $("answer-input").addEventListener("input", () => gateComposer(S.lastStatus || { state: "idle" }));

  $("end-btn").onclick = onEndInterview;
  $("confirm-end").onclick = confirmEnd;
  $("cancel-end").onclick = () => $("confirm-modal").classList.remove("show");
  $("again-btn").onclick = () => location.reload();
  $("report-retry-btn").onclick = retryReportGeneration;
  $("report-skip-btn").onclick = dismissReportToPrep;
  $("report-skip-btn").hidden = ENTERPRISE_MODE;
  $("prep-report-entry").onclick = openReportFromPrep;

  if (DEBUG) {
    $("dbg-fixture").onclick = loadFixture;
    $("dbg-fixture-report").onclick = loadFixtureReport;
    $("dbg-toggle").onclick = () => $("debug-body").classList.toggle("show");
  }

  // Any real gesture re-permits audio: guarantees the meter's AudioContext resumes
  // even if the browser suspended it when the mic auto-started after "开始面试".
  for (const evt of ["pointerdown", "keydown"]) {
    document.addEventListener(evt, resumeMeter, { passive: true });
  }

  // If the candidate backed out to prep after a connection attempt, keep the
  // temporary session alive during active interaction and only release on real idleness.
  for (const evt of ["pointerdown", "keydown"]) {
    document.addEventListener(evt, () => {
      if (S.sessionReady && !S.interviewStarted && document.body.dataset.view === "prep") {
        scheduleIdleRelease();
      }
    }, { passive: true });
  }

  window.addEventListener("pagehide", (event) => {
    // BFCache temporarily hides a page and may restore it later; a real tab
    // close/navigation must explicitly release even an enterprise room.
    if (event.persisted) return;
    stopDirectAsrCapture({ closeSocket: true });
    if (S.sessionReady || S.interviewStarted) {
      const sessionToClose = S.sessionId || S.closedSessionId;
      const stopUrl = sessionToClose
        ? `/api/stop-session?sessionId=${encodeURIComponent(sessionToClose)}&release=1`
        : "/api/stop-session?release=1";
      navigator.sendBeacon(stopUrl);
    }
  });
}

init();
