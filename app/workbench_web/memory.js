/* 项目记忆只读视图：服务端内容仅按文本显示，不作为论文证据或指令。 */
"use strict";
window.MemoryView = (() => {
  const $ = selector => document.querySelector(selector);
  const state = {page:1, pageSize:20, generation:0, detailGeneration:0};
  const types = {preference:"用户偏好", project_decision:"项目决策", project_fact:"项目事实", workflow_lesson:"工作经验", research_context:"研究背景"};
  const statuses = {active:"有效", superseded:"已替代", expired:"已过期", deleted:"已删除"};
  const text = (tag, value, cls) => { const node = document.createElement(tag); if (cls) node.className = cls; node.textContent = value ?? "—"; return node; };
  function date(value) { if (!value) return "—"; const t = new Date(value); return Number.isNaN(t.valueOf()) ? String(value) : t.toLocaleString("zh-CN"); }
  async function get(url) {
    let response;
    try { response = await fetch(url, {headers:{"Accept":"application/json"}}); }
    catch { throw new Error("连接失败，请确认服务可用后重试。"); }
    if (!response.ok) {
      let body = {}; try { body = await response.json(); } catch {}
      const code = body?.error?.code || body?.detail?.code || body?.code;
      if (code === "memory_disabled") throw new Error("项目记忆尚未启用，请先按文档配置。");
      throw new Error("记忆服务暂不可用，请稍后重试。");
    }
    try { return await response.json(); } catch { throw new Error("服务响应无效，请稍后重试。"); }
  }
  function params() {
    const values = new URLSearchParams({page:String(state.page), page_size:String(state.pageSize)});
    const query = $("#memory-query").value.trim(), type = $("#memory-type").value, status = $("#memory-status").value;
    if (query) values.set("query", query);
    if (type) values.set("memory_type", type);
    if (status) values.set("status", status);
    return values;
  }
  function detail(record) {
    const target = $("#memory-detail"); target.replaceChildren();
    target.append(text("h2","记忆详情"), text("p",record.content,"memory-content"));
    const list = document.createElement("dl"); list.className = "memory-details";
    const entries = [["记忆 ID",record.memory_id],["逻辑键",record.memory_key],["类型",types[record.memory_type] || record.memory_type],["状态",statuses[record.effective_status || record.status] || record.effective_status || record.status],["标签",(record.tags || []).join("、") || "—"],["来源",record.source_type],["来源标识",record.source_ref],["作用范围",record.scope_id],["创建时间",date(record.created_at)],["更新时间",date(record.updated_at)],["过期时间",date(record.expires_at)],["替代此前记录",record.supersedes_id || "—"],["后继记录",record.superseded_by_id || "—"]];
    for (const [name, value] of entries) list.append(text("dt",name),text("dd",value));
    target.append(list);
  }
  async function showDetail(id) {
    const target = $("#memory-detail"), generation=++state.detailGeneration; target.replaceChildren(text("p","正在读取详情…","muted"));
    try { const record=await get("/api/memories/" + encodeURIComponent(id)); if(generation===state.detailGeneration) detail(record); }
    catch (error) { if(generation===state.detailGeneration) target.replaceChildren(text("p",error.message,"memory-error")); }
  }
  function list(data) {
    if (!Array.isArray(data.items) || !Number.isInteger(data.total)) throw new Error("记忆列表响应无效，请稍后重试。");
    const target=$("#memory-list"); target.replaceChildren();
    if (!data.items.length) target.append(text("p",state.page === 1 ? "暂无记忆。Agent 产生并保存可复用的信息后，相关记录会显示在这里。" : "当前页没有匹配的记忆。","muted"));
    for (const item of data.items) {
      const card=document.createElement("button"); card.type="button"; card.className="memory-card";
      card.append(text("strong",item.memory_key || item.content),text("span",item.content,"memory-preview"),text("small",[types[item.memory_type] || item.memory_type,statuses[item.effective_status || item.status] || item.effective_status || item.status,date(item.updated_at),item.source_type].filter(Boolean).join(" · ")));
      card.addEventListener("click",()=>showDetail(item.memory_id)); target.append(card);
    }
    $("#memory-page-label").textContent = "第 " + state.page + " 页 · 共 " + data.total + " 条";
    $("#memory-prev").disabled = state.page <= 1;
    $("#memory-next").disabled = state.page * state.pageSize >= data.total;
  }
  function stats(data) {
    const target=$("#memory-stats"); target.replaceChildren();
    for (const [key,label] of [["total","全部"],["active","有效"],["superseded","已替代"],["expired","已过期"],["deleted","已删除"]]) {
      const item=document.createElement("div"); item.append(text("strong",data[key] ?? "—"),text("span",label)); target.append(item);
    }
  }
  async function refresh() {
    const generation=++state.generation; state.detailGeneration++;
    $("#memory-detail").replaceChildren(text("h2","记忆详情"),text("p","选择一条记忆查看内容和来源。","muted"));
    $("#memory-list").replaceChildren(text("p","正在读取记忆…","muted"));
    $("#memory-stats").replaceChildren(text("p","正在读取统计…","muted"));
    $("#memory-list-error").hidden = true; $("#memory-stats-error").hidden = true;
    const [listResult,statsResult]=await Promise.allSettled([get("/api/memories?"+params()),get("/api/memories/stats")]);
    if (generation !== state.generation) return;
    if (listResult.status === "fulfilled") {
      try { list(listResult.value); } catch (error) { $("#memory-list").replaceChildren(); $("#memory-list-error").textContent=error.message; $("#memory-list-error").hidden=false; }
    } else { $("#memory-list").replaceChildren(); $("#memory-list-error").textContent=listResult.reason.message; $("#memory-list-error").hidden=false; }
    if (statsResult.status === "fulfilled") stats(statsResult.value);
    else { $("#memory-stats").replaceChildren(); $("#memory-stats-error").textContent=statsResult.reason.message; $("#memory-stats-error").hidden=false; }
  }
  function init() {
    $("#memory-filter").addEventListener("submit",event=>{event.preventDefault();state.page=1;refresh();});
    for (const selector of ["#memory-type","#memory-status"]) $(selector).addEventListener("change",()=>{state.page=1;refresh();});
    $("#memory-refresh").addEventListener("click",refresh);
    $("#memory-prev").addEventListener("click",()=>{if(state.page>1){state.page--;refresh();}});
    $("#memory-next").addEventListener("click",()=>{state.page++;refresh();});
    $("#memory-back").addEventListener("click",()=>window.setWorkbenchView("chat"));
  }
  init();
  return {show:refresh};
})();
