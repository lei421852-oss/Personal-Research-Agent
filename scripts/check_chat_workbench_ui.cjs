/* 浏览器交互回归：真实静态页面 + 明确标记的本地 API 夹具，零 Provider 调用。 */
"use strict";
const fs=require("node:fs"),path=require("node:path"),assert=require("node:assert/strict");
const {chromium}=require(process.env.PLAYWRIGHT_NODE_PACKAGE || "playwright");
const args=process.argv.slice(2);if(args.includes("--help")){console.log("用法：node scripts/check_chat_workbench_ui.cjs --url http://127.0.0.1:8008 --output-dir artifacts/ui-check [--browser-executable PATH]；所有 API 研究响应为本地夹具，零 Provider 调用。");process.exit(0)}function option(key,fallback){const i=args.indexOf(key);return i<0?fallback:args[i+1]}
const base=option("--url","http://127.0.0.1:8008"),output=option("--output-dir","artifacts/chat-workbench-browser");
const executablePath=option("--browser-executable",undefined);
const paper=(id)=>({paper_id:id,title:`测试论文 ${id}`,availability:"prepared"});
const event=(id,type,node,status="completed",details={})=>({run_id:"fixture-run",event_id:id,node_type:type,node_id:node,status,event_type:status==="running"?"started":"completed",timestamp:"2026-10-26T08:00:00Z",summary:"浏览器测试夹具记录",source:"live",details});
const report={status:"completed",execution_mode:"tools",direct_answer:"这是**浏览器夹具答案**，不是真实模型研究。 <img src=x onerror=alert(1)>",citations:[{paper_id:"paper-a",page:2}],key_findings:[{paper_id:"paper-a",text:"原文快照 <img src=x onerror=alert(1)>",evidence_ids:["ev-a"],evidence:[{paper_id:"paper-a",page:2,evidence_id:"ev-a",text:"受控正文快照"}]}],limitations:["本地 API 夹具，不代表研究质量。"],language:{status:"not_needed"}};
async function setup(browser,{scenario="completed",events=[event(1,"MainAgent","main")]}={}) {
 const page=await browser.newPage({viewport:{width:1500,height:1000}}),records={posts:[],reads:0,cancels:0,reconciles:0,eventReads:0},errors=[];
 page.on("pageerror",error=>errors.push(error.message));
 let status=scenario==="cancel"?"running":"completed";
 const run=()=>({run_id:"fixture-run",question:"夹具研究问题",status,mode:"live",report_available:status==="completed",metrics:{model_calls:1,tool_calls:1,total_latency_ms:20}});
 await page.route("**/api/storage/health",route=>route.fulfill({json:{status:"disabled",scope:"connection_only"}}));
 await page.route("**/api/research/**",async route=>{
  const request=route.request(),url=new URL(request.url()),endpoint=url.pathname.replace("/api/research","");
  const reply=(json,code=200)=>route.fulfill({status:code,json});
  if(endpoint==="/papers")return reply({catalog:[paper("paper-a"),paper("paper-b"),paper("paper-c")]});
  if(endpoint==="/health")return reply({main_agent_ready:true,replay_ready:true,dependencies:[],limits:{max_model_calls:200}});
  if(endpoint==="/replays")return reply({cases:[]});
  if(endpoint==="/runs" && request.method()==="POST") {
   const headers=request.headers();records.posts.push({headers,body:request.postDataJSON()});
   if(headers["idempotency-replay-only"]==="true"){records.reconciles++;return reply(run());}
   await new Promise(resolve=>setTimeout(resolve,180));
   if(scenario==="unknown")return reply({error:{code:"submission_outcome_unknown",message:"postgresql://private:secret@host",retry_mode:"reconcile_only"}},503);
   return reply(run(),202);
  }
  if(endpoint.endsWith("/cancel")){records.cancels++;status="cancelled";return reply({...run(),cancel_requested:true});}
  if(endpoint.endsWith("/events")){
   records.eventReads++;
   let selected=events;
   if(scenario==="gap" && records.eventReads===1)selected=[events[2]];
   if(scenario==="gap" && records.eventReads>1)selected=[events[1],events[0],events[2],events[2]];
   return reply({events:selected,next_cursor:events.length,last_event_seq:events.length});
  }
  if(endpoint.endsWith("/report"))return reply(report);
  if(endpoint==="/runs/fixture-run"){records.reads++;if(scenario==="offline")return reply({error:{code:"database_unavailable",message:"password=secret"}},503);return reply(run());}
  return reply({error:{code:"run_not_found"}},404);
 });
 await page.goto(base);await page.waitForFunction(()=>state.health?.main_agent_ready===true);
 return {page,records,errors};
}
async function main(){
 fs.mkdirSync(output,{recursive:true});const browser=await chromium.launch({...(executablePath?{executablePath}:{}),headless:true});const checks=[];
 try {
  {
   const {page,records,errors}=await setup(browser);
   assert.equal(await page.locator("#workflow .node").count(),0);
   await page.screenshot({path:path.join(output,"initial.png"),fullPage:true});
   await page.locator("#question").fill("中文研究问题");
   await page.locator("#question").dispatchEvent("keydown",{key:"Enter",isComposing:true});assert.equal(records.posts.length,0);
   await page.locator("#question").press("Shift+Enter");assert.ok((await page.locator("#question").inputValue()).includes("\n"));
   await page.locator("#question").press("Enter");
   await page.evaluate(()=>document.querySelector("#research-form").requestSubmit());
   await page.waitForSelector("#direct-answer");assert.equal(records.posts.length,1);
   assert.equal(await page.locator("#report-details img").count(),0);
   assert.equal(await page.locator("#direct-answer strong").count(),1);
   assert.equal(await page.locator("#workflow .node").count(),1);
   await page.locator("#workflow .node").click();assert.equal(await page.locator("#node-panel").isVisible(),true);
   await page.locator("#reference-papers button").click();assert.match(await page.locator("#evidence-details").innerText(),/受控正文快照/);
   const reads=records.reads;await page.waitForTimeout(1600);assert.equal(records.reads,reads);
   await page.reload();await page.waitForSelector("#direct-answer");assert.equal(records.posts.length,1);
   await page.getByRole("button",{name:"历史记录",exact:true}).click();assert.equal(await page.locator("#history-view").isVisible(),true);
   await page.getByRole("button",{name:/新建研究/}).click();assert.equal(await page.locator("#welcome").isVisible(),true);
   await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>innerWidth+1),false);
   await page.screenshot({path:path.join(output,"mobile.png"),fullPage:true});
   assert.deepEqual(errors,[]);checks.push("中文输入/IME/换行/防重复提交/终态停止轮询/引用/XSS/刷新只读/导航/窄屏");await page.close();
  }
  {
   const events=[event(1,"MainAgent","main"),event(2,"ResearchTool","research"),event(3,"Planner","plan"),event(4,"Worker","worker-a","completed",{paper_id:"paper-a"}),event(5,"Worker","worker-b","running",{paper_id:"paper-b"}),event(6,"Reviewer","review-a","completed",{paper_id:"paper-a"}),event(7,"Reviewer","unassigned","completed",{paper_id:"paper-c"}),event(8,"Synthesis","synthesis")];
   const {page,errors}=await setup(browser,{events});await page.locator("#question").fill("协作夹具问题");await page.locator("#start-button").click();await page.waitForSelector("#direct-answer");
   assert.equal(await page.locator(".paper-branch").count(),2);assert.equal(await page.locator(".paper-branch[data-paper-id='paper-c']").count(),0);
   const workflow=page.locator("#workflow");assert.equal(await workflow.getAttribute("data-layout"),"tree");
   assert.equal(await page.locator("#workflow [data-node-type='Worker']").count(),2);
   await page.locator(".worker-branch-details summary").first().click();assert.equal(await page.locator(".worker-branch-details").first().getAttribute("open"),null);
   await page.locator(".worker-branch-details summary").first().click();
   assert.equal(await workflow.evaluate(el=>el.scrollWidth>el.clientWidth),true);
   await page.screenshot({path:path.join(output,"controlled-worker-tree.png"),fullPage:true});
   await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.body.scrollWidth>innerWidth+1),false);
   assert.deepEqual(errors,[]);checks.push("真实事件形状驱动的横向协作布局/仅实际 Worker 分支/折叠/长流程内部滚动");await page.close();
  }
  {
   const {page,records,errors}=await setup(browser,{scenario:"gap",events:[event(1,"MainAgent","main"),event(2,"Evidence","text"),event(3,"Evidence","page")]});
   await page.locator("#question").fill("事件缺口夹具");await page.locator("#start-button").click();await page.waitForFunction(()=>state.cursor===3);
   assert.deepEqual(await page.evaluate(()=>state.events.map(e=>e.event_id)),[1,2,3]);assert.equal(records.posts.length,1);assert.deepEqual(errors,[]);checks.push("事件乱序/迟到/缺口补读/去重");await page.close();
  }
  {
   const {page,records,errors}=await setup(browser,{scenario:"unknown"});await page.locator("#question").fill("提交结果未知夹具");await page.locator("#start-button").click();await page.waitForSelector("#submission-recovery:not([hidden])");
   assert.ok((await page.locator("#question").inputValue()).includes("未知"));assert.ok(!(await page.locator("#api-error").innerText()).includes("secret"));assert.equal(records.posts.length,1);
   await page.reload();await page.waitForSelector("#submission-recovery:not([hidden])");assert.equal(records.posts.length,1);
   await page.screenshot({path:path.join(output,"submission-unknown.png"),fullPage:true});
   await page.locator("#reconcile-button").click();await page.waitForSelector("#direct-answer");
   assert.equal(records.reconciles,1);assert.equal(records.posts.length,2);assert.equal(records.posts[1].headers["idempotency-key"],records.posts[0].headers["idempotency-key"]);assert.deepEqual(records.posts[1].body,records.posts[0].body);
   assert.deepEqual(errors,[]);checks.push("UNKNOWN 不冒充回滚/刷新不重发/只读核对同键同请求/错误脱敏");await page.close();
  }
  {
   const {page,records,errors}=await setup(browser,{scenario:"cancel",events:[event(1,"MainAgent","main","running")]});await page.locator("#question").fill("取消夹具问题");await page.locator("#start-button").click();await page.waitForSelector("#cancel-button:not([disabled])");await page.locator("#cancel-button").click();await page.waitForFunction(()=>state.run.status==="cancelled");assert.equal(records.posts.length,1);assert.equal(records.cancels,1);assert.equal(await page.locator("#workflow .node.cancelled").count(),0);assert.deepEqual(errors,[]);checks.push("真实取消接口形状/不伪造节点取消事件");await page.close();
  }
  {
   const {page,records,errors}=await setup(browser,{scenario:"offline"});await page.evaluate(()=>localStorage.setItem("personal-agent-workbench-run-id","fixture-run"));await page.reload();await page.waitForSelector("#api-error:not([hidden])");assert.equal(await page.evaluate(()=>localStorage.getItem("personal-agent-workbench-run-id")),"fixture-run");assert.equal(records.posts.length,0);assert.deepEqual(errors,[]);checks.push("数据库/服务不可用时保留书签，不伪报无记录，不重建任务");await page.close();
  }
  const result={status:"PASS",source:"controlled_browser_api_fixtures",real_provider_calls:0,checks,screenshots:["initial.png","mobile.png","controlled-worker-tree.png","submission-unknown.png"]};fs.writeFileSync(path.join(output,"browser-check.json"),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exit(1)});
