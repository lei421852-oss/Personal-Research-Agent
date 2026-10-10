/* 中文工作台只呈现服务端公开事实；研究决策、预算和取消由后端负责。 */
"use strict";

const API = "/api/research";
const STORAGE_KEY = "personal-agent-workbench-run-id";
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const NODE_TYPES = ["Planner", "Worker", "Validator", "Reviewer", "Recovery", "Synthesis"];
const STATUS_LABELS = {pending:"等待记录",queued:"排队中",running:"运行中",completed:"已完成",failed:"执行失败",cancelled:"已取消",skipped:"已跳过",partial_success:"部分完成",insufficient_evidence:"证据不足",execution_failed:"执行失败",verified:"已审核",review_rejected:"审核拒绝",review_unavailable:"审核不可用",generation_degraded:"降级生成",supported:"有证据支持",passed:"审核通过",rejected:"审核拒绝",cancel_requested:"正在取消"};
const SOURCE_LABELS = {live:"真实调用活动",derived:"执行记录推导",replay:"历史回放"};
const NODE_LABELS = {Planner:"研究规划",Worker:"论文研究",Validator:"引用结构验证",Reviewer:"论断审核",Recovery:"定向恢复",Synthesis:"综合生成"};
const DETAIL_LABELS = {paper_id:"论文",paper_ids:"论文范围",dimension_id:"研究维度",dimension:"研究维度",dimensions:"研究维度",requirement_id:"需求 ID",requirements:"研究需求",requirement_count:"需求数量",total_requirements:"需求总数",verified_requirements:"已审核需求",missing_requirements:"缺失需求",resolved_requirements:"合法终态需求",execution_failed_requirements:"执行失败需求",model_calls:"模型调用",tool_calls:"工具调用",protocol_corrections:"协议纠正",provider_retries:"服务重试",latency_ms:"实际耗时 (ms)",elapsed_ms:"实际耗时 (ms)",status:"执行状态",reason:"原因",reason_code:"原因代码",stop_reason:"终止原因",issue_code:"问题代码",code:"错误代码",message:"错误信息",error:"错误",issue:"问题",review_status:"审核状态",validation_status:"引用校验",triggered:"是否触发",recovered_requirements:"恢复需求",metrics:"执行统计",summary:"摘要",instruction:"反馈指令",recovery_instruction:"反馈指令",actual_action_count:"实际动作数",expected:"协议要求",evidence_ids:"证据 ID",claim_ids:"论断 ID",source:"数据来源",explanation:"来源说明",recorded_at:"原记录时间",note:"记录说明",started_at:"开始时间",finished_at:"结束时间",concurrency:"并发上限",validation:"引用校验",validation_results:"引用校验结果",valid:"校验通过",violations:"校验违规",validation_failures:"校验失败数",passed:"通过数",rejected:"拒绝数",unavailable:"不可用数",completed:"完成数",failed:"失败数",skipped:"跳过数",attempts:"尝试次数",retryable:"允许重试",completed_requirements:"已完成需求",insufficient_requirements:"证据不足需求",eligible_requirements:"可恢复需求",reason_counts:"原因统计"};
Object.assign(DETAIL_LABELS,{name:"名称",description:"说明",research_question:"研究问题",question:"问题",claim:"原始提交论断",required:"必要需求",dimension_count:"维度数量",dimension_ids:"研究维度",worker_task_count:"Worker 数量",candidate_paper_count:"候选论文数",worker_task_id:"Worker 任务",resolved_count:"合法终态需求",observed_evidence_ids:"已观察证据",tool_name:"工具名称",arguments:"工具参数",query:"检索问题",top_k:"返回数量",page:"PDF 页码",figure_id:"图号",figure_evidence_id:"图像证据",context_evidence_ids:"上下文证据",limitations:"限制",phase:"失败阶段",error_type:"错误类型",hint:"恢复提示",field:"参数字段",type:"字段错误",execution_status:"执行状态",retry_count:"重试次数",correction_allowed:"允许协议纠正",step:"实际步骤",requirement_ids:"需求 ID",investigated_requirement_ids:"调查需求",skip_reason:"跳过原因",record_available:"记录是否存在",generation_degraded:"是否降级",synthesis_model_calls:"综合模型调用",citation_validation_failures:"引用校验失败",evidence_coverage:"已审核证据覆盖率",resolution_rate:"合法终态率",review_pass_rate:"模型审核通过率",report_generation_success_rate:"报告生成成功率",degraded_generation:"降级生成",total_latency_ms:"实际总耗时 (ms)",synthesis_latency_ms:"综合耗时 (ms)",additional_model_calls:"额外模型调用",additional_tool_calls:"额外工具调用",recovery_eligible_count:"可恢复需求",recovery_attempted_count:"已尝试恢复",recovery_passed_count:"恢复后审核通过",recovery_failed_count:"恢复失败",recovery_skipped_count:"跳过恢复",recovery_rounds_used:"已使用恢复轮次",recovery_latency_ms:"恢复耗时 (ms)",eligible:"允许恢复",eligibility:"恢复资格",tasks:"实际恢复任务",final_requirements:"恢复后需求",final_reviews:"恢复后审核",recovery_task_id:"恢复任务",target_requirement_ids:"目标需求",attempt_index:"尝试序号",max_attempts:"最多尝试次数",protocol_recovery:"协议恢复",llm_calls:"策略模型调用",vlm_tool_attempts:"视觉工具尝试",dependency_calls:"依赖调用",retried_dependency_calls:"依赖重试",last_new_evidence_count:"新增证据数"});
const state = {papers:[],health:null,run:null,events:[],seen:new Set(),cursor:0,nodes:new Map(),displayNodes:new Map(),selectedNode:null,report:null,reportFetched:false,pollTimer:null,polling:false,posting:false,replays:[],refreshing:false,epoch:0};
const $ = (selector) => document.querySelector(selector);

