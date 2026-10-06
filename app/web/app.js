"use strict";
const examples = {
  text: "What training objective does CLIP use to align images and text?",
  figure: "Figure 3 为什么证明 BLIP 性能更强？请核实问题前提。",
  recheck: "Why does Flamingo use the Perceiver Resampler?",
  insufficient: "What is the exact SHA-256 of the private dataset manifest used to train BLIP, and the number of duplicate files removed during preprocessing? Use only the prepared corpus."
};
const labels = {completed:"回答已生成",insufficient_evidence:"证据不足",step_exhaustion:"工具预算已耗尽",repeated_failure:"重复失败，已停止",model_failure:"模型请求失败",protocol_failure:"动作协议不符，已停止"};
const statusLabels = {success:"成功",empty:"无证据",error:"失败"};
// 统一元素查找；下面的 DOM 辅助函数用 textContent 写入文本，避免把模型输出当 HTML 执行。
const el = id => document.getElementById(id);
function node(tag, text, cls) { const item=document.createElement(tag); if(text!==undefined)item.textContent=text; if(cls)item.className=cls; return item; }
function paperName(id) { return (id || "").split("-")[0].toUpperCase(); }
function sourceLabel(c) { return `${paperName(c.paper_id)} · Page ${c.page}${c.figure_id ? ` · ${c.figure_id}` : ""}`; }
// 组合查询、论文、页码与图编号，过滤缺失字段后形成步骤摘要。
function description(step) { const a=step.arguments; return [a.query, a.paper_id ? paperName(a.paper_id) : null, a.paper_ids?.length ? a.paper_ids.map(paperName).join(", ") : null, a.page ? `Page ${a.page}` : null, a.figure_id].filter(Boolean).join(" · ") || (step.tool_name==="finish" ? "提交最终答案与证据引用" : "按证据 ID 执行"); }
// 按公开 DTO 更新答案、引用、图像、Trace 和指标；不读取模型内部消息。
function renderResult(data) {
  el("result").hidden=false; el("empty-state").hidden=true;
  const badge=el("run-status");badge.textContent=labels[data.status] || data.status;badge.dataset.status=data.status;
  el("retrieval-mode").textContent=data.retrieval_mode==="day5-controlled" ? "当前检索：受控演示" : data.retrieval_mode==="test" ? "测试模型演示" : "当前检索：语义检索";
  el("answer").textContent=data.answer || "本次没有生成最终答案，请查看限制说明与执行轨迹。";
  const citations=el("citations");citations.replaceChildren();
  if(!data.citations.length)citations.append(node("p","没有最终引用。"));
  for(const c of data.citations){const card=node("div",sourceLabel(c),"source");card.append(node("small",c.paper_id));citations.append(card);}
  el("limitations-section").hidden=!data.limitations.length;el("limitations").replaceChildren(...data.limitations.map(t=>node("li",t)));
  el("stop-section").hidden=!Object.keys(data.stop_details || {}).length;el("stop-details").textContent=JSON.stringify(data.stop_details || {},null,2);
  // 图像的可见观察与解释分开展示，尚未分析的图片也保留明确状态。
  const figures=el("figures");figures.replaceChildren();el("figures-section").hidden=!data.figures.length;
  for(const f of data.figures){const card=node("article",undefined,"figure-card");const img=node("img");img.src=f.image_url;img.alt=sourceLabel(f);img.loading="lazy";card.append(img,node("p",sourceLabel(f),"figure-meta"),node("p",f.caption,"caption"));
    if(f.analysis){const analysis=node("div",undefined,"analysis");analysis.append(node("h3","Visual observations"),node("ul"));analysis.lastChild.append(...f.analysis.observations.map(t=>node("li",t)));analysis.append(node("h3","Interpretation"),node("p",f.analysis.interpretation));if(f.analysis.limitations.length){analysis.append(node("h3","Analysis limitations"),node("ul"));analysis.lastChild.append(...f.analysis.limitations.map(t=>node("li",t)));}card.append(analysis);}
    else card.append(node("p","本次已取得图像证据，尚未执行视觉分析。", "not-analyzed"));figures.append(card);}
  // Trace 只显示动作、耗时、证据与失败信息；未执行原因由后端 failure 提供。
  const trace=el("trace");trace.replaceChildren();
  if(!data.trace.length)trace.append(node("li","没有已执行的工具动作。"));
  for(const step of data.trace){const row=node("li",undefined,"trace-step");const line=node("div",undefined,"trace-line");const status=node("span",statusLabels[step.status] || step.status,"step-status");status.dataset.status=step.status;
    line.append(node("span",String(step.step),"step-number"),node("span",step.tool_name,"tool-name"),status,node("span",step.latency_ms===null ? "—" : `${step.latency_ms.toFixed(0)} ms`,"step-latency"));row.append(line,node("p",description(step),"step-description"));
    if(step.failure)row.append(node("p",step.failure.message || step.failure.issue_code || "调用未成功。","failure"));
    const details=node("details");details.append(node("summary",`${step.evidence_ids.length} Evidence · 查看参数与返回信息`),node("pre",JSON.stringify({arguments:step.arguments,evidence_ids:step.evidence_ids,...(step.failure?{failure:step.failure}:{})},null,2)));row.append(details);trace.append(row);}
  // 毫秒转换为秒只影响展示，真实工具和模型调用数直接使用后端指标。
  const m=data.metrics;const metrics=el("metrics");metrics.replaceChildren();
  for(const [value,label] of [[m.tool_calls,"Tools"],[m.model_calls,"Model Calls"],[m.vlm_calls,"VLM Calls"],[(m.total_latency_ms/1000).toFixed(2),"s Total"]]){const item=node("span");item.append(node("strong",String(value)),document.createTextNode(label));metrics.append(item);}
}
for(const button of document.querySelectorAll("[data-example]"))button.addEventListener("click",()=>{el("question").value=examples[button.dataset.example];el("question").focus();});
// 提交期间禁用按钮；成功、失败都在 finally 恢复，允许用户修正后再问。
el("ask-form").addEventListener("submit",async event=>{event.preventDefault();const question=el("question").value.trim();if(!question){el("api-error").hidden=false;el("api-error").textContent="请输入问题。";return;}
  el("ask-button").disabled=true;el("ask-button").textContent="正在运行…";el("request-state").textContent="Agent 正在执行，完成后一次性展示结果与轨迹。";el("api-error").hidden=true;el("result").hidden=true;el("empty-state").hidden=true;
  try{const response=await fetch("/api/agent/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question})});const data=await response.json();if(!response.ok)throw new Error(data.error?.message || `请求失败（HTTP ${response.status}）。`);renderResult(data);el("request-state").textContent="本次运行已结束。";}
  catch(error){el("api-error").textContent=error.message || "无法连接本地服务。";el("api-error").hidden=false;el("request-state").textContent="本次请求未完成。";}
  finally{el("ask-button").disabled=false;el("ask-button").replaceChildren(document.createTextNode("Ask Agent "),node("span","↗"));}
});
