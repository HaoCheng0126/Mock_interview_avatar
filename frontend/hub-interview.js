/* Interview config page logic — loaded by hub-interview.html via /interview-config.js */

const LIST_FIELDS = ["follow_up_prefixes", "next_question_transitions"];
const SPEECH_TEXT_FIELDS = ["self_intro_prompt", "prep_template", "opening_template", "first_question_transition", "skip_transition", "closing", "termination"];
const WORKFLOW_FIELDS = [
  "hard_timeout_seconds", "candidate_speech_grace_seconds",
  "max_skipped_questions", "max_consecutive_skipped_questions",
  "opening_to_question_delay_seconds", "prompt_playback_timeout_seconds",
  "evaluation_join_timeout_seconds", "foreground_evaluation_timeout_seconds",
];
const PROMPT_FIELDS = ["system", "evaluator", "follow_up_decider", "planner", "closing_comment"];
const PLAN_FIELDS = [
  "resume_experiences", "business_questions",
  "resume_followups", "business_followups",
  "self_intro_followups", "self_intro_followups_no_resume",
];

let DEFAULT_PROMPTS = {};

function $(id) { return document.getElementById(id); }

// Which avatar's interview.yaml this page edits (?avatar=<slug>; default when absent).
const AVATAR = new URLSearchParams(location.search).get("avatar") || "";
function configUrl() {
  return "/api/interview-config" + (AVATAR ? "?avatar=" + encodeURIComponent(AVATAR) : "");
}

let currentRoster = null;
let currentUsageType = "practice";
let currentAvatarPlatform = null;

function currentSlug() {
  return AVATAR || (currentRoster?.avatars?.[0]?.slug) || "";
}

// Load the roster: fill the switcher, this avatar's 形象/声音 meta, and the 试面 link.
async function loadAvatarSwitcher() {
  try { currentRoster = await (await fetch("/api/roster")).json(); }
  catch { return; }
  const sel = $("avatar-switcher");
  sel.innerHTML = "";
  for (const a of currentRoster.avatars || []) {
    const opt = document.createElement("option");
    opt.value = a.slug;
    opt.textContent = a.name || a.slug;
    sel.appendChild(opt);
  }
  const slug = currentSlug();
  sel.value = slug;
  const entry = (currentRoster.avatars || []).find((a) => a.slug === slug) || {};
  currentUsageType = entry.usage_type || "practice";
  $("m-name").value = entry.name || "";
  $("m-direction").value = entry.direction || "";
  $("m-avatar_id").value = entry.avatar_id || "";
  $("m-voice_id").value = entry.voice_id || "";
  $("m-voice_speed").value = entry.voice_speed || "";
  $("m-poster_url").value = entry.poster_url || "";
  $("m-usage-type").textContent = currentUsageType === "enterprise"
    ? "企业招聘面试（创建后不可与 C 端训练混用）"
    : "C 端模拟面试";
  $("company-knowledge-tab").hidden = currentUsageType !== "enterprise";
  await Promise.all([loadTryLink(slug), loadAvatarPlatform(slug)]);
}

function avatarPlatformUrl(path = "") {
  const slug = currentSlug();
  return `/api/interview-platform${path}?avatar=${encodeURIComponent(slug)}`;
}

async function loadAvatarPlatform(slug) {
  const summary = $("platform-global-summary");
  try {
    const res = await fetch(`/api/interview-platform?avatar=${encodeURIComponent(slug)}`);
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);
    currentAvatarPlatform = data;
    $("platform-use-global").checked = data.use_global !== false;
    $("platform-use-custom").checked = data.use_global === false;
    $("platform-custom-api-key").value = data.custom?.api_key || "";
    $("platform-custom-base-url").value = data.custom?.base_url || data.global?.base_url || "";
    $("platform-custom-sandbox").value = data.custom?.sandbox || "";
    const keyStatus = data.global?.configured
      ? `API Key ${data.global.api_key || "已配置"}`
      : "API Key 未配置";
    const environment = data.global?.sandbox ? "沙箱环境" : "正式环境";
    summary.textContent = `全局配置：${keyStatus} · ${data.global?.base_url || "平台地址未配置"} · ${environment}`;
    summary.classList.remove("error");
    onAvatarPlatformModeChange();
  } catch (error) {
    currentAvatarPlatform = null;
    summary.textContent = `全局平台配置读取失败：${error.message}`;
    summary.classList.add("error");
  }
}

