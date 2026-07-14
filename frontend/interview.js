/* Interview page logic — served at /interview.js, loaded by interview.html.
   Views: prep → connecting(fallback) → interview → finished. The avatar
   session is preheated on page load; the interview itself starts on CTA. */

/* ---------------- constants & state ---------------- */

const LISTENING_STATES = new Set(["listening", "thinking_check"]);
const TYPING_STATES = new Set(["analyzing", "deciding_followup", "planning_followup"]);
const TERMINAL_STATES = new Set(["completed", "terminated"]);
const IDLE_RELEASE_MS = 3 * 60 * 1000;
const DEBUG = new URLSearchParams(location.search).has("debug");

const TERMINATION_TEXT = {
  user_stopped: "你主动结束了本场面试",
  too_many_no_answer_timeouts: "多道题未收到回答，面试提前结束",
  insufficient_effective_answers: "有效回答不足，面试提前结束",
};
const SKIP_REASON_TEXT = { hard_timeout_no_answer: "该题超时未作答，已跳过" };

const S = {
  client: null,
  sessionReady: false,
  preheat: "idle", // idle | connecting | ready | failed | released
  asrAvailable: false,
  interviewStarted: false,
  interviewOver: false,
  inputMode: localStorage.getItem("interviewInputMode") || "voice",
  micOn: false,
  pollTimer: null,
  idleTimer: null,
  levelRaf: null,
  starting: false,
  questionCount: 0,
  lastStatus: null,
  lastError: "",
};
const renderedTurns = new Map();

function $(id) { return document.getElementById(id); }

function setView(view) {
  document.body.dataset.view = view;
}

function toast(msg, ok = true) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "show " + (ok ? "ok" : "err");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ""; }, ok ? 2600 : 6000);
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

/* ---------------- platform error mapping ---------------- */

function mapPlatformError(raw) {
  const text = String(raw || "");
  const match = text.match(/\[(\d+)\]\s*([A-Z_]+)/);
  const code = match ? match[1] : "";
  const table = {
    40001: { title: "平台资源繁忙", hint: "调度层暂时满载，稍等片刻即可。", retry: "auto" },
    40002: { title: "平台资源繁忙", hint: "渲染层暂时满载，稍等片刻即可。", retry: "auto" },
    40003: { title: "数字人会话启动失败", hint: "若持续出现，请到 LiveAvatar 控制台确认该数字人已发布并共享。", retry: "semi" },
    40004: { title: "API Key 无效", hint: "请在控制台（localhost:8000）检查平台 API Key。", retry: "none" },
    40005: { title: "并发会话已达上限", hint: "请关闭其他进行中的会话后重试，或升级套餐。", retry: "manual" },
    40006: { title: "使用额度已用尽", hint: "请在平台控制台充值或切换沙箱环境。", retry: "none" },
    40007: { title: "无权访问该会话", hint: "请检查凭证归属后重试。", retry: "none" },
  };
  const entry = table[code];
  if (entry) return { ...entry, detail: text };
  return { title: "连接失败", hint: "请检查网络与后端服务后重试。", retry: "manual", detail: text };
}

/* ---------------- session preheat & lifecycle ---------------- */

function setReadiness(state, text) {
  S.preheat = state;
  const chip = $("readiness");
  chip.dataset.state = state;
  chip.querySelector("span").textContent = text;
}

function scheduleIdleRelease() {
  clearTimeout(S.idleTimer);
  S.idleTimer = setTimeout(async () => {
    if (S.interviewStarted || document.body.dataset.view !== "prep") return;
    await teardownSession();
    setReadiness("released", "面试间已释放，开始时将重新连接");
  }, IDLE_RELEASE_MS);
}

async function teardownSession() {
  clearTimeout(S.idleTimer);
  if (S.client) {
    try { await S.client.disconnect(); } catch (e) { /* already gone */ }
    S.client = null;
  }
  S.sessionReady = false;
  try { await postJSON("/api/stop-session"); } catch (e) { /* offline */ }
}

