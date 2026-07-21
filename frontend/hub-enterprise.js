const $ = (id) => document.getElementById(id);
const labels = {pending:"待面试",in_progress:"进行中",completed:"已完成",expired:"已过期",revoked:"已撤销"};
let records = [];
let positions = [];
let candidates = [];
let editingPositionId = null;

function updateCreateGate() {
  $("create").disabled = !$("candidate").value || !$("avatar").value || !$("position").value;
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}
async function parseApiResponse(res) {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch (_error) {
    if (res.status === 404) {
      throw new Error("后台接口尚未加载（HTTP 404），请重启 Hub 服务后刷新页面");
    }
    throw new Error(`服务器返回了非 JSON 响应（HTTP ${res.status}）：${text.slice(0, 120) || "空响应"}`);
  }
}
function fmt(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {hour12:false});
}
function recordLinkCell(record) {
  if (record.invite_url) {
    const action = record.status === "pending"
      ? `<button onclick="copyRecordLink('${record.id}')">复制</button>`
      : '<span class="candidate-meta">已使用</span>';
    return `<div class="record-link"><code title="${escapeHtml(record.invite_url)}">${escapeHtml(record.invite_url)}</code>${action}</div>`;
  }
  if (record.status === "pending") {
    return `<button onclick="renewInviteLink('${record.id}')">生成链接</button>`;
  }
  return '<span class="report-waiting">链接已失效</span>';
}
async function loadRoster() {
  const roster = await (await fetch("/api/roster")).json();
  const avatars = (roster.avatars || []).filter(a => a.usage_type === "enterprise");
  $("avatar").innerHTML = avatars.map(a => `<option value="${escapeHtml(a.slug)}">${escapeHtml(a.name || a.slug)}</option>`).join("");
  updateCreateGate();
}
async function loadPositions() {
  try {
    const res = await fetch("/api/enterprise/positions");
    const data = await parseApiResponse(res);
    if (!res.ok || !data.success) throw new Error(data.error || "岗位加载失败");
    positions = data.positions || [];
    $("position-count").textContent = `${positions.length} 个`;
    $("position-empty").hidden = positions.length > 0;
    $("position").innerHTML = positions.length
      ? positions.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.title)}</option>`).join("")
      : '<option value="">请先创建岗位</option>';
    $("positions").innerHTML = positions.map(p => `
      <tr>
        <td><b>${escapeHtml(p.title)}</b></td>
        <td>${escapeHtml((p.jd || "").replace(/\s+/g, " ").slice(0, 120))}${(p.jd || "").length > 120 ? "…" : ""}</td>
        <td>${fmt(p.updated_at || p.created_at)}</td>
        <td><div class="actions"><button onclick="editPosition('${p.id}')">编辑</button><button class="danger" onclick="deletePosition('${p.id}')">删除</button></div></td>
      </tr>`).join("");
    renderPositionPreview();
    updateCreateGate();
  } catch (error) {
    $("position-status").textContent = error.message;
    positions = [];
    $("position").innerHTML = '<option value="">岗位服务不可用</option>';
    updateCreateGate();
  }
}
async function savePosition(event) {
  event.preventDefault();
  const title = $("position-title").value.trim();
  const jd = $("position-description").value.trim();
  const btn = $("position-save");
  btn.disabled = true;
  $("position-status").textContent = editingPositionId ? "正在更新岗位…" : "正在创建岗位…";
  try {
    const url = editingPositionId
      ? `/api/enterprise/positions/${editingPositionId}`
      : "/api/enterprise/positions";
    const res = await fetch(url, {
      method: editingPositionId ? "PUT" : "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({title, jd}),
    });
    const data = await parseApiResponse(res);
    if (!res.ok || !data.success) throw new Error(data.error || "岗位保存失败");
    const savedId = data.position.id;
    cancelPositionEdit();
    $("position-status").textContent = `已保存岗位：${data.position.title}`;
    await loadPositions();
    $("position").value = savedId;
    renderPositionPreview();
  } catch (error) {
    $("position-status").textContent = error.message;
  } finally {
    btn.disabled = false;
  }
}
function editPosition(id) {
  const position = positions.find(item => item.id === id);
  if (!position) return;
  editingPositionId = id;
  $("position-title").value = position.title || "";
  $("position-description").value = position.jd || "";
  $("position-save").textContent = "更新岗位";
  $("position-cancel").hidden = false;
  $("position-status").textContent = `正在编辑：${position.title}`;
  $("position-title").focus();
}
function cancelPositionEdit() {
  editingPositionId = null;
  $("position-form").reset();
  $("position-save").textContent = "保存岗位";
  $("position-cancel").hidden = true;
}
async function deletePosition(id) {
  if (!confirm("删除岗位主档？已创建的面试任务会保留创建时的岗位快照。")) return;
  try {
    const res = await fetch(`/api/enterprise/positions/${id}`, {method:"DELETE"});
    const data = await parseApiResponse(res);
    if (!res.ok || !data.success) throw new Error(data.error || "删除失败");
    if (editingPositionId === id) cancelPositionEdit();
    await loadPositions();
  } catch (error) {
    $("position-status").textContent = error.message;
  }
}
async function loadCandidates() {
  let data;
  try {
    const res = await fetch("/api/enterprise/candidates");
    data = await parseApiResponse(res);
    if (!res.ok || !data.success) throw new Error(data.error || "候选人加载失败");
  } catch (error) {
    $("candidate-status").textContent = error.message;
    candidates = [];
    $("candidate").innerHTML = '<option value="">候选人服务不可用</option>';
    updateCreateGate();
    return;
  }
  candidates = data.candidates || [];
  $("candidate-count").textContent = `${candidates.length} 人`;
  $("candidate-empty").hidden = candidates.length > 0;
  $("candidate").innerHTML = candidates.length
    ? candidates.map(c => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}${c.contact ? ` · ${escapeHtml(c.contact)}` : ""}</option>`).join("")
    : '<option value="">请先录入候选人</option>';
  $("candidates").innerHTML = candidates.map(c => `
    <tr>
      <td><b>${escapeHtml(c.name)}</b><br><span class="candidate-meta">${escapeHtml(c.contact || "未填写联系方式")}</span></td>
      <td>${escapeHtml(c.resume_filename || "粘贴文本")}<br><span class="candidate-meta">${c.resume_chars || 0} 字</span></td>
      <td>${escapeHtml(c.source || "—")}</td>
      <td>${fmt(c.created_at)}</td>
      <td><div class="actions"><button onclick="showCandidate('${c.id}')">查看</button><button class="danger" onclick="deleteCandidate('${c.id}')">删除</button></div></td>
    </tr>`).join("");
  updateCreateGate();
}
async function createCandidate(event) {
  event.preventDefault();
  const btn = $("candidate-save");
  btn.disabled = true;
  $("candidate-status").textContent = "正在解析并保存简历…";
  try {
    const form = new FormData();
    form.append("name", $("candidate-name").value);
    form.append("contact", $("candidate-contact").value);
    form.append("source", $("candidate-source").value);
    form.append("resume_text", $("candidate-resume-text").value);
    if ($("candidate-resume-file").files[0]) {
      form.append("resume_file", $("candidate-resume-file").files[0]);
    }
    const res = await fetch("/api/enterprise/candidates", {method:"POST", body:form});
    const data = await parseApiResponse(res);
    if (!res.ok || !data.success) throw new Error(data.error || "保存失败");
    $("candidate-form").reset();
    $("candidate-status").textContent = `已保存 ${data.candidate.name}`;
    await loadCandidates();
    $("candidate").value = data.candidate.id;
    updateCreateGate();
  } catch (error) {
    $("candidate-status").textContent = error.message;
  } finally {
    btn.disabled = false;
  }
}
async function showCandidate(id) {
  const res = await fetch(`/api/enterprise/candidates/${id}`);
  const data = await parseApiResponse(res);
  if (!data.success) return alert(data.error || "候选人不存在");
  const c = data.candidate;
  $("detail").textContent = JSON.stringify({
    姓名:c.name,
    联系方式:c.contact,
    来源:c.source,
    简历文件:c.resume_filename || "粘贴文本",
    简历正文:c.resume_text,
    创建时间:c.created_at,
  }, null, 2);
  $("detail-dialog").showModal();
}
async function deleteCandidate(id) {
  if (!confirm("删除候选人主档？已创建的面试任务会保留当时的候选人快照。")) return;
  const res = await fetch(`/api/enterprise/candidates/${id}`, {method:"DELETE"});
  const data = await parseApiResponse(res);
  if (!res.ok || !data.success) return alert(data.error || "删除失败");
  await loadCandidates();
}
function renderPositionPreview() {
  const selected = positions.find(p => p.id === $("position").value);
  $("position-jd").textContent = selected?.jd || "暂无岗位 JD";
}
async function loadRecords() {
  const data = await (await fetch("/api/enterprise/interviews")).json();
  records = data.records || [];
  $("empty").hidden = records.length > 0;
  $("records").innerHTML = records.map(r => `
    <tr>
      <td><b>${escapeHtml(r.candidate_name || "未填写")}</b><br><span>${escapeHtml(r.target_role || "岗位待填写")}</span></td>
      <td>${escapeHtml(r.avatar_snapshot?.name || r.avatar_slug)}</td>
      <td><span class="badge">${labels[r.status] || r.status}</span></td>
      <td>${recordLinkCell(r)}</td>
      <td>${fmt(r.created_at)}<br><span>至 ${fmt(r.expires_at)}</span></td>
      <td><div class="actions">
        <button onclick="showDetail('${r.id}')">详情</button>
        ${r.status === "completed" ? `<button class="report-ready" onclick="showReport('${r.id}')">查看报告</button>` : '<span class="report-waiting">报告待生成</span>'}
        ${["pending","in_progress"].includes(r.status) ? `<button class="danger" onclick="revokeRecord('${r.id}')">撤销</button>` : ""}
        <button class="danger" onclick="deleteRecord('${r.id}')">删除</button>
      </div></td>
    </tr>`).join("");
}
async function createInvite() {
  const res = await fetch("/api/enterprise/invites", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({candidate_id:$("candidate").value,avatar_slug:$("avatar").value,position_id:$("position").value,expires_days:Number($("days").value)})});
  const data = await parseApiResponse(res);
  if (!res.ok || !data.success) return alert(data.error || "创建失败");
  $("invite-url").textContent = data.invite_url;
  $("invite").style.display = "block";
  await loadRecords();
}
async function showDetail(id) {
  const data = await (await fetch(`/api/enterprise/interviews/${id}`)).json();
  const r = data.record;
  $("detail-title").textContent = "招聘记录";
  $("detail").textContent = JSON.stringify({
    基本信息:{候选人:r.candidate_name,联系方式:r.candidate_contact,岗位:r.target_role,状态:labels[r.status]||r.status},
    候选人快照:r.candidate_snapshot,
    岗位JD:r.jd_text,
    候选人画像:r.candidate_brief,
    招聘报告:r.report,
    面试逐字稿:r.transcript,
  }, null, 2);
  $("detail-dialog").showModal();
}
async function showReport(id) {
  const data = await (await fetch(`/api/enterprise/interviews/${id}`)).json();
  if (!data.success) return alert(data.error || "报告加载失败");
  const r = data.record;
  $("detail-title").textContent = `${r.candidate_name || "候选人"} · 面试报告`;
  $("detail").textContent = JSON.stringify({
    候选人:r.candidate_name,
    应聘岗位:r.target_role,
    面试状态:labels[r.status] || r.status,
    招聘结论:r.report?.recommendation || "待生成",
    岗位匹配分:r.report?.role_match_score,
    总结:r.report?.conclusion,
    优势:r.report?.strengths,
    风险:r.report?.risks,
    待核验项:r.report?.verification_points,
    建议复试问题:r.report?.suggested_second_round_questions,
    逐题评价:r.report?.question_assessments,
  }, null, 2);
  $("detail-dialog").showModal();
}
async function copyRecordLink(id) {
  const record = records.find(item => item.id === id);
  if (!record?.invite_url) return;
  await navigator.clipboard.writeText(record.invite_url);
}
async function renewInviteLink(id) {
  const res = await fetch(`/api/enterprise/interviews/${id}/renew-link`, {method:"POST"});
  const data = await parseApiResponse(res);
  if (!res.ok || !data.success) return alert(data.error || "生成链接失败");
  await navigator.clipboard.writeText(data.invite_url);
  $("invite-url").textContent = data.invite_url;
  $("invite").style.display = "block";
  await loadRecords();
}
async function revokeRecord(id) {
  if (!confirm("撤销后候选人将不能继续面试，确认撤销？")) return;
  await fetch(`/api/enterprise/interviews/${id}/revoke`, {method:"POST"});
  loadRecords();
}
async function deleteRecord(id) {
  if (!confirm("永久删除该记录及候选人数据？")) return;
  await fetch(`/api/enterprise/interviews/${id}`, {method:"DELETE"});
  loadRecords();
}
$("create").onclick = createInvite;
$("candidate-form").onsubmit = createCandidate;
$("position-form").onsubmit = savePosition;
$("position-cancel").onclick = cancelPositionEdit;
$("position").onchange = renderPositionPreview;
$("refresh").onclick = loadRecords;
$("copy").onclick = async () => { await navigator.clipboard.writeText($("invite-url").textContent); $("copy").textContent = "已复制"; };
loadRoster();
loadPositions();
loadCandidates();
loadRecords();
