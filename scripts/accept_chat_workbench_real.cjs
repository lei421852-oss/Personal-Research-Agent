/* 显式门控的真实浏览器验收；每个案例只提交一次，已有尝试只允许读取。 */
"use strict";
const fs=require("node:fs"),path=require("node:path");
const {chromium}=require(process.env.PLAYWRIGHT_NODE_PACKAGE || "playwright");
const args=process.argv.slice(2);if(args.includes("--help")){console.log("用法：node scripts/accept_chat_workbench_real.cjs --url http://127.0.0.1:8008 --output-dir artifacts/ui-check [--browser-executable PATH] --case simple|collaboration；真实调用须另行授权并设置 UI_REAL_RESEARCH_AUTHORIZED=yes，已有尝试不会重提。");process.exit(0)}function arg(key,fallback){const i=args.indexOf(key);return i<0?fallback:args[i+1]}
const examples={
 simple:{budget:12,question:"CLIP 使用什么训练目标对齐图像和文本？请引用当前论文库中的证据，并用中文简洁回答。"},
 collaboration:{budget:36,question:"请启动多论文协作研究，对 BLIP-2 与 Flamingo 的视觉语言连接机制分别检索论文证据并审核，再综合比较 Q-Former、Perceiver Resampler 和门控交叉注意力如何将视觉特征传入语言模型；只研究这一个维度，保留证据缺口、逐项引用，最后用中文回答。"}
};
async function main(){
 if(process.env.UI_REAL_RESEARCH_AUTHORIZED!=="yes")throw new Error("真实调用未授权：仅在人工批准本轮预算后设置 UI_REAL_RESEARCH_AUTHORIZED=yes。");
 const name=arg("--case","simple"),example=examples[name];if(!example)throw new Error("未知验收案例");
 const base=arg("--url","http://127.0.0.1:8008"),out=arg("--output-dir","artifacts/chat-workbench-real");fs.mkdirSync(out,{recursive:true});
 const ledger=path.join(out,`${name}-submission.json`);if(fs.existsSync(ledger))throw new Error("此案例已有提交尝试。为避免重复付费，请使用记录中的 run_id 只读核对，不重新运行。");
 const executable=arg("--browser-executable",undefined),browser=await chromium.launch({headless:true,...(executable?{executablePath:executable}:{})});
 const page=await browser.newPage({viewport:{width:1540,height:1040}}),errors=[];page.on("pageerror",e=>errors.push(e.message));
 const evidence={case:name,question:example.question,model_call_limit:example.budget,started_at:new Date().toISOString(),source:"real_browser_and_real_provider",api_overrides:false,errors};let progress;
 try {
  await page.goto(base);await page.waitForFunction(()=>state.health?.main_agent_ready===true);
  await page.locator("#model-budget").fill(String(example.budget));await page.locator("#question").fill(example.question);
  fs.writeFileSync(ledger,JSON.stringify({...evidence,submission_attempted:true},null,2),{flag:"wx"});
  const responsePromise=page.waitForResponse(r=>r.url().endsWith("/api/research/runs")&&r.request().method()==="POST");
  await page.locator("#start-button").click();const response=await responsePromise;
  evidence.creation={status:response.status(),request:response.request().postDataJSON(),response:await response.json()};
  evidence.run_id=evidence.creation.response.run_id;fs.writeFileSync(ledger,JSON.stringify(evidence,null,2));
  if(!response.ok()||!evidence.run_id)throw new Error("真实创建没有返回已确认任务；停止，不重复提交。");
  progress=setInterval(async()=>{try{console.log(JSON.stringify(await page.evaluate(()=>({run_id:state.run?.run_id,status:state.run?.status,events:state.events.length,roles:[...new Set(state.events.map(e=>e.node_type))],model_calls:state.run?.metrics?.model_calls}))));}catch{/* 页面退出时不再读取。 */}},15000);
  await page.waitForFunction(()=>state.run && !["queued","running"].includes(state.run.status) && (!state.run.report_available || state.reportFetched) && state.run.report_language_status!=="pending",{},{timeout:500000});
  evidence.finished_at=new Date().toISOString();evidence.public_state=await page.evaluate(()=>({run:state.run,events:state.events,report:state.report,display_nodes:[...state.displayNodes.keys()],layout:document.querySelector("#workflow").dataset.layout,cursor:state.cursor}));
  evidence.report_headers=await page.evaluate(async()=>{const r=await fetch(`/api/research/runs/${encodeURIComponent(state.run.run_id)}/report`);return{status:r.status,revision:r.headers.get("x-report-revision"),sha256:r.headers.get("x-report-payload-sha256")}});
  await page.screenshot({path:path.join(out,`${name}-real.png`),fullPage:true});
  const posts=[];page.on("request",r=>{if(r.method()==="POST")posts.push(r.url())});
  await page.reload();await page.waitForFunction(id=>state.run?.run_id===id && (!state.run.report_available || state.reportFetched),evidence.run_id);
  evidence.refresh={post_requests:posts.length,run_id:await page.evaluate(()=>state.run.run_id),report_present:await page.evaluate(()=>Boolean(state.report)),event_count:await page.evaluate(()=>state.events.length)};
  evidence.status=evidence.public_state.run.status;fs.writeFileSync(path.join(out,`${name}-execution.json`),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify({case:name,status:evidence.status,run_id:evidence.run_id,metrics:evidence.public_state.run.metrics,layout:evidence.public_state.layout,actual_workers:evidence.public_state.events.filter(e=>e.node_type==="Worker").map(e=>e.node_id).filter((v,i,a)=>a.indexOf(v)===i),refresh:evidence.refresh,browser_errors:errors}));
 }catch(error){evidence.failure=error.message;evidence.finished_at=new Date().toISOString();try{evidence.public_state=await page.evaluate(()=>({run:state.run,events:state.events,report:state.report}));await page.screenshot({path:path.join(out,`${name}-failure.png`),fullPage:true});}catch{/* 保留此前已落盘的提交记录。 */}fs.writeFileSync(path.join(out,`${name}-execution.json`),JSON.stringify(evidence,null,2));throw error;}
 finally{clearInterval(progress);await browser.close()}
}
main().catch(e=>{console.error(e.message);process.exit(1)});