function buildClient(data) {
  const container = $("avatar-stage");
  container.innerHTML = "";
  const client = LivekitSDK.createClient({
    connectConfig: {
      type: "direct",
      config: { sfuUrl: data.sfuUrl, userToken: data.userToken },
    },
    video: { containerElement: container, fitMode: "contain" },
    audio: {
      output: { enabled: true, volume: 1.0, muted: false },
      input: {
        noiseSuppression: true,
        voiceIsolation: true,
        sampleRate: 24000,
        constraints: { echoCancellation: true, autoGainControl: true },
      },
    },
  });
  client.events.on("sdk:connected", () => {
    $("conn-dot").dataset.state = "on";
    $("reconnect-banner").classList.remove("show");
  });
  client.events.on("sdk:disconnected", async () => {
    $("conn-dot").dataset.state = "off";
    S.sessionReady = false;
    if (S.interviewStarted && !S.interviewOver) {
      $("reconnect-banner").classList.add("show");
      try { await client.reconnect(); S.sessionReady = true; } catch (e) { /* banner stays */ }
    }
  });
  client.events.on("sdk:error", (info) => {
    debugLog("sdk:error " + JSON.stringify(info));
  });
  client.events.on("conversation:asr:chunk", (chunk) => {
    if (chunk && chunk.text) upsertDraftBubble(chunk.text);
    debugLog("asr:chunk qid=" + (chunk && chunk.questionId));
  });
  return client;
}

async function connectWithTimeout(client, timeoutMs) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("连接超时（15s）")), timeoutMs);
  });
  try { await Promise.race([client.connect(), timeout]); }
  finally { clearTimeout(timer); }
}

async function startSession() {
  const { ok, data } = await postJSON("/api/start-session");
  if (!ok || !data.success) throw new Error(data.error || "start-session failed");
  S.asrAvailable = Boolean(data.asrAvailable);
  S.client = buildClient(data);
  await connectWithTimeout(S.client, 15000);
  if (S.client.isMuted) S.client.unmute();
  S.sessionReady = true;
}

async function preheat() {
  if (S.preheat === "connecting" || S.sessionReady) return;
  setReadiness("connecting", "正在准备面试间…");
  try {
    await startSession();
    setReadiness("ready", "面试间已就绪");
    scheduleIdleRelease();
  } catch (err) {
    const info = mapPlatformError(err && err.message);
    S.lastError = info.detail;
    setReadiness("failed", info.title + "（可点击重试）");
    debugLog("preheat failed: " + info.detail);
  }
}

/* ---------------- start interview (CTA) ---------------- */

async function onStartInterview() {
  if (S.starting) return;
  S.starting = true;
  $("start-btn").disabled = true;
  try {
    await saveProfileIfDirty();
    if (!S.sessionReady) {
      await coldConnectFlow(); // switches to connecting view; throws on failure
    }
    const { ok, data } = await postJSON("/api/interview/start");
    if (!ok || !data.success) throw new Error(data.error || "面试启动失败");
    enterInterview();
  } catch (err) {
    if (document.body.dataset.view === "connecting") {
      showConnectError(err);
    } else {
      toast("开始面试失败：" + (err && err.message ? err.message : err), false);
    }
  } finally {
    S.starting = false;
    $("start-btn").disabled = false;
  }
}

function setStep(n, state) {
  for (let i = 1; i <= 4; i++) {
    const li = $("step-" + i);
    if (i < n) li.dataset.state = "done";
    else if (i === n) li.dataset.state = state;
    else li.dataset.state = "";
  }
}

async function coldConnectFlow() {
  setView("connecting");
  $("connect-error").classList.remove("show");
  setStep(1, "done");
  setStep(2, "active");
  const attempt = async () => {
    await startSession();
    setStep(3, "done");
    setStep(4, "active");
  };
  let autoLeft = 2;
  for (;;) {
    try {
      await attempt();
      return;
    } catch (err) {
      const info = mapPlatformError(err && err.message);
      const canAuto = (info.retry === "auto" && autoLeft > 0) || (info.retry === "semi" && autoLeft === 2);
      if (!canAuto) throw err;
      autoLeft -= 1;
      setStep(2, "active");
      await new Promise((r) => setTimeout(r, 2500));
    }
  }
}