function onAvatarPlatformModeChange() {
  const useGlobal = $("platform-use-global").checked;
  $("platform-global-summary").hidden = !useGlobal;
  $("platform-custom-fields").hidden = useGlobal;
  if (!useGlobal && !$("platform-custom-base-url").value.trim()) {
    $("platform-custom-base-url").value = currentAvatarPlatform?.global?.base_url || "";
    $("platform-custom-sandbox").value = currentAvatarPlatform?.global?.sandbox || "";
  }
  const out = $("avatar-test-result");
  out.textContent = "";
  out.className = "test-result";
}

function collectAvatarPlatform() {
  const useGlobal = $("platform-use-global").checked;
  return {
    use_global: useGlobal,
    api_key: useGlobal ? "" : $("platform-custom-api-key").value.trim(),
    base_url: useGlobal ? "" : $("platform-custom-base-url").value.trim(),
    sandbox: useGlobal ? "" : $("platform-custom-sandbox").value,
  };
}

function validateAvatarPlatform(platform) {
  if (platform.use_global) return;
  if (!platform.api_key) throw new Error("使用独立平台配置时，请填写 API Key");
  if (!platform.base_url) throw new Error("使用独立平台配置时，请填写平台地址");
}

async function saveAvatarPlatform() {
  const platform = collectAvatarPlatform();
  validateAvatarPlatform(platform);
  const res = await fetch(avatarPlatformUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(platform),
  });
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);
  currentAvatarPlatform = data;
}

async function loadTryLink(slug) {
  try {
    const agents = await (await fetch("/api/agents")).json();
    const iv = agents.find((a) => a.name === "interview");
    const link = $("try-link");
    if (currentUsageType === "enterprise") {
      link.removeAttribute("href");
      link.textContent = "仅邀请进入";
      link.classList.add("disabled");
    } else if (iv && iv.url && (iv.status === "running" || iv.status === "external")) {
      link.href = `${iv.url}/?avatar=${encodeURIComponent(slug)}`;
      link.classList.remove("disabled");
    } else {
      link.removeAttribute("href");
      link.classList.add("disabled");
    }
  } catch { /* ignore */ }
}

// Persist this avatar's 形象/声音/显示名 back into the roster (separate file from the
// interview.yaml content, so it's a second save target behind the one 保存配置 button).
async function saveMeta() {
  if (!currentRoster) return;
  const slug = currentSlug();
  const avatars = (currentRoster.avatars || []).map((a) =>
    a.slug === slug
      ? {
          ...a,
          name: $("m-name").value.trim(),
          direction: $("m-direction").value.trim(),
          avatar_id: $("m-avatar_id").value.trim(),
          voice_id: $("m-voice_id").value.trim(),
          voice_speed: $("m-voice_speed").value.trim(),
          poster_url: $("m-poster_url").value.trim(),
        }
      : a
  );
  const res = await fetch("/api/roster", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...currentRoster, avatars }),
  });
  const data = await res.json();
  if (data.success) currentRoster = data.roster;
}

