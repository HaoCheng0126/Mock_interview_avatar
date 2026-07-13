/* Interview config page logic — loaded by hub-interview.html via /interview-config.js */

const LIST_FIELDS = ["answer_acknowledgements", "final_answer_acknowledgements", "follow_up_prefixes"];
const SPEECH_TEXT_FIELDS = ["opening_template", "first_question_transition", "next_question_transition", "skip_transition", "closing", "termination"];
const WORKFLOW_FIELDS = [
  "hard_timeout_seconds", "candidate_speech_grace_seconds",
  "max_skipped_questions", "max_consecutive_skipped_questions",
  "opening_to_question_delay_seconds", "prompt_playback_timeout_seconds",
  "evaluation_join_timeout_seconds",
];
const PROMPT_FIELDS = ["system", "evaluator", "follow_up_decider", "report"];

let DEFAULT_PROMPTS = {};

function $(id) { return document.getElementById(id); }

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
}

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

/* ---------------- knowledge entries ---------------- */

function knowledgeCard(entry = { title: "", content: "", enabled: true }) {
  const card = document.createElement("div");
  card.className = "k-entry";
  card.innerHTML = `
    <div class="k-head">
      <input class="k-title" placeholder="资料标题（如：岗位 JD / 候选人简历）">
      <label class="k-toggle"><input type="checkbox" class="k-enabled"> 启用</label>
      <button class="small danger-btn" type="button">删除</button>
    </div>
    <textarea class="k-content" rows="6" placeholder="粘贴资料内容…"></textarea>
    <div class="k-count note"></div>`;
  card.querySelector(".k-title").value = entry.title || "";
  card.querySelector(".k-enabled").checked = entry.enabled !== false;
  card.querySelector(".k-content").value = entry.content || "";
  card.querySelector("button").onclick = () => { card.remove(); updateKnowledgeStats(); schedulePreview(); };
  card.querySelector(".k-content").addEventListener("input", updateKnowledgeStats);
  return card;
}

function addKnowledgeEntry(entry) {
  $("knowledge-list").appendChild(knowledgeCard(entry));
  updateKnowledgeStats();
}

function collectKnowledge() {
  const entries = [...document.querySelectorAll("#knowledge-list .k-entry")].map(card => ({
    title: card.querySelector(".k-title").value.trim(),
    content: card.querySelector(".k-content").value.trim(),
    enabled: card.querySelector(".k-enabled").checked,
  })).filter(e => e.title || e.content);
  return { max_chars: Number($("k-max-chars").value) || 6000, entries };
}

function updateKnowledgeStats() {
  const budget = Number($("k-max-chars").value) || 6000;
  let total = 0;
  for (const card of document.querySelectorAll("#knowledge-list .k-entry")) {
    const len = card.querySelector(".k-content").value.length;
    const enabled = card.querySelector(".k-enabled").checked;
    card.querySelector(".k-count").textContent = `${len} 字${enabled ? "" : "（未启用，不注入）"}`;
    if (enabled) total += len;
  }
  const el = $("k-total");
  el.textContent = `启用资料共 ${total} 字 / 预算 ${budget} 字${total > budget ? " — 超出部分将被截断" : ""}`;
  el.style.color = total > budget ? "var(--yellow)" : "var(--text-dim)";
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
  const res = await fetch("/api/interview-config");
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

  $("f-role").value = cfg.candidate?.target_role ?? "";
  $("f-background").value = cfg.candidate?.background ?? "";

  for (const key of WORKFLOW_FIELDS) $(`w-${key}`).value = cfg.workflow?.[key] ?? "";
  for (const key of SPEECH_TEXT_FIELDS) $(`s-${key}`).value = cfg.speech?.[key] ?? "";
  for (const key of LIST_FIELDS) $(`s-${key}`).value = (cfg.speech?.[key] ?? []).join("\n");
  for (const key of PROMPT_FIELDS) $(`p-${key}`).value = cfg.prompts?.[key] ?? "";

  $("thinking-checks").innerHTML = "";
  for (const check of cfg.speech?.thinking_checks ?? []) addCheckRow(check.after_seconds, check.text);

  $("knowledge-list").innerHTML = "";
  $("k-max-chars").value = cfg.knowledge?.max_chars ?? 6000;
  for (const entry of cfg.knowledge?.entries ?? []) addKnowledgeEntry(entry);

  $("q-yaml").value = cfg.questions_yaml ?? "";

  updatePromptBadges();
  updateKnowledgeStats();
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
    candidate: {
      target_role: $("f-role").value,
      background: $("f-background").value,
    },
    prompts, speech, workflow,
    knowledge: collectKnowledge(),
    questions_yaml: $("q-yaml").value,
  };
}

async function saveAll() {
  $("save-btn").disabled = true;
  try {
    const res = await fetch("/api/interview-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectAll()),
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || "HTTP " + res.status);
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
      $("preview-opening").textContent = "";
      return;
    }
    $("preview-system").classList.remove("error");
    $("preview-system").textContent = data.system_prompt;
    $("preview-opening").textContent = data.opening_text;
    $("preview-status").textContent = "已同步 — 这就是 LLM 实际收到的内容";
  } catch {
    $("preview-status").textContent = "预览暂不可用";
  }
}

/* ---------------- init ---------------- */

document.addEventListener("input", (event) => {
  if (event.target.closest("#pane-questions, #pane-settings, #pane-knowledge, #pane-speech, #pane-prompts")) {
    schedulePreview();
    if (event.target.closest("#pane-prompts")) updatePromptBadges();
    if (event.target.id === "k-max-chars") updateKnowledgeStats();
  }
});

loadAll();