function showConnectError(err) {
  const info = mapPlatformError(err && err.message ? err.message : String(err));
  $("connect-error-title").textContent = info.title;
  $("connect-error-hint").textContent = info.hint;
  $("connect-error-detail").textContent = info.detail;
  $("retry-btn").style.display = info.retry === "none" ? "none" : "";
  $("connect-error").classList.add("show");
}

function enterInterview() {
  clearTimeout(S.idleTimer);
  S.interviewStarted = true;
  S.interviewOver = false;
  S.questionCount = 0;
  renderedTurns.clear();
  $("chat-log").innerHTML = "";
  setView("interview");
  if (!S.asrAvailable) {
    $("mode-voice").disabled = true;
    $("mode-voice").title = "未配置语音识别（DashScope Key），仅支持文字作答";
    S.inputMode = "text";
  }
  applyInputMode(S.inputMode, { silent: true });
  startPolling();
}

/* ---------------- status polling & chat rendering ---------------- */

function startPolling() {
  stopPolling();
  S.pollTimer = setInterval(refreshStatus, 1000);
  refreshStatus();
}

function stopPolling() {
  if (S.pollTimer) clearInterval(S.pollTimer);
  S.pollTimer = null;
}

async function refreshStatus() {
  let status;
  try {
    status = await (await fetch("/api/interview/status")).json();
  } catch (err) {
    $("state-pill").textContent = "连接后端失败，重试中…";
    return;
  }
  S.lastStatus = status;
  if (DEBUG) $("debug-status").textContent = JSON.stringify(status, null, 2);
  renderHeader(status);
  renderTranscript(status.transcript || []);
  renderTyping(status);
  gateComposer(status);
  if (S.interviewStarted && TERMINAL_STATES.has(status.state)) {
    finishInterview(status);
  }
}

function renderHeader(status) {
  const pct = status.progressPercent || 0;
  $("progress-fill").style.width = pct + "%";
  $("q-count").textContent =
    (status.questionsCompleted || 0) + " / " + (status.totalQuestions || 0) + " 题";
  const pill = $("state-pill");
  pill.textContent = status.candidateMessage || "面试进行中";
  pill.dataset.kind = LISTENING_STATES.has(status.state)
    ? "listen"
    : TYPING_STATES.has(status.state)
    ? "think"
    : "info";
}