// Test the platform connection for THIS avatar with the currently selected
// global or independent credentials. The test does not persist form changes.
async function testAvatar() {
  const btn = $("avatar-test-btn");
  const out = $("avatar-test-result");
  const avatarId = $("m-avatar_id").value.trim();
  if (!avatarId) {
    out.textContent = "请先填写 Avatar ID";
    out.className = "test-result err";
    return;
  }
  btn.disabled = true;
  out.textContent = "测试中…";
  out.className = "test-result";
  try {
    const platform = collectAvatarPlatform();
    validateAvatarPlatform(platform);
    const res = await fetch(avatarPlatformUrl("/test"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...platform,
        avatar_id: avatarId,
        voice_id: $("m-voice_id").value.trim(),
      }),
    });
    const data = await res.json();
    if (data.success) {
      out.textContent = "✅ 连接成功（会话可创建，已自动释放）";
      out.className = "test-result ok";
    } else {
      out.textContent = "❌ " + (data.error || "连接失败");
      out.className = "test-result err";
    }
  } catch (e) {
    out.textContent = "❌ " + e.message;
    out.className = "test-result err";
  } finally {
    btn.disabled = false;
  }
}

function switchAvatar() {
  location.search = "?avatar=" + encodeURIComponent($("avatar-switcher").value);
}

function toast(msg, ok = true) {
  const el = $("toast");
  el.textContent = msg;
  el.className = ok ? "show ok" : "show err";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ""; }, ok ? 2500 : 8000);
}

/* ---------------- tabs ---------------- */

function showTab(name) {
  for (const btn of document.querySelectorAll("#tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === name);
  }
  for (const pane of document.querySelectorAll(".pane")) {
    pane.classList.toggle("active", pane.id === `pane-${name}`);
  }
  // 如果切换到日志标签页，自动刷新
  if (name === "logs") {
    refreshLogs();
  }
}

// The opening line is only previewed on the settings tab; its editable field lives on
// the prompts tab. Jump there, focus it, and flash it so it's obvious what to edit.
function goEditOpening() {
  showTab("prompts");
  const el = $("s-opening_template");
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.focus();
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1600);
}
window.goEditOpening = goEditOpening;

/* ---------------- thinking checks ---------------- */

function addCheckRow(seconds = "", text = "") {
  const row = document.createElement("div");
  row.className = "check-row field";
  row.innerHTML = `
    <input class="secs" type="number" min="1" placeholder="秒" value="${seconds}">
    <input class="txt" placeholder="提醒话术">
    <button class="small" type="button">删除</button>`;
  row.querySelector(".txt").value = text;
  row.querySelector("button").onclick = () => { row.remove(); schedulePreview(); };
  $("thinking-checks").appendChild(row);
}

/* ---------------- positions (岗位与题库) ---------------- */

function rowDelete(row) {
  row.querySelector(".row-del").onclick = () => { row.remove(); schedulePreview(); };
}

function questionRow(question = "") {
  const prompt = typeof question === "string" ? question : (question?.prompt || "");
  const competency = typeof question === "string" ? "" : (question?.competency || "");
  const row = document.createElement("div");
  row.className = "pos-row";
  row.innerHTML = `
    <input class="q-prompt" placeholder="业务题题干（只需题目）">
    <input class="q-competency" placeholder="本题考察点（不向候选人口播）">
    <button class="small danger-btn row-del" type="button">删</button>`;
  row.querySelector(".q-prompt").value = prompt || "";
  row.querySelector(".q-competency").value = competency || "";
  rowDelete(row);
  return row;
}

function positionCard(pos = {}) {
  const card = document.createElement("div");
  card.className = "pos-card";
  card.innerHTML = `
    <div class="pos-head">
      <input class="pos-name" placeholder="岗位名称，如 Python 后端工程师">
      <button class="small danger-btn pos-del" type="button">删除岗位</button>
    </div>
    <div class="field"><label>匹配关键词（逗号分隔，按候选人上传的 JD 匹配到此岗位）</label>
      <input class="pos-keywords" placeholder="python, 后端, 高并发"></div>
    <div class="pos-sub">业务题库 — 题干会口播；考察点只用于出题、追问和评分</div>
    <div class="pos-questions"></div>
    <div><button class="small pos-add-q" type="button">＋ 添加业务题</button></div>
    <div class="field"><label>核心考察点 / 岗位要求（一整段话，驱动出题、追问与评分）</label>
      <textarea class="pos-comp" rows="4" placeholder="用一段话描述这个岗位重点考察什么、什么样的回答算好…"></textarea></div>`;
  card.querySelector(".pos-name").value = pos.name || "";
  card.querySelector(".pos-keywords").value = (pos.match_keywords || []).join(", ");
  const questions = card.querySelector(".pos-questions");
  for (const q of pos.business_questions || []) questions.appendChild(questionRow(q));
  card.querySelector(".pos-comp").value = pos.core_competencies || "";
  card.querySelector(".pos-del").onclick = () => { card.remove(); schedulePreview(); };
  card.querySelector(".pos-add-q").onclick = () => { questions.appendChild(questionRow()); };
  return card;
}

