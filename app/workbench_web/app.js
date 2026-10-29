/* 中文工作台只呈现服务端公开事实；研究决策、预算和取消由后端负责。 */
"use strict";

const API = "/api/research";
const STORAGE_KEY = "personal-agent-workbench-run-id";
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const NODE_TYPES = ["MainAgent", "PaperSearch", "QueryExpansion", "Evidence", "ResearchTool", "QueryLanguage", "PaperSelection", "Planner", "Worker", "Validator", "Reviewer", "Recovery", "Synthesis", "ReportLanguage"];
const TERMINAL_NODE_STATUSES = new Set(["completed", "failed", "skipped"]);
const STATUS_LABELS = {pending:"等待记录",queued:"排队中",running:"运行中",completed:"已完成",failed:"执行失败",cancelled:"已取消",skipped:"已跳过",partial_success:"部分完成",insufficient_evidence:"证据不足",execution_failed:"执行失败",verified:"已审核",review_rejected:"审核拒绝",review_unavailable:"审核不可用",generation_degraded:"降级生成",supported:"有证据支持",passed:"审核通过",rejected:"审核拒绝",cancel_requested:"正在取消"};
Object.assign(STATUS_LABELS,{translated:"已转换",not_needed:"无需转换",unavailable:"不可用",empty:"没有结果",empty_result:"没有结果",invalid_arguments:"参数不合法",redundant_action:"重复动作已阻止",dependency_failure:"依赖失败",internal_failure:"内部执行失败"});
const SOURCE_LABELS = {live:"实时调用",derived:"记录推导",replay:"历史回放"};
const NODE_LABELS = {MainAgent:"主 Agent",PaperSearch:"论文检索",QueryExpansion:"查询扩展",Evidence:"证据工具",ResearchTool:"深入研究工具",QueryLanguage:"问题语言转换",PaperSelection:"自动论文选择",Planner:"研究规划",Worker:"论文研究",Validator:"引用结构验证",Reviewer:"论断审核",Recovery:"定向恢复",Synthesis:"综合生成",ReportLanguage:"报告中文转换"};
const ROLE_DESCRIPTIONS = {MainAgent:"按问题、观察结果和预算决定下一动作",PaperSearch:"查询真实本地论文索引",QueryExpansion:"只生成检索查询，不执行检索",Evidence:"实际读取文本或图像证据",ResearchTool:"可选调用已有多论文研究链",QueryLanguage:"保留原问题并生成内部研究问题",PaperSelection:"仅从已准备正文证据的论文中选择",Planner:"拆分研究维度与证据需求",Worker:"每篇论文独立检索和提交论断",Validator:"核对引用身份与论文归属",Reviewer:"模型辅助审核论断与证据",Recovery:"针对实际未通过的需求进行有界恢复",Synthesis:"仅组织已审核事实与缺失状态",ReportLanguage:"翻译可读文本，保留权威引用和状态"};
const DETAIL_LABELS = {paper_id:"论文",paper_ids:"论文范围",dimension_id:"研究维度",dimension:"研究维度",dimensions:"研究维度",requirement_id:"需求 ID",requirements:"研究需求",requirement_count:"需求数量",total_requirements:"需求总数",verified_requirements:"已审核需求",missing_requirements:"缺失需求",resolved_requirements:"合法终态需求",execution_failed_requirements:"执行失败需求",model_calls:"模型调用",tool_calls:"工具调用",protocol_corrections:"协议纠正",provider_retries:"服务重试",latency_ms:"实际耗时 (ms)",elapsed_ms:"实际耗时 (ms)",status:"执行状态",reason:"原因",reason_code:"原因代码",stop_reason:"终止原因",issue_code:"问题代码",code:"错误代码",message:"错误信息",error:"错误",issue:"问题",review_status:"审核状态",validation_status:"引用校验",triggered:"是否触发",recovered_requirements:"恢复需求",metrics:"执行统计",summary:"摘要",instruction:"反馈指令",recovery_instruction:"反馈指令",actual_action_count:"实际动作数",expected:"协议要求",evidence_ids:"证据 ID",claim_ids:"论断 ID",source:"数据来源",explanation:"来源说明",recorded_at:"原记录时间",note:"记录说明",started_at:"开始时间",finished_at:"结束时间",concurrency:"并发上限",validation:"引用校验",validation_results:"引用校验结果",valid:"校验通过",violations:"校验违规",validation_failures:"校验失败数",passed:"通过数",rejected:"拒绝数",unavailable:"不可用数",completed:"完成数",failed:"失败数",skipped:"跳过数",attempts:"尝试次数",retryable:"允许重试",completed_requirements:"已完成需求",insufficient_requirements:"证据不足需求",eligible_requirements:"可恢复需求",reason_counts:"原因统计"};
Object.assign(DETAIL_LABELS,{name:"名称",description:"说明",research_question:"研究问题",question:"问题",claim:"原始提交论断",required:"必要需求",dimension_count:"维度数量",dimension_ids:"研究维度",worker_task_count:"Worker 数量",candidate_paper_count:"候选论文数",worker_task_id:"Worker 任务",resolved_count:"合法终态需求",observed_evidence_ids:"已观察证据",tool_name:"工具名称",arguments:"工具参数",query:"检索问题",top_k:"返回数量",page:"PDF 页码",figure_id:"图号",figure_evidence_id:"图像证据",context_evidence_ids:"上下文证据",limitations:"限制",phase:"失败阶段",error_type:"错误类型",hint:"恢复提示",field:"参数字段",type:"字段错误",execution_status:"执行状态",retry_count:"重试次数",correction_allowed:"允许协议纠正",step:"实际步骤",requirement_ids:"需求 ID",investigated_requirement_ids:"调查需求",skip_reason:"跳过原因",record_available:"记录是否存在",generation_degraded:"是否降级",synthesis_model_calls:"综合模型调用",citation_validation_failures:"引用校验失败",evidence_coverage:"已审核证据覆盖率",resolution_rate:"合法终态率",review_pass_rate:"模型审核通过率",report_generation_success_rate:"报告生成成功率",degraded_generation:"降级生成",total_latency_ms:"实际总耗时 (ms)",synthesis_latency_ms:"综合耗时 (ms)",additional_model_calls:"额外模型调用",additional_tool_calls:"额外工具调用",recovery_eligible_count:"可恢复需求",recovery_attempted_count:"已尝试恢复",recovery_passed_count:"恢复后审核通过",recovery_failed_count:"恢复失败",recovery_skipped_count:"跳过恢复",recovery_rounds_used:"已使用恢复轮次",recovery_latency_ms:"恢复耗时 (ms)",eligible:"允许恢复",eligibility:"恢复资格",tasks:"实际恢复任务",final_requirements:"恢复后需求",final_reviews:"恢复后审核",recovery_task_id:"恢复任务",target_requirement_ids:"目标需求",attempt_index:"尝试序号",max_attempts:"最多尝试次数",protocol_recovery:"协议恢复",llm_calls:"策略模型调用",vlm_tool_attempts:"视觉工具尝试",dependency_calls:"依赖调用",retried_dependency_calls:"依赖重试",last_new_evidence_count:"新增证据数"});
Object.assign(DETAIL_LABELS,{original_question:"原始问题",research_question:"内部研究问题",language_mode:"语言模式",selected_paper_ids:"实际选择的论文",selection_status:"论文选择状态",reasons:"选择说明",source_language:"问题语言",translation_review:"翻译审核说明",translated:"已转换",source_status:"原始状态",target_language:"展示语言",translation_status:"转换状态",evidence:"证据摘要",text:"原始证据正文",caption:"图注",score:"检索得分",retrieval_score:"检索得分",result_count:"结果数量",executed:"是否实际执行",evidence_available:"正文证据可用",unavailable_paper_ids:"缺少正文证据的论文"});
const state = {view:"chat",pending:null,ledger:new ResearchUI.EventLedger(),readErrors:0,gapReads:0,storage:null,reportLanguage:"zh",eventTruncated:false,openGroups:new Map(),papers:[],health:null,run:null,events:[],seen:new Set(),cursor:0,nodes:new Map(),displayNodes:new Map(),selectedNode:null,report:null,reportFetched:false,pollTimer:null,polling:false,posting:false,replays:[],refreshing:false,epoch:0};
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
function dimensionTitle(id,report = null) {
  const source = report || state.report?.chinese_report || state.report;
  const dimension = source?.diagnostics?.planner?.dimensions?.find((item) => item.dimension_id === id);
  return dimension?.name || id || "维度无记录";
}
// 只排版常用 Markdown 结构，所有片段仍以文本节点写入，不执行 HTML 或链接脚本。
function answerProse(text) {
  const target=element("div","evidence-text answer-prose");
  const snapshots=[...(state.report?.verified_claims || []),...(state.report?.key_findings || [])].flatMap(c=>c.evidence || []);
  const citations=new Map((state.report?.citations || []).map(c=>[c.evidence_id,c]));
  function inline(parent,value) {
    const tokens=value.split(/(\*\*[^*]+\*\*|`[^`]+`|\[ev_[a-zA-Z0-9_-]+\])/gu);
    for(const token of tokens) {
      if(token.startsWith("**") && token.endsWith("**"))parent.append(element("strong","",token.slice(2,-2)));
      else if(token.startsWith("`") && token.endsWith("`"))parent.append(element("code","",token.slice(1,-1)));
      else if(token.startsWith("[ev_") && citations.has(token.slice(1,-1))) {
        const id=token.slice(1,-1),source=citations.get(id),button=element("button","inline-citation",`${shortPaperTitle(source.paper_id)} · p.${source.page}`);
        button.type="button";button.title=id;
        button.addEventListener("click",()=>{renderEvidence(snapshots.filter(e=>e.evidence_id===id).length ? snapshots.filter(e=>e.evidence_id===id) : [source],null);selectTab("evidence-panel");});parent.append(button);
      } else parent.append(document.createTextNode(token));
    }
  }
  let list=null;
  for(const line of String(text || "").split("\n")) {
    if(!line.trim()){list=null;continue;}
    const heading=/^(#{1,4})\s+(.+)$/u.exec(line),bullet=/^\s*(?:[-*]|\d+\.)\s+(.+)$/u.exec(line);
    if(bullet){if(!list){list=element("ul");target.append(list);}const item=element("li");inline(item,bullet[1]);list.append(item);}
    else {list=null;const block=element(heading ? "h4" : "p");inline(block,heading ? heading[2] : line);target.append(block);}
  }
  return target;
}
function badge(text, kind = "neutral") { return element("span", `badge ${kind}`, text); }
function hasActiveRun() { return Boolean(state.run && ACTIVE_STATUSES.has(state.run.status)); }
function displayValue(value) {
  if (value === null || value === undefined) return "无记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map((item) => typeof item === "object" ? JSON.stringify(safeDetails(item)) : String(item)).join("\n") : "无";
  return typeof value === "object" ? JSON.stringify(safeDetails(value), null, 2) : STATUS_LABELS[value] || String(value);
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
const request = ResearchUI.request;
function setControls() {
  const busy = hasActiveRun() || state.posting;
  $("#new-research").disabled = busy || Boolean(state.pending);
  $("#model-budget").disabled = busy;
  const ready = (state.health?.main_agent_ready ?? state.health?.real_research_ready) === true;
  const validScope = true;
  const valid = $("#question").value.trim().length > 0 && validScope;
  $("#start-button").disabled = busy || Boolean(state.pending) || !ready || !valid;
  $("#start-button").textContent = state.posting ? "正在创建任务…" : hasActiveRun() ? "任务运行中" : "发送 ↑";
  $("#cancel-button").disabled = !hasActiveRun() || state.run?.mode === "replay" || state.run?.cancel_requested;
  $("#cancel-button").textContent = state.run?.cancel_requested ? "取消请求已提交" : "取消当前任务";
  $("#replay-button").disabled = busy || Boolean(state.pending) || !state.health?.replay_ready || !$("#replay-select").value;
  $("#replay-select").disabled = busy || state.replays.length === 0;
  $("#question").disabled = busy;
  $("#original-language").disabled = busy;
  $("#selection-mode").textContent = "论文库仅供资源浏览，不要求选择论文。";
  $("#form-hint").textContent = state.pending ? "先核对结果未知的原提交；不会自动再次启动任务。" : !validScope ? "当前研究协议支持 2–3 篇论文；可取消勾选，改用自动选择。" : !ready ? "真实研究依赖尚未就绪；仍可使用历史记录中的归档回放。" : busy ? "正在执行任务，输入已锁定，避免重复提交。" : "主 Agent 自主选择动作；本地目录元数据不等于正文证据。";
}
function renderPapers() {
  const container = $("#papers"); container.replaceChildren();
  for (const paper of state.papers) {
    const option = element("div", "paper-option"), text = element("span");
    text.append(element("span", "paper-name", shortPaperTitle(paper.paper_id)), element("span", "paper-info", paper.availability === "metadata_only" ? "仅元数据 · 正文未准备" : "正文证据可用"));
    option.title = paper.title; option.append(text,element("p","paper-title-full",paper.title)); container.append(option);
  }
  $("#paper-count").textContent = String(state.papers.length);
  if (!state.papers.length) container.append(element("p", "muted small", "论文资源暂不可用，仍可查看历史记录。"));
}
function renderHealth() {
  const container = $("#health"); container.replaceChildren();
  if (!state.health) { container.append(element("p", "small muted", "就绪状态尚无记录。")); return; }
  for (const dependency of state.health.dependencies || []) {
    const row = element("div", `health-item ${dependency.status}`);
    const dependencyNames = {language_adapter:"问题与报告语言适配",paper_selection:"自动论文选择",configuration:"研究配置",prepared_corpus:"预处理论文语料",provider:"模型服务",embedding_cache:"本地嵌入模型",evidence_index:"证据检索索引",ui_static:"工作台静态资源",ui:"工作台静态资源"};
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
function visibleSummary(event) {
  // 英文原摘要保存在详情；状态文案是确定性映射，不补写执行结果或研究结论。
  if (/[\u3400-\u9fff]/u.test(event.summary || "")) return event.summary;
  const details = event.details || {}, parts = [labelStatus(event.status)];
  if (details.requirement_count !== undefined) parts.push(`${details.requirement_count} 项研究需求`);
  if (details.result_count !== undefined) parts.push(`${details.result_count} 条结果`);
  if (details.tool_calls !== undefined) parts.push(`${details.tool_calls} 次工具调用`);
  if (details.evidence_ids?.length) parts.push(`${details.evidence_ids.length} 条引用证据`);
  return `${parts.join(" · ")}。点击查看原始执行记录。`;
}
function rebuildNodes() {
  state.nodes.clear();
  // 先按服务端事件序号排序，再重建节点；网络批次乱序不能倒退状态或制造新节点。
  state.events.sort((left,right) => Number(left.event_id) - Number(right.event_id));
  for (const event of state.events) {
    if (!NODE_TYPES.includes(event.node_type)) continue;
    const previous = state.nodes.get(event.node_id);
    if (!previous) { state.nodes.set(event.node_id,{...event,first_event_id:event.event_id,records:[event]}); continue; }
    const lateProgress = TERMINAL_NODE_STATUSES.has(previous.status) && !TERMINAL_NODE_STATUSES.has(event.status);
    const details = lateProgress ? {...event.details,...previous.details} : {...previous.details,...event.details};
    state.nodes.set(event.node_id,lateProgress
      ? {...previous,details,records:[...previous.records,event]}
      : {...event,details,first_event_id:previous.first_event_id,records:[...previous.records,event]});
  }
}
// 同一节点在两种布局中复用详情、状态和点击行为，不制造新的执行节点。
function nodeCard(event) {
  state.displayNodes.set(event.node_id,event);
  const type = event.node_type, paper = event.details?.paper_id;
  const button = element("button", `node ${event.status}${state.selectedNode === event.node_id ? " selected" : ""}`);
  button.type = "button"; button.dataset.nodeId = event.node_id; button.dataset.nodeType = type;
  const title = paper && ["Worker","Validator","Reviewer","Recovery"].includes(type)
    ? `${shortPaperTitle(paper)} · ${NODE_LABELS[type]}` : NODE_LABELS[type];
  const top = element("div", "node-top");
  top.append(element("span", "node-title", title),badge(labelStatus(event.status),event.status));
  button.append(top,element("p", "node-summary", visibleSummary(event)));
  const bottom = element("div", "node-bottom");
  bottom.append(element("span", "node-origin", SOURCE_LABELS[event.source] || "来源无记录"));
  if (event.timestamp && event.source !== "derived" && type !== "Validator") {
    const date = new Date(event.timestamp);
    if (!Number.isNaN(date.getTime())) bottom.append(element("time", "node-time", date.toLocaleTimeString("zh-CN",{hour12:false})));
  }
  button.append(bottom);
  button.addEventListener("click", () => { state.selectedNode = event.node_id; renderNodes(); renderNodeDetails(event); selectTab("node-panel"); });
  return button;
}
function treeItem(event) {
  const item = element("li", "tree-item");
  item.append(nodeCard(event));
  return item;
}
function treeList(events, className = "") {
  const list = element("ul", `tree-list ${className}`);
  for (const event of events) list.append(treeItem(event));
  return list;
}
function renderResearchBranches(actual) {
  const list = element("ul", "tree-list research-branches");
  const roles = new Set(["Planner","Worker","Validator","Reviewer","Recovery","Synthesis"]);
  const records = actual.filter(event => roles.has(event.node_type));
  if (!records.length) return list;
  for (const event of records.filter(e => e.node_type === "Planner")) list.append(treeItem(event));
  // 仅按公开 paper_id / Worker 需求映射归属；无身份记录的审核保持在公共分支。
  const requirementPapers = new Map();
  for (const event of records) {
    for (const requirement of event.details?.requirements || []) {
      const paper = requirement.paper_id || event.details?.paper_id;
      if (!paper || !requirement.requirement_id) continue;
      const previous = requirementPapers.get(requirement.requirement_id);
      requirementPapers.set(requirement.requirement_id, previous === undefined || previous === paper ? paper : null);
    }
  }
  const papers = new Map(), common = [];
  const workerPapers = new Set(records.filter(e=>e.node_type === "Worker").map(e=>e.details?.paper_id).filter(Boolean));
  for (const event of records.filter(e => !["Planner","Synthesis"].includes(e.node_type))) {
    const paper = event.details?.paper_id || requirementPapers.get(event.details?.requirement_id);
    if (!paper || !workerPapers.has(paper)) { common.push(event); continue; }
    if (!papers.has(paper)) papers.set(paper, []);
    papers.get(paper).push(event);
  }
  if (papers.size) {
    const fork = element("li", "tree-item tree-fork");
    fork.append(element("p", "tree-caption", `按论文分工 · ${papers.size} 个已记录分支`));
    const branches = element("ul", "paper-branches");
    for (const [paper, events] of papers) {
      const branch = element("li", "paper-branch"); branch.dataset.paperId = paper;
      const group = element("details","worker-branch-details"), groupId = `paper:${paper}`;
      group.open = state.openGroups.has(groupId) ? state.openGroups.get(groupId) : true;
      group.append(element("summary", "paper-branch-title", `${shortPaperTitle(paper)} · 已记录 Worker 分支`),treeList(events));
      group.addEventListener("toggle",()=>{if(group.isConnected)state.openGroups.set(groupId,group.open);});
      branch.append(group);
      branches.append(branch);
    }
    fork.append(branches); list.append(fork);
  }
  if (common.length) {
    const shared = element("li", "tree-item shared-branch");
    shared.append(element("p", "tree-caption", "公共审核 / 恢复记录"),treeList(common));
    list.append(shared);
  }
  // 综合阶段是汇总角色，按真实事件出现；不提前生成等待中的综合节点。
  for (const event of records.filter(e => e.node_type === "Synthesis")) list.append(treeItem(event));
  return list;
}
function renderTree(actual, target) {
  const tree = element("ul", "tree-list workflow-tree");
  tree.setAttribute("aria-label", "已记录的多 Agent 协作职责树");
  const researchRoles = new Set(["Planner","Worker","Validator","Reviewer","Recovery","Synthesis"]);
  for (const event of actual.filter(e => ["QueryLanguage","PaperSelection"].includes(e.node_type))) tree.append(treeItem(event));
  const main = actual.find(e => e.node_type === "MainAgent");
  const mainItem = main ? treeItem(main) : null;
  const actions = element("ul", "tree-list main-actions");
  let researchAttached = false;
  for (const event of actual.filter(e => !researchRoles.has(e.node_type) && !["MainAgent","QueryLanguage","PaperSelection","ReportLanguage"].includes(e.node_type))) {
    const item = treeItem(event);
    if (event.node_type === "ResearchTool" && !researchAttached) {
      const branches = renderResearchBranches(actual);
      if (branches.childElementCount) item.append(branches);
      researchAttached = true;
    }
    actions.append(item);
  }
  if (mainItem) { if (actions.childElementCount) mainItem.append(actions); tree.append(mainItem); }
  else for (const item of [...actions.children]) tree.append(item);
  // 历史默认研究入口没有主 Agent / ResearchTool；仅展示历史中真实存在的研究角色。
  if (!researchAttached) {
    const branches = renderResearchBranches(actual);
    for (const item of [...branches.children]) (mainItem ? (mainItem.querySelector(".main-actions") || mainItem) : tree).append(item);
  }
  for (const event of actual.filter(e => e.node_type === "ReportLanguage")) tree.append(treeItem(event));
  target.append(tree);
}
function renderNodes() {
  state.displayNodes.clear();
  const target = $("#workflow"); target.replaceChildren();
  const actual = [...state.nodes.values()].filter((event) => event.details?.record_available !== false && !(event.node_type === "Recovery" && (event.status === "skipped" || event.details?.triggered === false)));
  // 布局依据实际研究调用/角色事件，不按问题关键词或论文数量判定协作。
  const collaborative = actual.some(event => ["ResearchTool","Planner","Worker","Reviewer","Synthesis"].includes(event.node_type));
  target.dataset.layout = collaborative ? "tree" : "linear";
  target.className = `workflow ${collaborative ? "collaboration-tree" : "timeline"}`;
  $("#workflow-layout").textContent = !actual.length ? "等待执行" : collaborative ? "多 Agent 协作 · 树状" : "单 Agent · 线性";
  $("#workflow-note").textContent = collaborative
    ? "树状图按实际角色与论文归属组织；连线不代表执行时间或同时并发。点击节点查看工作、证据与错误。"
    : "按首次记录顺序展示主 Agent 与工具；只有实际调用多 Agent 研究后才切换树状图。点击节点查看详情。";
  if (!actual.length) {
    const empty = element("div", "timeline-empty");
    empty.append(element("span", "", "○"),element("p", "", state.run ? "等待实际执行记录" : "等待研究任务"),element("p", "small muted", "节点仅由后端事件产生，不预置研究流程。"));
    target.append(empty); return;
  }
  if (collaborative) { renderTree(actual,target); return; }
  const groups = [], counts = {};
  for (const event of actual) {
    let group = groups[groups.length - 1];
    const groupType = ["Validator","Reviewer"].includes(event.node_type) ? "Checks" : event.node_type;
    if (!group || group.type !== groupType) { group = {type:groupType,records:[]}; groups.push(group); }
    group.records.push(event);
  }
  for (const group of groups) {
    const isChecks = group.type === "Checks";
    const wrapper = element(isChecks ? "details" : "section", `timeline-group${isChecks ? " review-results" : ""}`);
    wrapper.dataset.nodeType = group.type;
    if (isChecks) {
      const groupId = `${group.type}:${group.records[0].node_id}`;
      wrapper.open = group.records.some((event) => event.node_id === state.selectedNode) || (state.openGroups.has(groupId) ? state.openGroups.get(groupId) : group.records.some((event) => event.status === "running"));
      wrapper.addEventListener("toggle", () => { if (wrapper.isConnected) state.openGroups.set(groupId,wrapper.open); });
    }
    const heading = element(isChecks ? "summary" : "div", "timeline-heading", isChecks ? `引用与审核 · ${group.records.length} 条实际记录` : `${NODE_LABELS[group.type]}${group.type === "Worker" && group.records.length > 1 ? " · 独立执行" : ""}`);
    wrapper.append(heading);
    const lists = new Map();
    for (const event of group.records) {
      const type = event.node_type;
      let list = lists.get(type);
      if (!list) {
        counts[type] = (counts[type] || 0) + 1;
        list = element("div", `node-list${type === "Worker" ? " workers" : ""}`);
        list.id = `${type.toLowerCase()}-nodes${counts[type] > 1 ? `-${counts[type]}` : ""}`;
        if (isChecks) wrapper.append(element("p", "small muted review-role", NODE_LABELS[type]));
        wrapper.append(list); lists.set(type,list);
      }
      list.append(nodeCard(event));
    }
    target.append(wrapper);
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
  const heading = element("div", "detail-heading"); heading.append(element("h3", "", `${NODE_LABELS[event.node_type]} · ${event.node_type}`),badge(labelStatus(event.status),event.status));
  const records = event.records || [event];
  const first = records.find(r=>r.event_type === "started" && r.source !== "derived" && r.timestamp);
  const last = [...records].reverse().find(r=>["completed","failed","skipped"].includes(r.event_type) && r.source !== "derived" && r.timestamp);
  target.append(element("p","small muted",`开始：${first ? new Date(first.timestamp).toLocaleString("zh-CN") : "无独立记录"} · 结束：${last ? new Date(last.timestamp).toLocaleString("zh-CN") : "无独立记录"}`));
  target.append(heading,element("p", "detail-summary", ROLE_DESCRIPTIONS[event.node_type] || "仅展示公开执行事实。"));
  if (event.summary) { const original = element("details", "report-source"); original.append(element("summary", "", "原始执行摘要"),element("p", "detail-summary", event.summary)); target.append(original); }
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
  if (event.records?.length > 1) {
    const history = element("details", "report-source"); history.append(element("summary", "", `该节点的实际事件 · ${event.records.length} 条`));
    for (const record of event.records) history.append(element("p", "small muted", `#${record.event_id} · ${labelStatus(record.status)} · ${SOURCE_LABELS[record.source] || "来源无记录"}\n${record.summary || "无摘要"}`));
    target.append(history);
  }
  if (event.details?.trace) {
    target.append(element("h4", "detail-subheading", "实际执行统计")); detailRows(target,event.details.trace);
  }
  if (Array.isArray(event.details?.requirements) && event.details.requirements.length) {
    const requirements = element("details", "report-source");
    requirements.append(element("summary", "detail-subheading", `研究需求与原始提交 · ${event.details.requirements.length} 项`));
    for (const requirement of event.details.requirements) {
      const item = element("div", "evidence-card");
      item.append(element("h4", "", `${dimensionTitle(requirement.dimension_id)} · ${labelStatus(requirement.status)}`));
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
    item.append(element("span", "event-number", String(event.event_id)),element("span", "event-summary", `${NODE_LABELS[event.node_type] || event.node_type} · ${visibleSummary(event)}`),element("span", "event-source", SOURCE_LABELS[event.source] || "来源无记录"));
    item.dataset.eventId = String(event.event_id); list.append(item);
  }
  $("#event-count").textContent = `${state.events.length} 条`;
}
function numberMetric(metrics, names) { for (const name of names) if (metrics[name] !== undefined && metrics[name] !== null) return metrics[name]; return "—"; }
function renderRun() {
  if (!state.run) return;
  $("#welcome").hidden = true; $("#conversation").hidden = state.view !== "chat";
  $("#topbar-context").textContent = state.run.mode === "replay" ? "历史回放" : "当前研究";
  $("#mode-badge").textContent = state.run.mode === "replay" ? "历史回放 · 不调用模型" : "真实研究";
  $("#mode-badge").className = `badge ${state.run.mode === "replay" ? "replay" : "live"}`;
  $("#run-status").textContent = state.run.cancel_requested && hasActiveRun() ? "取消请求已提交，等待执行收尾" : state.run.mode === "replay" && state.run.status === "completed" ? "历史记录已载入" : labelStatus(state.run.status);
  $("#run-question").textContent = state.run.question || "问题无记录";
  $("#task-question").textContent = state.run.question || "问题无记录";
  const selected = state.run.paper_selection?.selected_paper_ids || state.run.paper_ids || [];
  $("#selection-result").textContent = selected.length ? `实际研究范围：${selected.map(shortPaperTitle).join("、")}` : state.run.mode === "replay" ? "研究范围以历史执行记录为准。" : "主 Agent 根据实际问题与观察决定是否选择论文。";
  if (state.run.mode !== "replay" && state.run.status === "completed" && state.run.answer_status) $("#run-status").textContent = ({answered:"已完成",partial:"部分回答",insufficient:"证据不足",failed:"执行失败"})[state.run.answer_status] || labelStatus(state.run.status);
  if (state.run.report_language_status === "pending") $("#run-status").textContent = "答案已生成，正在转换中文";
  $("#run-id").textContent = state.run.run_id;
  const metrics = {...(state.report?.metrics || {}),...(state.run.metrics || {})};
  const values = [numberMetric(metrics,["model_calls","total_model_calls"]),numberMetric(metrics,["tool_calls","total_tool_calls"]),numberMetric(metrics,["verified_requirements","verified_requirement_count"]),numberMetric(metrics,["total_latency_ms","latency_ms","wall_time_ms"])];
  const blocks = document.querySelectorAll("#metrics strong");
  values.forEach((value,index) => { blocks[index].textContent = index === 3 && typeof value === "number" ? `${(value / 1000).toFixed(1)} s` : String(value); });
  const metricLabels = state.run.mode === "replay" ? ["历史模型调用","历史工具调用","已审核需求","原执行耗时"] : ["模型调用","工具调用","已审核需求","实际耗时"];
  document.querySelectorAll("#metrics span").forEach((item,index) => { item.textContent = metricLabels[index]; });
  if (state.run.error) showError(state.run.error.message || "研究执行失败；已完成的记录仍保留。");
  rememberRun(state.run.run_id); renderReferences(); setControls();
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
function setReportLanguageControls() {
  $("#report-language-zh").classList.toggle("active",state.reportLanguage === "zh");
  $("#report-language-en").classList.toggle("active",state.reportLanguage === "en");
  $("#report-language-zh").setAttribute("aria-selected",String(state.reportLanguage === "zh"));
  $("#report-language-en").setAttribute("aria-selected",String(state.reportLanguage === "en"));
}
function renderPartialResults(target) {
  const workers = [...state.nodes.values()].filter((event) => event.node_type === "Worker");
  const submitted = workers.flatMap((event) => event.details?.requirements || []).filter((item) => item.claim);
  if (submitted.length) {
    target.append(element("h4", "report-section-title", "已保存的过程提交（不等同于审核通过）"));
    for (const item of submitted) {
      const card = element("div", "missing-card");
      card.append(element("strong", "", `${shortPaperTitle(item.paper_id)} · ${item.dimension_id || "维度无记录"} · ${labelStatus(item.status)}`),element("p", "evidence-text", typeof item.claim === "string" ? item.claim : item.claim.text || "论断正文无记录"));
      target.append(card);
    }
  }
  if (state.run?.error) target.append(element("div", "error-banner", state.run.error.message || "研究执行失败，已保存节点可继续查看。"));
}
function renderReport() {
  setReportLanguageControls();
  const original = state.report, target = $("#report-details"), notice = $("#report-language-notice");
  const language = original?.language || {};
  const useChinese = state.reportLanguage === "zh" && Boolean(original?.chinese_report);
  const report = useChinese ? original.chinese_report : original;
  if (state.reportLanguage === "en") notice.textContent = "英文原始输出：回答、报告和引用完整保留；原始模型输出不等同于人工已核实。";
  else if (useChinese) notice.textContent = state.run?.mode === "replay" ? "历史回放中文阅读版：不调用模型，不修改原始记录；翻译未经独立人工事实审核。" : "中文阅读版：仅转换可读文本，原始引用与审核状态保持不变；翻译不等于独立人工审核。";
  else if (language.status === "failed") notice.textContent = `中文转换失败，已回退权威原文。${language.issue_code ? `错误类别：${language.issue_code}。` : ""}原研究结果未被改写。`;
  else if (language.status === "pending" || state.run?.report_language_status === "pending") notice.textContent = "研究已完成，正在生成中文版本；当前先展示权威原文。";
  else if (original) notice.textContent = "当前展示权威原文；尚无可用中文阅读版。翻译不会补充研究事实。";
  else notice.textContent = "中文版本仅用于阅读，权威原报告与引用保持不变。";
  if (!report || report.status === "report_unavailable") {
    const active = hasActiveRun();
    emptyPanel(target,state.run ? active ? "研究进行中" : `当前任务：${labelStatus(state.run.status)}` : "尚无研究报告",active ? "真实研究报告完成后将在此显示；执行过程可在中间查看。" : "尚无可用最终报告。已保存的真实过程结果和错误仍可查看。");
    if (state.run) renderPartialResults(target);
    return;
  }
  target.className = ""; target.replaceChildren();
  target.append(element("h3", "report-heading", report.title || "实际研究报告"),badge(labelStatus(report.status),report.status === "completed" ? "completed" : "skipped"));
  target.append(element("p", "report-notice", "模型输出与论断审核通过不等于独立人工事实审核。缺失与拒绝状态不会因翻译改变。"));
  if (original.provenance) {
    target.append(element("p", "report-notice", `报告来源：${original.provenance.case_id || original.provenance.source || "本次研究记录"}。${original.provenance.note || ""}`));
    const sources = original.provenance.source_files || [];
    if (sources.length) {
      const origin = element("details", "report-source"); origin.append(element("summary", "small muted", "原始文件与校验值"));
      for (const source of sources) origin.append(element("p", "evidence-id", `${source.path || "来源路径无记录"}\nSHA256: ${source.sha256 || "无记录"}`));
      target.append(origin);
    }
  }
  if (report.direct_answer) {
    const direct = element("section", "direct-answer"); direct.id = "direct-answer";
    direct.append(element("h4", "report-section-title", "直接回答"), answerProse(report.direct_answer));
    target.append(direct);
    if (report.execution_mode) target.append(element("p", "small muted", `实际路径：${({direct:"直接回答",tools:"工具辅助",multi_agent_research:"多 Agent 研究",historical_replay:"历史回放"})[report.execution_mode] || report.execution_mode}`));
    const findings = report.key_findings || [];
    if (findings.length) target.append(element("h4", "report-section-title", "回答要点与来源"));
    for (const finding of findings) {
      const card = element("article", "claim-card"), excerpt = finding.text.length > 240 ? finding.text.slice(0,240) + "…" : finding.text;
      card.append(element("p", "evidence-text", excerpt), element("p", "small muted", `${shortPaperTitle(finding.paper_id)} · ${finding.review_status === "passed" ? "原论断经模型审核" : "当前事实未独立审核"}${finding.text.length > 240 ? " · 原文节选" : ""}`));
      if (finding.text.length > 240) { const full=element("details","report-source");full.append(element("summary","small muted","展开原论断"),element("p","evidence-text",finding.text));card.append(full); }
      const raw = finding.claim_id ? (original.verified_claims||[]).find(c=>c.claim_id===finding.claim_id) : (original.key_findings||[]).find(f=>f.evidence_ids?.join()===finding.evidence_ids?.join());
      if (raw?.evidence?.length) { const button=element("button","text-button","查看原始证据"); button.type="button";button.addEventListener("click",()=>{renderEvidence(raw.evidence,finding.claim_id ? raw : null);selectTab("evidence-panel");});card.append(button); }
      target.append(card);
    }
    for (const citation of report.citations || []) target.append(element("p", "citation-row", `${shortPaperTitle(citation.paper_id)} · 第 ${citation.page} 页${citation.figure_id ? ` · ${citation.figure_id}` : ""}`));
  }
  const claims = report.verified_claims || [];
  if (claims.length && !(report.direct_answer && report.key_findings?.length)) {
    target.append(element("h4", "report-section-title", `已审核论断 · ${claims.length} 项`));
    for (const claim of claims) {
      const card = element("article", "claim-card");
      card.append(element("h4", "", `${shortPaperTitle(claim.paper_id)} · ${dimensionTitle(claim.dimension_id,report)}`),element("p", "evidence-text", claim.text));
      const sources = (claim.evidence || []).map((item) => `${shortPaperTitle(item.paper_id)} · 第 ${item.page ?? "无记录"} 页`).join("；");
      card.append(element("p", "citation-row", sources || "引用记录缺失"));
      const button = element("button", "text-button", "查看原始引用证据"); button.type = "button";
      const originalClaim = (original.verified_claims || []).find((item) => item.claim_id === claim.claim_id) || claim;
      button.addEventListener("click", () => showClaimEvidence(originalClaim)); card.append(button); target.append(card);
    }
  } else if (!report.direct_answer) target.append(element("p", "missing-card", "当前没有足够已审核证据，不能补充论文技术事实。"));
  const missing = report.missing_requirements || [];
  if (missing.length) {
    target.append(element("h4", "report-section-title", `未完成或证据缺失 · ${missing.length} 项`));
    for (const item of missing) target.append(element("div", "missing-card", `${shortPaperTitle(item.paper_id)} · ${dimensionTitle(item.dimension_id,report)}\n${labelStatus(item.status)}${item.reason_code || item.reason ? ` · ${item.reason_code || item.reason}` : ""}`));
  }
  const fullReport = element("details", "report-source"); fullReport.id = "report-full-text";
  fullReport.append(element("summary", "report-section-title", useChinese ? "中文阅读版全文" : "权威原报告全文"));
  // 报告文本包含模型输出，始终当作纯文本，不执行 Markdown 内的任意 HTML。
  fullReport.append(element("pre", "report-text", report.markdown || "报告全文无记录。"));
  if ((report.limitations || []).length) {
    target.append(element("h4", "report-section-title", "研究限制")); const list = element("ul", "limitations-list");
    for (const limitation of report.limitations) list.append(element("li", "", limitation)); target.append(list);
  }
  if (report.detailed_report || !report.direct_answer) target.append(fullReport);
  const direct = target.querySelector("#direct-answer");
  if (direct) {
    const detail = element("details","report-source report-expanded"); detail.id = "report-expanded";
    detail.append(element("summary","report-section-title","详细回答、引用与研究限制"));
    for (const child of [...target.children]) if (child !== direct) detail.append(child);
    target.replaceChildren(direct,detail);
  }
  renderReferences();
}
function selectTab(panelId) {
  if (panelId === "report-panel") { const full = $("#report-full-text"); if (full) full.open = true; const expanded = $("#report-expanded"); if(expanded) expanded.open = true; $("#result-title").scrollIntoView({block:"start"}); return; }
  document.querySelectorAll(".tab").forEach((button) => { const active = button.dataset.panel === panelId; button.classList.toggle("active",active); button.setAttribute("aria-selected",String(active)); });
  document.querySelectorAll(".detail-content").forEach((panel) => { panel.hidden = panel.id !== panelId; });
  $("#process-title").scrollIntoView({block:"start"});
}
// 网络与本机记录只维护可观察状态，绝不在刷新或连接恢复时重新创建任务。
const HISTORY_KEY = "personal-agent-workbench-bookmarks-v1";
const PENDING_KEY = "personal-agent-workbench-pending-v1";
function readLocal(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
function writeLocal(key, value) { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key,JSON.stringify(value)); } catch { /* 隐私模式只影响本机书签，不影响当前执行。 */ } }
function connection(text, offline = false) { $("#connection-status").textContent = text; $("#connection-status").classList.toggle("offline",offline); }
function setView(view) {
  state.view = view;
  for (const name of ["history","library","environment","memory"]) $(`#${name}-view`).hidden = name !== view;
  $("#welcome").hidden = view !== "chat" || Boolean(state.run);
  $("#conversation").hidden = view !== "chat" || !state.run;
  document.querySelectorAll(".nav-item").forEach(button=>button.classList.toggle("active",button.dataset.view === view));
  $("#main-scroll").scrollTop = 0;
  if (view === "memory") window.MemoryView?.show();
}
window.setWorkbenchView = setView;
function stopPolling() { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; }
function rememberRun(id) {
  try { localStorage.setItem(STORAGE_KEY,id); } catch { /* 本机书签不可用时不阻断读取。 */ }
  if (state.run?.run_id !== id) return;
  const bookmarks = readLocal(HISTORY_KEY,[]).filter(item=>item.run_id !== id);
  bookmarks.unshift({run_id:id,question:state.run.question || "问题无记录",status:state.run.status,mode:state.run.mode});
  writeLocal(HISTORY_KEY,bookmarks.slice(0,20)); renderBookmarks();
}
function renderBookmarks() {
  const bookmarks = readLocal(HISTORY_KEY,[]);
  for (const selector of ["#recent-runs","#history-runs"]) {
    const target = $(selector);target.replaceChildren();
    for (const record of bookmarks) {
      if (!/^[a-zA-Z0-9_-]{1,128}$/u.test(record.run_id || "")) continue;
      const button = element("button",`recent-run${state.run?.run_id === record.run_id ? " active" : ""}`,record.question);
      button.type = "button";button.title = record.question;
      if (selector === "#history-runs") button.append(element("small","",`${record.mode === "replay" ? "归档回放" : "研究任务"} · 书签状态：${labelStatus(record.status)} · 点击读取服务端最新状态`));
      button.addEventListener("click",()=>loadRun(record.run_id));target.append(button);
    }
    if (!target.children.length) target.append(element("p","small muted","尚无本机任务记录。这里不会列出其他浏览器或其他用户的任务。"));
  }
}
function renderReferences() {
  const target = $("#reference-papers"), report = state.report || {}, papers = new Map();
  const add = (id,kind,meta={}) => {
    if (!id) return;
    const previous = papers.get(id); papers.set(id,{...previous,...meta,paper_id:id,kind:previous?.kind === "正文引用" ? previous.kind : kind});
  };
  for (const citation of report.citations || []) add(citation.paper_id,"正文引用");
  for (const claim of report.verified_claims || []) add(claim.paper_id,"正文引用");
  for (const finding of report.key_findings || []) if(finding.evidence_ids?.length)add(finding.paper_id,"正文引用");
  for (const source of report.source_references || []) if(source.source_kind === "paper_metadata")add(source.paper_id,"候选元数据",source);
  for (const event of state.events) {
    for (const candidate of event.details?.papers || event.details?.candidates || []) add(candidate.paper_id,"候选元数据",candidate);
  }
  for (const id of state.run?.paper_selection?.selected_paper_ids || state.run?.paper_ids || []) if(!papers.has(id))add(id,"研究范围 · 引用待核对");
  if (!papers.size) { target.replaceChildren(); const box=element("div","resource-empty");box.append(element("span","","▤"),element("h3","",state.run ? "尚无论文来源记录" : "等待研究资料"),element("p","","采用的论文和候选元数据在取得真实记录后出现。"));target.append(box);return; }
  target.replaceChildren();
  for (const paper of papers.values()) {
    const card=element("article","reference-card");card.dataset.paperId=paper.paper_id;
    card.append(element("span","reference-kind",paper.kind),element("h3","",paper.title || paperTitle(paper.paper_id)));
    if (paper.year || paper.venue)card.append(element("p","",[paper.year,paper.venue].filter(Boolean).join(" · ")));
    if (Array.isArray(paper.authors) && paper.authors.length)card.append(element("p","",paper.authors.join("、")));
    const citations=(report.citations||[]).filter(c=>c.paper_id === paper.paper_id);
    if(citations.length)card.append(element("p","",`引用页码：${[...new Set(citations.map(c=>c.page))].join("、")}`));
    if(paper.kind === "候选元数据")card.append(element("p","","元数据仅供发现论文，不能作为正文证据。"));
    const url=ResearchUI.safeLink(paper.source_url || paper.url);
    if(url){const link=element("a","","查看原始来源 ↗");link.href=url;link.target="_blank";link.rel="noopener noreferrer";card.append(link);}
    const snapshots=[...(report.verified_claims||[]),...(report.key_findings||[])].filter(c=>c.paper_id === paper.paper_id).flatMap(c=>c.evidence || []);
    if(snapshots.length){const button=element("button","text-button","查看引用证据 →");button.type="button";button.addEventListener("click",()=>{renderEvidence(snapshots,null);selectTab("evidence-panel");});card.append(button);}
    if(!url && !snapshots.length)card.append(element("p","","当前接口未提供来源链接或正文快照。"));
    target.append(card);
  }
}
function showPending(pending) { state.pending=pending;writeLocal(PENDING_KEY,pending);$("#submission-recovery").hidden=!pending;setControls(); }
function resetRun(run) {
  stopPolling();state.epoch++;
  Object.assign(state,{run,events:[],ledger:new ResearchUI.EventLedger(),cursor:0,selectedNode:null,report:null,reportFetched:false,reportLanguage:"zh",eventTruncated:false,readErrors:0,gapReads:0});
  state.seen.clear();state.nodes.clear();state.openGroups.clear();
  $("#event-count").textContent="0 条";
  emptyPanel($("#node-details"),"等待节点记录","点击流程节点查看真实执行详情。");
  emptyPanel($("#evidence-details"),"尚无证据","等待本次记录中的引用证据。");
  showError("");$("#event-warning").hidden=true;
  history.replaceState(null,"",`${location.pathname}?run=${encodeURIComponent(run.run_id)}`);
  setView("chat");rememberRun(run.run_id);renderRun();renderNodes();renderEvents();renderReport();
}
async function loadRun(identity) {
  if (hasActiveRun() && state.run.run_id !== identity) return showError("请先等待或取消当前任务，再读取其他历史记录。");
  connection("正在读取历史");
  try { const run=await request(`/runs/${encodeURIComponent(identity)}`);resetRun(run);await pollRun(); }
  catch(error){connection("读取未完成",true);showError(error.message);}
}
async function pollRun() {
  if (!state.run || state.polling) return;
  const runId=state.run.run_id,epoch=state.epoch;state.polling=true;
  let retryRead=false,moreEvents=false;
  try {
    const [run,batch]=await Promise.all([request(`/runs/${encodeURIComponent(runId)}`),request(`/runs/${encodeURIComponent(runId)}/events?after=${state.cursor}`)]);
    if(epoch!==state.epoch)return;
    state.run=run;
    const merged=state.ledger.merge(runId,batch,run);state.events=merged.events;state.cursor=merged.cursor;
    moreEvents=state.cursor<state.ledger.highWater;
    if(merged.gap)state.gapReads++;else state.gapReads=0;
    const warnings=[];
    if(merged.truncated)warnings.push("早期事件已超出服务保留范围；仅展示仍可取得的真实记录。");
    if(merged.gap)warnings.push(state.gapReads<4 ? "事件序号暂不连续，正在从最后连续序号重新读取；不会补画缺失节点。" : "事件仍不连续，已暂停自动补读；点击顶部连接状态可只读重试。");
    if(merged.conflict)warnings.push("相同序号出现不同记录，保留首次收到的事件，需核对服务端原始记录。");
    $("#event-warning").textContent=warnings.join(" ");$("#event-warning").hidden=!warnings.length;
    rebuildNodes();renderEvents();renderNodes();renderRun();
    if(state.selectedNode && state.displayNodes.has(state.selectedNode))renderNodeDetails(state.displayNodes.get(state.selectedNode));
    if(run.report_available && (!state.reportFetched || state.report?.language?.status === "pending" || run.report_language_status === "pending")) {
      const report=await request(`/runs/${encodeURIComponent(runId)}/report`);if(epoch!==state.epoch)return;
      state.report=report;state.reportFetched=true;renderReport();renderRun();
    }
    if(!state.report)renderReport();
    state.readErrors=0;connection(hasActiveRun() ? "已连接 · 研究运行中" : "记录已同步");
  } catch(error) {if(epoch===state.epoch){state.readErrors++;retryRead=error.status!==404 && state.readErrors<5;connection("连接中断 · 保留已有记录",true);showError(error.message);}}
  finally {
    state.polling=false;
    const translationPending=state.run?.report_language_status === "pending" || state.report?.language?.status === "pending";
    // 终态且记录已齐时停止；失败读取和缺口只做有界 GET 重读，永不重发创建请求。
    if(epoch===state.epoch && state.readErrors<5 && (hasActiveRun() || retryRead || translationPending || moreEvents && state.gapReads<4)) state.pollTimer=setTimeout(pollRun,state.readErrors ? 2500 : 1000);
    else if(epoch!==state.epoch && state.run)state.pollTimer=setTimeout(pollRun,0);
  }
}
async function startRun(event) {
  event.preventDefault();
  if(state.posting || state.pending || hasActiveRun() || !(state.health?.main_agent_ready ?? state.health?.real_research_ready))return;
  const question=$("#question").value.trim(),budget=Number($("#model-budget").value);
  if(!question)return showError("请输入研究问题。");
  if(!Number.isInteger(budget)||budget<1||budget>200)return showError("模型调用上限必须为 1–200 的整数。");
  const pending={key:crypto.randomUUID(),body:{question,language_mode:$("#original-language").checked ? "original":"auto",max_model_calls:budget}};
  showPending(pending);state.posting=true;setControls();showError("");connection("正在提交研究");
  try {
    const run=await request("/runs",{method:"POST",headers:{"Idempotency-Key":pending.key},body:JSON.stringify(pending.body)});
    showPending(null);resetRun(run);await pollRun();
  } catch(error) {
    const ambiguous=!error.status || ["invalid_response","submission_outcome_unknown","task_scheduling_outcome_unknown","run_persistence_incomplete"].includes(error.code) || error.retryMode === "reconcile_only";
    if(!ambiguous)showPending(null);
    if(error.runId)pending.run_id=error.runId;
    if(ambiguous){showPending(pending);showError(`${error.message}\n结果未知不等于回滚。保留原问题和提交标识，仅允许核对。`);}
    else showError(error.message);
    connection(ambiguous ? "提交待核对" : "请求未完成",true);
  } finally {state.posting=false;setControls();}
}
async function reconcileSubmission() {
  const pending=state.pending;if(!pending||state.posting)return;
  state.posting=true;setControls();$("#reconcile-button").disabled=true;
  try {
    // 现有 Replay-Only 契约保证此 POST 只查询幂等结果，没有首次创建资格。
    const run=await request("/runs",{method:"POST",headers:{"Idempotency-Key":pending.key,"Idempotency-Replay-Only":"true"},body:JSON.stringify(pending.body)});
    showPending(null);resetRun(run);await pollRun();
  } catch(error){showError(`${error.message}\n原提交仍保留；没有重新安排任务。`);if(pending.run_id)await loadRun(pending.run_id);}
  finally{state.posting=false;$("#reconcile-button").disabled=false;setControls();}
}
async function startReplay() {
  if(state.posting || state.pending || hasActiveRun() || !$("#replay-select").value)return;
  state.posting=true;setControls();showError("");
  try{resetRun(await request(`/replays/${encodeURIComponent($("#replay-select").value)}`,{method:"POST",body:"{}"}));await pollRun();}
  catch(error){showError(error.message);}finally{state.posting=false;setControls();}
}
async function cancelRun() {
  if(!hasActiveRun() || state.run.cancel_requested)return;
  $("#cancel-button").disabled=true;
  try{const epoch=state.epoch,id=state.run.run_id;const run=await request(`/runs/${encodeURIComponent(id)}/cancel`,{method:"POST",body:"{}"});if(epoch!==state.epoch)return;state.run=run;renderRun();stopPolling();await pollRun();}
  catch(error){showError(error.message);setControls();}
}
function newResearch() {
  if(state.posting || state.pending || hasActiveRun())return;
  stopPolling();state.epoch++;state.run=null;state.report=null;state.events=[];state.nodes.clear();
  try{localStorage.removeItem(STORAGE_KEY);}catch{/* 不影响页面。 */}
  history.replaceState(null,"",location.pathname);
  $("#question").value="";$("#topbar-context").textContent="新对话";showError("");setView("chat");renderReferences();setControls();$("#question").focus();
}
async function initialize() {
  $("#research-form").addEventListener("submit",startRun);
  $("#question").addEventListener("input",setControls);
  $("#question").addEventListener("keydown",event=>{if(event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229){event.preventDefault();$("#research-form").requestSubmit();}});
  $("#cancel-button").addEventListener("click",cancelRun);
  $("#replay-button").addEventListener("click",startReplay);
  $("#replay-select").addEventListener("change",setControls);
  $("#refresh-health").addEventListener("click",refreshHealth);
  $("#reconcile-button").addEventListener("click",reconcileSubmission);
  $("#new-research").addEventListener("click",newResearch);
  document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>setView(button.dataset.view)));
  document.querySelectorAll("[data-question]").forEach(button=>button.addEventListener("click",()=>{$("#question").value=button.dataset.question;setControls();$("#question").focus();}));
  document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>selectTab(button.dataset.panel)));
  $("#tab-report").addEventListener("click",()=>selectTab("report-panel"));
  $("#show-process").addEventListener("click",()=>{$("#workflow-section").open=true;$("#workflow-section").scrollIntoView({block:"start"});});
  $("#report-language-zh").addEventListener("click",()=>{state.reportLanguage="zh";renderReport();});
  $("#report-language-en").addEventListener("click",()=>{state.reportLanguage="en";renderReport();});
  $("#connection-status").addEventListener("click",()=>{if(state.run){stopPolling();pollRun();}});
  const pending=readLocal(PENDING_KEY,null);if(pending?.key && pending?.body?.question)showPending(pending);
  const results=await Promise.allSettled([request("/papers"),request("/health"),request("/replays"),fetch("/api/storage/health").then(r=>r.ok?r.json():null)]);
  if(results[0].status === "fulfilled")state.papers=results[0].value.catalog?.length ? results[0].value.catalog : results[0].value.papers || [];
  if(results[1].status === "fulfilled")state.health=results[1].value;
  if(results[2].status === "fulfilled")state.replays=results[2].value.cases || [];
  if(results[3].status === "fulfilled")state.storage=results[3].value;
  $("#storage-label").textContent=state.storage?.status === "ready" ? "数据库连接可用 · 历史以提交结果为准" : state.storage?.status === "disabled" ? "进程内模式 · 重启不保证保留" : "存储状态未确认";
  renderPapers();renderHealth();renderReplays();renderNodes();renderBookmarks();connection("已连接");
  const failure=results.slice(0,3).find(result=>result.status === "rejected");if(failure){showError(failure.reason.message);connection("部分服务不可用",true);}
  let saved;try{saved=localStorage.getItem(STORAGE_KEY);}catch{/* 无书签可恢复。 */}
  const linked=new URLSearchParams(location.search).get("run");
  const identity=linked || saved;
  if(identity && /^[a-zA-Z0-9_-]{1,128}$/u.test(identity))await loadRun(identity);
}
initialize();