function turnElement(turn) {
  const el = document.createElement("div");
  const type = turn.type || "";
  if (turn.role === "system") {
    el.className = "sys-note";
    el.textContent = SKIP_REASON_TEXT[(turn.metadata || {}).reason] || SKIP_REASON_TEXT[turn.text] || turn.text;
    return el;
  }
  if (turn.role === "interviewer" && ["answer_acknowledgement", "thinking_check", "question_skip_transition"].includes(type)) {
    el.className = "aside-note";
    el.textContent = turn.text;
    return el;
  }
  const mine = turn.role === "candidate";
  el.className = "bubble " + (mine ? "me" : "them");
  if (type === "main_question") {
    S.questionCount += 1;
    el.dataset.badge = "第 " + S.questionCount + " 题";
  } else if (type === "follow_up") {
    el.dataset.badge = "追问";
  }
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
    log.appendChild(el);
    if (!targetLog) {
      renderedTurns.set(turn.turnId, el);
      if (turn.role === "candidate") {
        removeDraftBubble();
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

/* draft bubble for live ASR */
function upsertDraftBubble(text) {
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

function removePendingBubbles() {
  document.querySelectorAll(".bubble.pending").forEach((el) => el.remove());
}

/* ---------------- composer: voice / text ---------------- */

function applyInputMode(mode, opts = {}) {
  S.inputMode = mode;
  localStorage.setItem("interviewInputMode", mode);
  $("mode-voice").classList.toggle("active", mode === "voice");
  $("mode-text").classList.toggle("active", mode === "text");
  $("voice-pane").classList.toggle("show", mode === "voice");
  $("text-pane").classList.toggle("show", mode === "text");
  if (mode === "voice") {
    if (!opts.silent) toast("已切换到语音作答");
    setMic(true);
  } else {
    setMic(false);
    if (!opts.silent) toast("已切换到文字作答");
    $("answer-input").focus();
  }
}

async function setMic(on) {
  if (!S.client || !S.sessionReady) { S.micOn = false; updateMicUI(); return; }
  try {
    if (on && !S.micOn) {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      await postJSON("/api/interview/audio-input", { enabled: true });
      await S.client.startAudioCapture();
      S.micOn = true;
      startLevelLoop();
    } else if (!on && S.micOn) {
      await postJSON("/api/interview/audio-input", { enabled: false });
      await S.client.stopAudioCapture();
      S.micOn = false;
      stopLevelLoop();
    }
  } catch (err) {
    S.micOn = false;
    stopLevelLoop();
    toast("麦克风不可用：" + (err && err.message ? err.message : err), false);
  }
  updateMicUI();
}

function updateMicUI() {
  $("mic-btn").dataset.on = S.micOn ? "1" : "0";
  $("mic-hint").textContent = S.micOn
    ? "正在聆听 — 直接开口回答即可"
    : "麦克风已关闭，点击开启";
}

function startLevelLoop() {
  stopLevelLoop();
  const ring = $("mic-ring");
  const loop = () => {
    if (!S.micOn || !S.client) return;
    const level = S.client.getMicrophoneAudioLevel();
    const scale = 1 + Math.min(0.45, (level || 0) * 1.8);
    ring.style.transform = "scale(" + scale.toFixed(3) + ")";
    S.levelRaf = requestAnimationFrame(loop);
  };
  S.levelRaf = requestAnimationFrame(loop);
}

function stopLevelLoop() {
  if (S.levelRaf) cancelAnimationFrame(S.levelRaf);
  S.levelRaf = null;
  $("mic-ring").style.transform = "scale(1)";
}

function gateComposer(status) {
  const open = LISTENING_STATES.has(status.state);
  const input = $("answer-input");
  const send = $("send-btn");
  input.disabled = !open;
  send.disabled = !open || !input.value.trim();
  input.placeholder = open ? "输入你的回答，Enter 发送（Shift+Enter 换行）" : "请等待面试官提问…";
  $("mic-btn").disabled = !S.asrAvailable;
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
    el.remove();
    input.value = text;
    toast("发送失败：" + (err && err.message ? err.message : err), false);
  }
}

/* ---------------- finish & report ---------------- */

async function onEndInterview() {
  $("confirm-modal").classList.add("show");
}

async function confirmEnd() {
  const btn = $("confirm-end");
  btn.disabled = true;
  btn.textContent = "正在生成报告…";
  try {
    await postJSON("/api/interview/stop");
    const status = await (await fetch("/api/interview/status")).json();
    finishInterview(status);
  } catch (err) {
    toast("结束失败：" + err, false);
  } finally {
    $("confirm-modal").classList.remove("show");
    btn.disabled = false;
    btn.textContent = "结束并生成报告";
  }
}

function finishInterview(status) {
  if (S.interviewOver) return;
  S.interviewOver = true;
  stopPolling();
  stopLevelLoop();
  clearTimeout(S.idleTimer);
  setMic(false);
  renderReport(status);
  setView("finished");
  if (S.client) { S.client.disconnect().catch(() => {}); S.client = null; }
  S.sessionReady = false;
  postJSON("/api/stop-session").catch(() => {});
}

function renderReport(status) {
  const report = status.finalReport || {};
  const score = Math.max(0, Math.min(5, Number(report.overallScore) || 0));
  const ring = $("score-ring-fill");
  const circumference = 2 * Math.PI * 52;
  ring.style.strokeDasharray = circumference.toFixed(1);
  ring.style.strokeDashoffset = (circumference * (1 - score / 5)).toFixed(1);
  $("score-num").textContent = score.toFixed(1);
  $("report-summary").textContent = report.summary || "本场面试未生成总结。";

  const note = $("termination-note");
  const reasonText = TERMINATION_TEXT[status.terminationReason];
  note.textContent = reasonText || "";
  note.style.display = reasonText ? "" : "none";

  const dims = $("dims");
  dims.innerHTML = "";
  for (const [name, d] of Object.entries(report.dimensions || {})) {
    const row = document.createElement("details");
    row.className = "dim";
    const dScore = Math.max(0, Math.min(5, Number(d.score) || 0));
    const summary = document.createElement("summary");
    summary.innerHTML =
      '<span class="dim-name"></span><span class="dim-bar"><i></i></span>' +
      '<span class="dim-score"></span><span class="dim-conf"></span>';
    summary.querySelector(".dim-name").textContent = name;
    summary.querySelector(".dim-bar i").style.width = (dScore / 5) * 100 + "%";
    summary.querySelector(".dim-score").textContent = dScore.toFixed(1);
    summary.querySelector(".dim-conf").textContent = d.confidence || "";
    row.appendChild(summary);
    const body = document.createElement("div");
    body.className = "dim-body";
    const lists = [
      ["依据", d.evidence],
      ["关注点", d.concerns],
      ["建议", d.recommendations],
    ];
    for (const [label, items] of lists) {
      if (!items || !items.length) continue;
      const h = document.createElement("p");
      h.className = "dim-label";
      h.textContent = label;
      body.appendChild(h);
      const ul = document.createElement("ul");
      for (const item of items) {
        const li = document.createElement("li");
        li.textContent = item;
        ul.appendChild(li);
      }
      body.appendChild(ul);
    }
    row.appendChild(body);
    dims.appendChild(row);
  }

  fillList("strengths-list", report.strengths);
  fillList("weaknesses-list", report.weaknesses);
  fillList("recs-list", report.recommendations);

  const replay = $("replay-log");
  replay.innerHTML = "";
  S.questionCount = 0;
  renderTranscript(status.transcript || [], replay);
}

function fillList(id, items) {
  const ul = $(id);
  ul.innerHTML = "";
  for (const item of items || []) {
    const li = document.createElement("li");
    li.textContent = item;
    ul.appendChild(li);
  }
  if (!ul.children.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "（无）";
    ul.appendChild(li);
  }
}

/* ---------------- profile (prep view) ---------------- */

let profileDirty = false;

async function loadProfile() {
  try {
    const profile = await (await fetch("/api/interview/profile")).json();
    $("prep-role").value = profile.target_role || "";
    const parts = [];
    if (profile.jd_chars) parts.push("JD " + profile.jd_chars + " 字");
    if (profile.resume_chars) parts.push("简历 " + profile.resume_chars + " 字");
    $("profile-summary").textContent = parts.length
      ? "已有档案：" + parts.join("，") + " — 可直接开始，或在下方更新"
      : "还没有 JD 和简历，建议填写后开始（面试官会围绕它们提问）";
  } catch (err) { /* backend down; CTA 会再报错 */ }
}

async function saveProfileIfDirty() {
  const hasNewFile = $("prep-resume-file").files.length > 0;
  if (!profileDirty && !hasNewFile) return;
  const form = new FormData();
  form.append("target_role", $("prep-role").value);
  form.append("jd_text", $("prep-jd").value);
  form.append("resume_text", $("prep-resume-text").value);
  if (hasNewFile) form.append("resume_file", $("prep-resume-file").files[0]);
  const res = await fetch("/api/interview/profile", { method: "POST", body: form });
  const data = await res.json();
  if (!data.success) throw new Error(data.error || "档案保存失败");
  profileDirty = false;
  $("prep-jd").value = "";
  $("prep-resume-text").value = "";
  $("prep-resume-file").value = "";
  loadProfile();
}

/* ---------------- debug ---------------- */

function debugLog(line) {
  if (!DEBUG) return;
  const el = $("debug-log");
  el.textContent = (new Date()).toLocaleTimeString() + " " + line + "\n" + el.textContent.slice(0, 4000);
}

const FIXTURE_STATUS = {
  state: "completed",
  candidateMessage: "面试已完成，可以查看反馈报告。",
  progressPercent: 100,
  questionsCompleted: 3,
  totalQuestions: 3,
  terminationReason: null,
  transcript: [
    { turnId: "t1", role: "interviewer", type: "opening", text: "你好，我是林面试官。今天我们聊聊你和 Python 后端工程师这个方向的匹配度。" },
    { turnId: "t2", role: "interviewer", type: "main_question", text: "我们先从第一个问题开始。请介绍一个你最近主导的后端项目。" },
    { turnId: "t3", role: "candidate", type: "answer", text: "我最近主导了订单中台的重构，把峰值处理能力提升了三倍。" },
    { turnId: "t4", role: "interviewer", type: "answer_acknowledgement", text: "好，我听明白了，我先顺一下你刚才讲的。" },
    { turnId: "t5", role: "interviewer", type: "follow_up", text: "我想顺着这里多问一句。缓存失效风暴你们是怎么规避的？" },
    { turnId: "t6", role: "candidate", type: "answer", text: "我们用了随机过期时间加互斥重建，配合本地二级缓存。" },
    { turnId: "t7", role: "system", type: "question_skipped", text: "hard_timeout_no_answer", metadata: { reason: "hard_timeout_no_answer" } },
    { turnId: "t8", role: "interviewer", type: "closing", text: "今天的模拟面试就到这里，稍后你可以查看完整反馈报告。" },
  ],
  finalReport: {
    summary: "候选人具备扎实的后端工程能力，项目主导经验清晰，量化结果明确；在可靠性设计上有真实实践。",
    overallScore: 4,
    strengths: ["项目结果量化清晰", "缓存与一致性取舍讲得具体"],
    weaknesses: ["有一题未在时限内作答"],
    recommendations: ["继续补充故障排查的体系化方法论"],
    dimensions: {
      technical_depth: { score: 4, evidence: ["缓存互斥重建方案"], concerns: [], recommendations: ["深入分布式事务"], confidence: "high" },
      communication_clarity: { score: 4, evidence: ["回答结构清晰"], concerns: ["偶有跳跃"], recommendations: [], confidence: "medium" },
    },
  },
};

function loadFixture() {
  S.interviewStarted = true;
  S.interviewOver = false;
  renderedTurns.clear();
  $("chat-log").innerHTML = "";
  S.questionCount = 0;
  setView("interview");
  renderHeader(FIXTURE_STATUS);
  renderTranscript(FIXTURE_STATUS.transcript);
  gateComposer({ state: "listening" });
  toast("已载入示例对话（debug）");
}

function loadFixtureReport() {
  S.interviewOver = false;
  renderReport(FIXTURE_STATUS);
  setView("finished");
  S.interviewOver = true;
  toast("已载入示例报告（debug）");
}

/* ---------------- init & wiring ---------------- */

function init() {
  if (DEBUG) document.body.classList.add("debug");
  setView("prep");
  loadProfile();
  preheat();

  $("start-btn").onclick = onStartInterview;
  $("readiness").onclick = () => {
    if (S.preheat === "failed" || S.preheat === "released") preheat();
  };
  $("retry-btn").onclick = onStartInterview;
  $("back-to-prep").onclick = () => { setView("prep"); };
  for (const id of ["prep-role", "prep-jd", "prep-resume-text"]) {
    $(id).addEventListener("input", () => { profileDirty = true; });
  }
  $("prep-resume-file").addEventListener("change", () => { profileDirty = true; });

  $("mode-voice").onclick = () => applyInputMode("voice");
  $("mode-text").onclick = () => applyInputMode("text");
  $("mic-btn").onclick = () => setMic(!S.micOn);
  $("send-btn").onclick = sendTextAnswer;
  $("answer-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendTextAnswer();
    }
  });
  $("answer-input").addEventListener("input", () => gateComposer(S.lastStatus || { state: "idle" }));

  $("end-btn").onclick = onEndInterview;
  $("confirm-end").onclick = confirmEnd;
  $("cancel-end").onclick = () => $("confirm-modal").classList.remove("show");
  $("again-btn").onclick = () => { location.reload(); };

  if (DEBUG) {
    $("dbg-fixture").onclick = loadFixture;
    $("dbg-fixture-report").onclick = loadFixtureReport;
    $("dbg-toggle").onclick = () => $("debug-body").classList.toggle("show");
  }

  window.addEventListener("pagehide", () => {
    if (S.sessionReady || S.interviewStarted) {
      navigator.sendBeacon("/api/stop-session");
    }
  });
}

init();