/* ---------------- enterprise company knowledge ---------------- */

const KNOWLEDGE_CATEGORIES = [
  ["company_overview", "公司介绍"], ["products_business", "产品与业务"],
  ["customers_market", "客户与市场"], ["culture_values", "文化价值观"],
  ["business_scenarios", "业务场景"], ["tech_stack", "技术栈"], ["other", "其他"],
];

function companyKnowledgeCard(item = {}) {
  const card = document.createElement("div");
  card.className = "card";
  card.style.marginBottom = "12px";
  card.dataset.id = item.id || `knowledge-${crypto.randomUUID?.() || Date.now()}`;
  card.innerHTML = `
    <div class="card-body">
      <div class="row3">
        <div class="field"><label>标题</label><input class="ck-title" maxlength="120"></div>
        <div class="field"><label>分类</label><select class="ck-category">${KNOWLEDGE_CATEGORIES.map(([v,l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
        <div class="field"><label>用途</label><select class="ck-visibility"><option value="interview">可用于面试</option><option value="internal">仅内部评估</option></select></div>
      </div>
      <div class="field"><label>正文</label><textarea class="ck-content" rows="6" maxlength="4000" placeholder="输入真实、可核验的公司背景…"></textarea></div>
      <div style="display:flex;gap:12px;align-items:center"><label><input class="ck-enabled" type="checkbox"> 启用</label><button class="small danger-btn ck-delete" type="button">删除</button></div>
    </div>`;
  card.querySelector(".ck-title").value = item.title || "";
  card.querySelector(".ck-category").value = item.category || "other";
  card.querySelector(".ck-visibility").value = item.visibility || "interview";
  card.querySelector(".ck-content").value = item.content || "";
  card.querySelector(".ck-enabled").checked = item.enabled !== false;
  card.querySelector(".ck-delete").onclick = () => card.remove();
  return card;
}

function renderCompanyKnowledge(entries = []) {
  const wrap = $("company-knowledge-list");
  wrap.innerHTML = "";
  entries.forEach(item => wrap.appendChild(companyKnowledgeCard(item)));
}

function addCompanyKnowledge() {
  const wrap = $("company-knowledge-list");
  if (wrap.children.length >= 30) return toast("公司知识库最多 30 条", false);
  wrap.appendChild(companyKnowledgeCard());
}

function collectCompanyKnowledge() {
  return {
    max_interview_chars: 4000,
    max_internal_chars: 3000,
    entries: [...document.querySelectorAll("#company-knowledge-list > .card")].map(card => ({
      id: card.dataset.id,
      title: card.querySelector(".ck-title").value.trim(),
      category: card.querySelector(".ck-category").value,
      content: card.querySelector(".ck-content").value.trim(),
      visibility: card.querySelector(".ck-visibility").value,
      enabled: card.querySelector(".ck-enabled").checked,
    })).filter(item => item.title || item.content),
  };
}

function renderPositions(list) {
  const root = $("positions-list");
  root.innerHTML = "";
  const positions = list && list.length ? list : [{}];  // start with one empty card
  for (const pos of positions) root.appendChild(positionCard(pos));
}

function addPosition() {
  $("positions-list").appendChild(positionCard());
}