// 任何模型或服务端字符串都通过 textContent 渲染，避免将论文、问题或报告执行为 HTML。
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function labelStatus(status) { return STATUS_LABELS[status] || status || "无记录"; }
function paperTitle(id) {
  const paper = state.papers.find((item) => item.paper_id === id);
  return paper ? paper.title : id || "论文无记录";
}
function shortPaperTitle(id) {
  const paper = state.papers.find((item) => item.paper_id === id);
  return paper?.display_name || paper?.short_title || (id ? id.replace(/-\d{4}\.\d+v\d+$/u, "").toUpperCase() : "论文无记录");
}
function badge(text, kind = "neutral") { return element("span", `badge ${kind}`, text); }
function hasActiveRun() { return Boolean(state.run && ACTIVE_STATUSES.has(state.run.status)); }
function selectedPapers() { return [...document.querySelectorAll("#papers input:checked")].map((input) => input.value); }
function displayValue(value) {
  if (value === null || value === undefined) return "无记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map((item) => typeof item === "object" ? JSON.stringify(safeDetails(item)) : String(item)).join("\n") : "无";
  return typeof value === "object" ? JSON.stringify(safeDetails(value), null, 2) : String(value);
}
function safeDetails(details) {
  // 只显示约定的执行事实，未知内部字段不进入详情面板。服务端仍负责完整脱敏。
  if (!details || typeof details !== "object") return {};
  const safe = {};
  for (const [key, value] of Object.entries(details)) {
    if (!(key in DETAIL_LABELS)) continue;
    safe[key] = Array.isArray(value)
      ? value.map((item) => item && typeof item === "object" ? safeDetails(item) : item)
      : value && typeof value === "object" ? safeDetails(value) : value;
  }
  return safe;
}
function emptyPanel(target, title, message) {
  const box = element("div", "empty-state");
  box.append(element("span", "empty-icon", "◇"), element("h3", "", title), element("p", "", message));
  target.replaceChildren(box);
}
function showError(message) { $("#api-error").textContent = message; $("#api-error").hidden = !message; }
async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {headers:{"Content-Type":"application/json"},...options});
  let body;
  try { body = await response.json(); } catch { throw new Error("服务返回了无法读取的响应，请检查服务状态。"); }
  if (!response.ok) throw new Error(body?.error?.message || body?.detail?.message || "请求未完成，请检查运行环境。");
  return body;
}
function setControls() {
  const busy = hasActiveRun() || state.posting;
  const ready = state.health?.real_research_ready === true;
  const paperCount = selectedPapers().length;
  const valid = $("#question").value.trim().length > 0 && paperCount >= 2 && paperCount <= 3;
  $("#start-button").disabled = busy || !ready || !valid;
  $("#start-button").textContent = state.posting ? "正在创建任务…" : hasActiveRun() ? "任务运行中" : "开始真实研究 →";
  $("#cancel-button").disabled = !hasActiveRun() || state.run?.mode === "replay" || state.run?.cancel_requested;
  $("#cancel-button").textContent = state.run?.cancel_requested ? "取消请求已提交" : "取消当前任务";
  $("#replay-button").disabled = busy || !state.health?.replay_ready || !$("#replay-select").value;
  $("#replay-select").disabled = busy || state.replays.length === 0;
  $("#question").disabled = busy;
  document.querySelectorAll("#papers input").forEach((input) => { input.disabled = busy; });
  $("#form-hint").textContent = !ready ? "真实研究尚未就绪；可查看下方历史案例。" : busy ? "正在执行任务，已锁定问题和论文，防止重复提交。" : "请选择 2–3 篇真实 Prepared Corpus 中的论文。";
}
function renderPapers() {
  const container = $("#papers");
  container.replaceChildren();
  if (!state.papers.length) { container.append(element("p", "muted small", "未发现可研究的预处理论文。")); return; }
  for (const paper of state.papers) {
    const option = element("label", "paper-option");
    const input = element("input"); input.type = "checkbox"; input.value = paper.paper_id;
    input.checked = /^(blip-2|flamingo)-/u.test(paper.paper_id);
    const text = element("span");
    text.append(element("span", "paper-name", shortPaperTitle(paper.paper_id)), element("span", "paper-info", `${paper.text_evidence_count ?? "—"} 条文本 · ${paper.figure_evidence_count ?? "—"} 张图像`));
    option.title = paper.title;
    option.append(input,text); container.append(option);
    input.addEventListener("change", () => { setControls(); if (!state.run) renderNodes(); });
  }
}
function renderHealth() {
  const container = $("#health"); container.replaceChildren();
  if (!state.health) { container.append(element("p", "small muted", "就绪状态尚无记录。")); return; }
  for (const dependency of state.health.dependencies || []) {
    const row = element("div", `health-item ${dependency.status}`);
    const dependencyNames = {configuration:"研究配置",prepared_corpus:"预处理论文语料",provider:"模型服务",embedding_cache:"本地嵌入模型",evidence_index:"证据检索索引",ui_static:"工作台静态资源",ui:"工作台静态资源"};
    const content = element("div"); content.append(element("strong", "", dependencyNames[dependency.name] || dependency.name));
    if (dependency.message) content.append(element("span", "health-message", dependency.message));
    row.append(element("i", "health-dot"),content); container.append(row);
  }
  if (!container.childElementCount) container.append(element("p", "small muted", state.health.real_research_ready ? "真实研究依赖就绪。" : "真实研究依赖尚未就绪。"));
  const limits = state.health.limits || {};
  const parts = [];
  if (limits.max_model_calls !== undefined) parts.push(`模型调用上限 ${limits.max_model_calls}`);
  if (limits.max_concurrent_runs !== undefined) parts.push(`同时运行 ${limits.max_concurrent_runs} 个任务`);
  if (limits.run_timeout_seconds !== undefined || limits.timeout_seconds !== undefined) parts.push(`任务超时 ${limits.run_timeout_seconds ?? limits.timeout_seconds} 秒`);
  $("#budget-hint").textContent = parts.join(" · ");
  setControls();
}
async function refreshHealth() {
  if (state.refreshing) return;
  state.refreshing = true; $("#refresh-health").disabled = true;
  try { state.health = await request("/health"); renderHealth(); } catch (error) { showError(error.message); }
  finally { state.refreshing = false; $("#refresh-health").disabled = false; }
}
function renderReplays() {
  const select = $("#replay-select"); select.replaceChildren();
  if (!state.replays.length) { const option = element("option", "", "没有可用的历史案例"); option.value = ""; select.append(option); }
  for (const replay of state.replays) { const option = element("option", "", replay.title); option.value = replay.case_id; select.append(option); }
  setControls();
}
function defaultNodes(type) {
  if (type === "Worker") return selectedPapers().map((paperId) => ({node_type:type,node_id:`pending:${paperId}`,status:"pending",summary:"等待实际 Worker 执行记录。",details:{paper_id:paperId},source:null}));
  return [{node_type:type,node_id:`pending:${type}`,status:"pending",summary:type === "Recovery" ? "按真实审核结果决定是否触发。" : type === "Validator" ? "等待实际引用结构校验结果。" : "等待实际执行记录。",details:{},source:null}];
}
function aggregateReviewStage(type, records) {
  if (records.length <= 1) return records;
  const counts = {completed:0,failed:0,skipped:0,running:0,pending:0};
  records.forEach((record) => { if (record.status in counts) counts[record.status] += 1; });
  const status = counts.running ? "running" : counts.pending ? "pending" : counts.failed ? "failed" : counts.completed ? "completed" : "skipped";
  // 此卡是可复核的结果汇总，不是新增 Agent 或臆造的生命周期事件。
  return [{node_type:type,node_id:`summary:${type}`,status,timestamp:null,source:records.every((record) => record.source === "replay") ? "replay" : "derived",summary:`已记录 ${records.length} 条需求：通过 ${counts.completed} · 失败 ${counts.failed} · 跳过 ${counts.skipped}。点击查看逐项记录。`,details:{record_available:true},records}];
}
function renderNodes() {
  state.displayNodes.clear();
  for (const type of NODE_TYPES) {
    const target = $(`#${type.toLowerCase()}-nodes`); target.replaceChildren();
    const actual = [...state.nodes.values()].filter((event) => event.node_type === type);
    const nodes = actual.length ? ["Validator","Reviewer"].includes(type) ? aggregateReviewStage(type,actual) : actual : defaultNodes(type);
    for (const event of nodes) {
      state.displayNodes.set(event.node_id,event);
      const button = element("button", `node ${event.status}${state.selectedNode === event.node_id ? " selected" : ""}`);
      button.type = "button"; button.dataset.nodeId = event.node_id;
      const top = element("div", "node-top");
      const title = type === "Worker" ? `${shortPaperTitle(event.details?.paper_id || event.node_id.replace(/^worker[:_-]/u,""))} Worker` : NODE_LABELS[type];
      top.append(element("span", "node-title", title),badge(labelStatus(event.status),event.status));
      button.append(top,element("p", "node-summary", event.summary || "无执行摘要。"));
      const bottom = element("div", "node-bottom");
      bottom.append(element("span", "node-origin", SOURCE_LABELS[event.source] || "尚无记录"));
      // 没有记录的时间不从浏览器时钟推算；Validator 的推导结果尤其不生成伪生命周期。
      if (event.timestamp && type !== "Validator") {
        const date = new Date(event.timestamp);
        if (!Number.isNaN(date.getTime())) bottom.append(element("time", "node-time", date.toLocaleTimeString("zh-CN",{hour12:false})));
      }
      button.append(bottom);
      button.addEventListener("click", () => { state.selectedNode = event.node_id; renderNodes(); renderNodeDetails(event); selectTab("node-panel"); });
      target.append(button);
    }
  }
}
function detailRows(target, details) {
  const list = element("dl");
  for (const [key,value] of Object.entries(safeDetails(details))) {
    const row = element("div", "detail-row");
    row.append(element("dt", "", DETAIL_LABELS[key]),element("dd", "", key === "status" ? labelStatus(value) : displayValue(value)));
    list.append(row);
  }
  target.append(list);
}
function renderNodeDetails(event) {
  const target = $("#node-details"); target.className = ""; target.replaceChildren();
  const heading = element("div", "detail-heading"); heading.append(element("h3", "", NODE_LABELS[event.node_type]),badge(labelStatus(event.status),event.status));
  target.append(heading,element("p", "detail-summary", event.summary || "无执行摘要。"));
  if (event.details?.error || event.details?.issue) {
    target.append(element("h4", "detail-subheading", "错误与终止信息"));
    detailRows(target,event.details.error || event.details.issue);
  }
  const basic = {...safeDetails(event.details)};
  delete basic.requirements; delete basic.error; delete basic.issue;
  detailRows(target,{source:SOURCE_LABELS[event.source] || "尚无记录",...basic});
  if (event.node_type === "Validator") target.append(element("p", "report-notice", "引用结构验证只核对身份与归属，不等同于语义正确性或人工审核。无独立事件时，不显示推定开始或结束时间。"));
  if (event.node_type === "Reviewer") target.append(element("p", "report-notice", "Reviewer 是模型辅助语义审核，审核通过不等同于独立人工事实核验。"));
  const evidence = Array.isArray(event.details?.evidence) ? event.details.evidence : [];
  if (evidence.length) renderEvidence(evidence, null);
  if (event.records) {
    target.append(element("p", "report-notice", "此节点汇总已保存的逐项结果，不代表独立开始/结束事件。原始事件序号和来源保留在执行事件列表。"));
    for (const record of event.records) {
      const item = element("div", "evidence-card");
      item.append(element("h4", "", `${shortPaperTitle(record.details?.paper_id)} · ${labelStatus(record.status)}`));
      detailRows(item,record.details); target.append(item);
    }
  }
  if (event.details?.trace) {
    target.append(element("h4", "detail-subheading", "实际执行统计")); detailRows(target,event.details.trace);
  }
  if (Array.isArray(event.details?.requirements) && event.details.requirements.length) {
    const requirements = element("details", "report-source");
    requirements.append(element("summary", "detail-subheading", `研究需求与原始提交 · ${event.details.requirements.length} 项`));
    for (const requirement of event.details.requirements) {
      const item = element("div", "evidence-card");
      item.append(element("h4", "", `${requirement.dimension_id || "维度无记录"} · ${labelStatus(requirement.status)}`));
      detailRows(item,requirement); requirements.append(item);
    }
    requirements.append(element("p", "report-notice", "Worker 原始提交不等同于最终已审核论断；最终可引用事实请查看研究报告。"));
    target.append(requirements);
  }
  const actions = event.details?.trace?.actions || event.details?.actions || event.details?.tool_actions;
  if (Array.isArray(actions) && actions.length) {
    target.append(element("h4", "detail-subheading", "可观察工具记录"));
    for (const action of actions) {
      const item = element("div", "evidence-card"); item.append(element("strong", "", action.tool_name || action.action || "工具动作"));
      detailRows(item,safeDetails(action));
      if (action.failure) { item.append(element("h4", "detail-subheading", "失败与恢复提示")); detailRows(item,action.failure); }
      target.append(item);
    }
  }
}
function renderEvents() {
  const list = $("#event-list"); list.replaceChildren();
  if (!state.events.length) { list.append(element("li", "muted small empty-event", "尚无执行记录。")); return; }
  for (const event of state.events) {
    const item = element("li");
    item.append(element("span", "event-number", String(event.event_id)),element("span", "event-summary", `${NODE_LABELS[event.node_type] || event.node_type} · ${event.summary || labelStatus(event.status)}`),element("span", "event-source", SOURCE_LABELS[event.source] || "来源无记录"));
    item.dataset.eventId = String(event.event_id); list.append(item);
  }
  $("#event-count").textContent = `${state.events.length} 条`;
}
function numberMetric(metrics, names) { for (const name of names) if (metrics[name] !== undefined && metrics[name] !== null) return metrics[name]; return "—"; }
function renderRun() {
  if (!state.run) return;
  $("#mode-badge").textContent = state.run.mode === "replay" ? "历史回放 · 不调用模型" : "真实研究";
  $("#mode-badge").className = `badge ${state.run.mode === "replay" ? "replay" : "live"}`;
  $("#run-status").textContent = state.run.cancel_requested && hasActiveRun() ? "取消请求已提交，等待执行收尾" : state.run.mode === "replay" && state.run.status === "completed" ? "历史记录已载入" : labelStatus(state.run.status);
  $("#run-question").textContent = state.run.question || "问题无记录";
  $("#run-id").textContent = state.run.run_id;
  const metrics = {...(state.report?.metrics || {}),...(state.run.metrics || {})};
  const values = [numberMetric(metrics,["model_calls","total_model_calls"]),numberMetric(metrics,["tool_calls","total_tool_calls"]),numberMetric(metrics,["verified_requirements","verified_requirement_count"]),numberMetric(metrics,["total_latency_ms","latency_ms","wall_time_ms"])];
  const blocks = document.querySelectorAll("#metrics strong");
  values.forEach((value,index) => { blocks[index].textContent = index === 3 && typeof value === "number" ? `${(value / 1000).toFixed(1)} s` : String(value); });
  const metricLabels = state.run.mode === "replay" ? ["历史模型调用","历史工具调用","已审核需求","原执行耗时"] : ["模型调用","工具调用","已审核需求","实际耗时"];
  document.querySelectorAll("#metrics span").forEach((item,index) => { item.textContent = metricLabels[index]; });
  if (state.run.error) showError(state.run.error.message || "研究执行失败；已完成的记录仍保留。");
  setControls();
}
function renderEvidence(evidence, claim) {
  const target = $("#evidence-details"); target.className = ""; target.replaceChildren();
  target.append(element("p", "report-notice", "以下为原论文证据快照。引用身份和模型审核状态均不能替代独立人工事实核验。"));
  for (const item of evidence) {
    const card = element("article", "evidence-card");
    card.append(element("h4", "", shortPaperTitle(item.paper_id)),element("div", "citation-row", `PDF 第 ${item.page ?? "无记录"} 页${item.figure_id ? ` · ${item.figure_id}` : ""}`));
    card.append(element("div", "evidence-id", item.evidence_id || "证据 ID 无记录"));
    card.append(element("p", "evidence-text", item.text || item.caption || "证据文本无记录。"));
    if (item.caption && item.text) card.append(element("p", "evidence-text", `图注：${item.caption}`));
    if (claim) card.append(element("p", "evidence-text", `对应论断：${claim.text}`),badge(labelStatus(claim.review_status),"completed"));
    if (item.corpus_check) card.append(element("p", "muted small", `证据快照核对：${item.corpus_check}`));
    target.append(card);
  }
  if (!evidence.length) target.append(element("p", "muted small", "当前记录没有可展示证据。"));
}
function showClaimEvidence(claim) { renderEvidence(claim.evidence || [],claim); selectTab("evidence-panel"); }
function renderReport() {
  const report = state.report;
  const target = $("#report-details");
  if (!report || report.status === "report_unavailable") { emptyPanel(target,"没有可用报告","服务记录尚未包含最终研究报告；已完成的节点与错误仍可查看。"); return; }
  target.className = ""; target.replaceChildren();
  target.append(element("h3", "report-heading", report.title || "实际研究报告"),badge(labelStatus(report.status),report.status === "completed" ? "completed" : "skipped"));
  target.append(element("p", "report-notice", "模型输出与 Reviewer 通过不等于独立人工事实审核。已审核论断、缺失状态和报告文字均来自本次记录。"));
  if (report.provenance) {
    target.append(element("p", "report-notice", `报告来源：${report.provenance.case_id || report.provenance.source || "本次研究记录"}。${report.provenance.note || ""}`));
    const sources = report.provenance.source_files || [];
    if (sources.length) {
      const origin = element("details", "report-source"); origin.append(element("summary", "small muted", "查看原始文件与校验值"));
      for (const source of sources) origin.append(element("p", "evidence-id", `${source.path || "来源路径无记录"}\nSHA256: ${source.sha256 || "无记录"}`));
      target.append(origin);
    }
  }
  const claims = report.verified_claims || [];
  if (claims.length) {
    target.append(element("h4", "report-section-title", `已审核论断 · ${claims.length} 项`));
    for (const claim of claims) {
      const card = element("article", "claim-card");
      card.append(element("h4", "", `${shortPaperTitle(claim.paper_id)} · ${claim.dimension_id}`),element("p", "evidence-text", claim.text));
      const sources = (claim.evidence || []).map((item) => `${shortPaperTitle(item.paper_id)} · 第 ${item.page ?? "无记录"} 页`).join("；");
      card.append(element("p", "citation-row", sources || "引用记录缺失"));
      const button = element("button", "text-button", "查看原始证据 →"); button.type = "button";
      button.addEventListener("click", () => showClaimEvidence(claim)); card.append(button); target.append(card);
    }
    renderEvidence(claims.flatMap((claim) => claim.evidence || []),null);
  } else { target.append(element("p", "missing-card", "当前没有足够已审核证据，不能据此补充论文技术事实。")); emptyPanel($("#evidence-details"),"没有已审核证据","报告保留证据缺口，不使用模型记忆补齐。"); }
  const missing = report.missing_requirements || [];
  if (missing.length) {
    target.append(element("h4", "report-section-title", `未完成或证据缺失 · ${missing.length} 项`));
    for (const item of missing) target.append(element("div", "missing-card", `${shortPaperTitle(item.paper_id)} · ${item.dimension_id}\n${labelStatus(item.status)}${item.reason_code || item.reason ? ` · ${item.reason_code || item.reason}` : ""}`));
  }
  const fullReport = element("details", "report-source");
  fullReport.append(element("summary", "report-section-title", "实际报告全文（展开查看）"));
  // Markdown 作为纯文本展示，保持原始报告，不把模型输出直接交给 HTML 解释器。
  fullReport.append(element("pre", "report-text", report.markdown || "报告全文无记录。"));
  target.append(fullReport);
  if ((report.limitations || []).length) {
    target.append(element("h4", "report-section-title", "研究限制")); const list = element("ul", "limitations-list");
    for (const limitation of report.limitations) list.append(element("li", "", limitation)); target.append(list);
  }
}
function selectTab(panelId) {
  document.querySelectorAll(".tab").forEach((button) => { const active = button.dataset.panel === panelId; button.classList.toggle("active",active); button.setAttribute("aria-selected",String(active)); });
  document.querySelectorAll(".detail-content").forEach((panel) => { panel.hidden = panel.id !== panelId; });
}
function stopPolling() { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; }
function rememberRun(id) { try { localStorage.setItem(STORAGE_KEY,id); } catch { /* 隐私模式禁用存储时，当前页仍可正常工作。 */ } }
function resetRun(run) {
  stopPolling(); state.epoch += 1;
  state.run = run; state.events = []; state.seen.clear(); state.nodes.clear(); state.cursor = 0; state.selectedNode = null; state.report = null; state.reportFetched = false;
  $("#event-count").textContent = "0 条";
  emptyPanel($("#node-details"),"等待节点记录","点击流程节点查看真实执行详情。");
  emptyPanel($("#evidence-details"),"尚无证据","等待本次记录中的引用证据。");
  emptyPanel($("#report-details"),"尚无研究报告","报告生成后保留真实结论与证据缺口。");
  showError(""); rememberRun(run.run_id); renderRun(); renderNodes(); renderEvents();
}
async function pollRun() {
  if (!state.run || state.polling) return;
  const runId = state.run.run_id, epoch = state.epoch;
  state.polling = true;
  try {
    const [run,result] = await Promise.all([request(`/runs/${encodeURIComponent(runId)}`),request(`/runs/${encodeURIComponent(runId)}/events?after=${state.cursor}`)]);
    if (epoch !== state.epoch) return;
    state.run = run;
    for (const event of result.events || []) {
      if (event.run_id !== runId || state.seen.has(String(event.event_id))) continue;
      state.seen.add(String(event.event_id)); state.events.push(event);
      if (NODE_TYPES.includes(event.node_type)) state.nodes.set(event.node_id,event);
    }
    if (result.next_cursor !== undefined) state.cursor = result.next_cursor;
    renderEvents(); renderNodes(); renderRun();
    if (state.selectedNode && state.displayNodes.has(state.selectedNode)) renderNodeDetails(state.displayNodes.get(state.selectedNode));
    if (run.report_available && !state.reportFetched) {
      const report = await request(`/runs/${encodeURIComponent(runId)}/report`);
      if (epoch !== state.epoch) return;
      state.report = report;
      state.reportFetched = true; renderReport(); renderRun();
    }
    if (!hasActiveRun() && state.report) selectTab("report-panel");
  } catch (error) { if (epoch === state.epoch) showError(error.message); }
  finally {
    state.polling = false;
    // 轮询仅获取真实增量数据，不根据计时器推进图中阶段，也不重新提交任务。
    if (epoch === state.epoch && hasActiveRun()) state.pollTimer = setTimeout(pollRun,1000);
    else if (epoch !== state.epoch && state.run) state.pollTimer = setTimeout(pollRun,0);
  }
}
async function startRun(event) {
  event.preventDefault();
  if (state.posting || hasActiveRun() || !state.health?.real_research_ready) return;
  const question = $("#question").value.trim(),paper_ids = selectedPapers();
  if (!question || paper_ids.length < 2 || paper_ids.length > 3) { showError("请输入研究问题并选择 2–3 篇论文。"); return; }
  state.posting = true; setControls(); showError("");
  try { resetRun(await request("/runs",{method:"POST",body:JSON.stringify({question,paper_ids})})); await pollRun(); }
  catch (error) { showError(error.message); }
  finally { state.posting = false; setControls(); }
}
async function startReplay() {
  if (state.posting || hasActiveRun() || !$("#replay-select").value) return;
  state.posting = true; setControls(); showError("");
  try { resetRun(await request(`/replays/${encodeURIComponent($("#replay-select").value)}`,{method:"POST",body:"{}"})); await pollRun(); }
  catch (error) { showError(error.message); }
  finally { state.posting = false; setControls(); }
}
async function cancelRun() {
  if (!hasActiveRun() || state.run.cancel_requested) return;
  $("#cancel-button").disabled = true;
  try { state.run = await request(`/runs/${encodeURIComponent(state.run.run_id)}/cancel`,{method:"POST",body:"{}"}); renderRun(); stopPolling(); await pollRun(); }
  catch (error) { showError(error.message); setControls(); }
}
async function initialize() {
  $("#research-form").addEventListener("submit",startRun);
  $("#question").addEventListener("input",setControls);
  $("#cancel-button").addEventListener("click",cancelRun);
  $("#replay-button").addEventListener("click",startReplay);
  $("#replay-select").addEventListener("change",setControls);
  $("#refresh-health").addEventListener("click",refreshHealth);
  document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.panel)));
  const results = await Promise.allSettled([request("/papers"),request("/health"),request("/replays")]);
  if (results[0].status === "fulfilled") state.papers = results[0].value.papers || [];
  if (results[1].status === "fulfilled") state.health = results[1].value;
  if (results[2].status === "fulfilled") state.replays = results[2].value.cases || [];
  renderPapers(); renderHealth(); renderReplays(); renderNodes();
  const failed = results.find((result) => result.status === "rejected"); if (failed) showError(failed.reason.message);
  let savedId; try { savedId = localStorage.getItem(STORAGE_KEY); } catch { savedId = null; }
  if (savedId) {
    try { resetRun(await request(`/runs/${encodeURIComponent(savedId)}`)); await pollRun(); }
    catch { try { localStorage.removeItem(STORAGE_KEY); } catch { /* 不影响界面启动。 */ } showError("上次任务已不在当前服务中；进程内任务在重启后可能丢失，请重新选择历史案例或创建任务。"); }
  }
}
initialize();
