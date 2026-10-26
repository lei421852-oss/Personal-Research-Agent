/* 浏览器边界适配：保留错误语义、只读核对及有序事件，不参与 Agent 决策。 */
"use strict";
(function (global) {
  const MESSAGES = {
    submission_outcome_unknown:"提交结果未知。任务可能已保存；请核对原提交，不要重新创建。",
    task_scheduling_outcome_unknown:"任务安排结果尚未确定；只读核对不会重新执行研究。",
    run_not_attached:"任务记录存在，但当前实例未接管。需要维护者核对，不会自动重跑。",
    run_persistence_incomplete:"持久化收尾尚未确认；已保存内容与内存结果可能不同，请先核对。",
    duplicate_run:"已有相同内容的活跃任务，本次没有创建第二个任务。",
    idempotency_key_conflict:"这个提交标识对应不同请求，不能自动重放。",
    submission_not_found:"未查到原提交记录。没有启动新研究；请先核对服务和存储状态。",
    database_unavailable:"数据库暂时不可用，无法确认已提交状态。请稍后只读刷新。",
    report_not_available:"当前尚无可读取的报告，任务状态和执行记录仍可查看。",
    foreign_active_runs:"存在其他实例的非终态任务，请维护者核对，页面不会自动接管。",
    run_not_found:"没有找到该任务；它可能未持久化或已超出保留期。",
    research_unavailable:"研究依赖未就绪，请检查运行配置。",
    service_stopping:"服务正在停止，请稍后核对当前任务。"
  };
  class ApiError extends Error {
    constructor(code, status = 0, retryMode = null, runId = null) {
      super(MESSAGES[code] || (status === 422 ? "请求参数未通过校验，请检查问题和调用上限。" : status === 404 ? "记录不存在或已超出保留范围。" : "服务暂时无法完成请求，请保留当前任务并检查连接。"));
      this.code = code; this.status = status; this.retryMode = retryMode;
      this.runId = typeof runId === "string" && /^[a-zA-Z0-9_-]{1,128}$/u.test(runId) ? runId : null;
    }
  }
  async function request(path, options = {}) {
    let response;
    try { response = await fetch(`/api/research${path}`, {...options, headers:{"Content-Type":"application/json",...(options.headers || {})}}); }
    catch { throw new ApiError("connection_interrupted"); }
    let body;
    try { body = await response.json(); } catch { throw new ApiError("invalid_response",response.status); }
    if (!response.ok) {
      const issue = body?.error || body?.detail || {};
      throw new ApiError(issue.code || issue.issue_code || "request_failed",response.status,issue.retry_mode,issue.run_id);
    }
    return body;
  }
  class EventLedger {
    constructor() { this.records = new Map(); this.cursor = 0; this.truncated = false; this.conflict = false; this.highWater = 0; }
    merge(runId, batch, run = {}) {
      this.truncated ||= Boolean(batch.truncated || batch.events_truncated || batch.history_truncated || run.event_limit_reached);
      for (const event of batch.events || []) {
        const id = event.event_id;
        if (event.run_id !== runId || !Number.isSafeInteger(id) || id < 1) continue;
        const previous = this.records.get(id);
        if (previous && JSON.stringify(previous) !== JSON.stringify(event)) this.conflict = true;
        if (!previous) this.records.set(id,event);
      }
      this.highWater = Math.max(this.highWater,Number(batch.last_event_seq || batch.next_cursor) || 0,...this.records.keys());
      while (this.records.has(this.cursor + 1)) this.cursor++;
      // 只在服务端明确声明截断时接受缺口；仍保留警告，不生成缺失事件。
      if (this.truncated) this.cursor = Math.max(this.cursor,...this.records.keys());
      return {events:[...this.records.values()].sort((a,b)=>a.event_id-b.event_id),cursor:this.cursor,gap:this.cursor<this.highWater && !this.truncated,truncated:this.truncated,conflict:this.conflict};
    }
  }
  function safeLink(value) { try { const url = new URL(value); return ["http:","https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; } }
  global.ResearchUI = {ApiError,request,EventLedger,safeLink};
})(globalThis);