function collectPositions() {
  return [...document.querySelectorAll("#positions-list .pos-card")].map((card) => {
    const keywords = card.querySelector(".pos-keywords").value
      .split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
    const business_questions = [...card.querySelectorAll(".pos-questions .pos-row")]
      .map((row) => {
        const prompt = row.querySelector(".q-prompt").value.trim();
        const competency = row.querySelector(".q-competency").value.trim();
        return competency ? { prompt, competency } : prompt;
      })
      .filter((item) => typeof item === "string" ? item : item.prompt);
    return {
      name: card.querySelector(".pos-name").value.trim(),
      match_keywords: keywords,
      business_questions,
      core_competencies: card.querySelector(".pos-comp").value.trim(),
    };
  }).filter((p) => p.name || p.core_competencies.length || p.business_questions.length);
}

/* ---------------- prompt templates ---------------- */

function updatePromptBadges() {
  for (const key of PROMPT_FIELDS) {
    const custom = $(`p-${key}`).value.trim() !== (DEFAULT_PROMPTS[key] || "").trim();
    const badge = $(`badge-${key}`);
    badge.textContent = custom ? "已自定义" : "默认";
    badge.className = "badge " + (custom ? "custom" : "");
    $(`restore-${key}`).style.visibility = custom ? "visible" : "hidden";
  }
}

function restoreDefault(key) {
  $(`p-${key}`).value = DEFAULT_PROMPTS[key] || "";
  updatePromptBadges();
  schedulePreview();
  toast(`已恢复「${key}」为默认模板`);
}

/* ---------------- load / collect / save ---------------- */

async function loadAll() {
  const res = await fetch(configUrl());
  if (!res.ok) { toast("加载失败：HTTP " + res.status, false); return; }
  const cfg = await res.json();
  DEFAULT_PROMPTS = cfg.defaults?.prompts || {};

  $("f-title").value = cfg.interview?.title ?? "";
  $("f-lang").value = cfg.interview?.lang ?? "zh";
  $("f-duration").value = cfg.interview?.duration_minutes ?? 20;
  $("f-difficulty").value = cfg.interview?.difficulty ?? "mid";
  $("f-maxprobe").value = cfg.interview?.max_probe_per_question ?? 2;

  $("f-iname").value = cfg.interviewer?.name ?? "";
  $("f-istyle").value = cfg.interviewer?.style ?? "";
  $("f-irules").value = (cfg.interviewer?.rules ?? []).join("\n");

  for (const key of WORKFLOW_FIELDS) $(`w-${key}`).value = cfg.workflow?.[key] ?? "";
  for (const key of PLAN_FIELDS) $(`pl-${key}`).value = cfg.plan?.[key] ?? "";
  for (const key of SPEECH_TEXT_FIELDS) $(`s-${key}`).value = cfg.speech?.[key] ?? "";
  for (const key of LIST_FIELDS) $(`s-${key}`).value = (cfg.speech?.[key] ?? []).join("\n");
  for (const key of PROMPT_FIELDS) $(`p-${key}`).value = cfg.prompts?.[key] ?? "";

  $("thinking-checks").innerHTML = "";
  for (const check of cfg.speech?.thinking_checks ?? []) addCheckRow(check.after_seconds, check.text);

  renderPositions(cfg.positions ?? []);
  renderCompanyKnowledge(cfg.company_knowledge?.entries ?? []);

  updatePromptBadges();
  await refreshPreview();
}

function collectAll() {
  const lines = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  const speech = {};
  for (const key of SPEECH_TEXT_FIELDS) speech[key] = $(`s-${key}`).value;
  for (const key of LIST_FIELDS) speech[key] = lines(`s-${key}`);
  speech.thinking_checks = [...document.querySelectorAll("#thinking-checks .check-row")]
    .map(row => ({
      after_seconds: Number(row.querySelector(".secs").value),
      text: row.querySelector(".txt").value.trim(),
    }))
    .filter(check => check.after_seconds > 0 && check.text);

  const workflow = {};
  for (const key of WORKFLOW_FIELDS) workflow[key] = $(`w-${key}`).value;
  const plan = {};
  for (const key of PLAN_FIELDS) plan[key] = $(`pl-${key}`).value;
  const prompts = {};
  for (const key of PROMPT_FIELDS) prompts[key] = $(`p-${key}`).value;

  return {
    interview: {
      title: $("f-title").value,
      lang: $("f-lang").value,
      duration_minutes: Number($("f-duration").value) || 20,
      difficulty: $("f-difficulty").value,
      max_probe_per_question: Number($("f-maxprobe").value) || 2,
    },
    interviewer: {
      name: $("f-iname").value,
      style: $("f-istyle").value,
      rules: lines("f-irules"),
    },
    prompts, speech, workflow, plan,
    positions: collectPositions(),
    company_knowledge: currentUsageType === "enterprise"
      ? collectCompanyKnowledge()
      : undefined,
  };
}

async function saveAll() {
  $("save-btn").disabled = true;
  try {
    validateAvatarPlatform(collectAvatarPlatform());
    const res = await fetch(configUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectAll()),
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "HTTP " + res.status);
    await saveAvatarPlatform();
    await saveMeta();   // 形象/声音/显示名 → roster
    toast("✅ 已保存 — 下一场面试生效");
    await loadAll();
  } catch (e) {
    toast("保存失败：" + e.message, false);
  } finally {
    $("save-btn").disabled = false;
  }
}

/* ---------------- live preview ---------------- */

let previewTimer = null;

function schedulePreview() {
  $("preview-status").textContent = "预览刷新中…";
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 600);
}

async function refreshPreview() {
  try {
    const res = await fetch("/api/interview-config/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectAll()),
    });
    const data = await res.json();
    if (!data.success) {
      $("preview-status").textContent = "";
      $("preview-system").textContent = "⚠ 配置有误：" + (data.error || "未知错误");
      $("preview-system").classList.add("error");
      return;
    }
    $("preview-system").classList.remove("error");
    $("preview-system").textContent = data.full_preview;
    $("preview-status").textContent = "已同步 — 这是当前所有提示词模板的汇总预览";
  } catch {
    $("preview-status").textContent = "预览暂不可用";
  }
}

/* ---------------- init ---------------- */

/* ---------------- logs ---------------- */
let autoRefreshInterval = null;
let autoRefreshEnabled = false;

async function refreshLogs() {
  try {
    const res = await fetch("/api/agents/logs?name=interview");
    const data = await res.json();
    if (data.success) {
      const logsDisplay = $("logs-display");
      if (data.lines && data.lines.length > 0) {
        logsDisplay.textContent = data.lines.join("\n");
        // 自动滚动到底部
        logsDisplay.scrollTop = logsDisplay.scrollHeight;
      } else {
        logsDisplay.textContent = "暂无日志";
      }
    }
  } catch (e) {
    $("logs-display").textContent = "加载日志失败：" + e.message;
  }
}

function toggleAutoRefresh() {
  autoRefreshEnabled = !autoRefreshEnabled;
  const btn = $("auto-refresh-btn");
  if (autoRefreshEnabled) {
    btn.textContent = "⏹️ 停止自动刷新";
    refreshLogs();
    autoRefreshInterval = setInterval(refreshLogs, 2000);
  } else {
    btn.textContent = "⏱️ 自动刷新";
    if (autoRefreshInterval) {
      clearInterval(autoRefreshInterval);
      autoRefreshInterval = null;
    }
  }
}

/* ---------------- input events ---------------- */
document.addEventListener("input", (event) => {
  if (event.target.closest("#pane-positions, #pane-settings, #pane-speech, #pane-prompts")) {
    schedulePreview();
    if (event.target.closest("#pane-prompts")) updatePromptBadges();
  }
});

loadAvatarSwitcher();
loadAll();
