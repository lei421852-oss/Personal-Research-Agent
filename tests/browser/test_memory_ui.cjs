/* 本地浏览器验收：所有请求均由路由夹具响应，不访问网络。 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = process.env.MEMORY_UI_REPO;
if (!root) throw new Error("MEMORY_UI_REPO is required");
const web = path.join(root, "app", "workbench_web");
const chrome = process.env.MEMORY_UI_CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const records = Array.from({length:21}, (_, index) => ({
  memory_id:"mem_"+String(index+1).padStart(32,"0"),
  memory_key:"example.key"+index, memory_type:"preference", content:"记录 "+index,
  tags:["公式"],source_type:"user_statement",source_ref:"question_sha256:abc",
  scope_id:"project",status:"active",created_at:"2026-10-29T00:00:00Z",
  updated_at:"2026-10-29T00:00:00Z",expires_at:null,supersedes_id:null,superseded_by_id:"mem_successor"
}));
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:chrome, args:["--disable-background-networking"]});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const requests = [];
  let listFailure=false, statsFailure=false;
  await page.route("**/*", async route => {
    const request=route.request(), u=new URL(request.url()), p=u.pathname;
    requests.push(p);
    if (u.hostname !== "memory.test") return route.abort();
    const fulfill = (data,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(data)});
    if (p === "/") return route.fulfill({contentType:"text/html",body:fs.readFileSync(path.join(web,"index.html"))});
    if (p.startsWith("/workbench-static/")) {
      const name=p.slice("/workbench-static/".length);
      if (!["style.css","app.js","adapters.js","memory.js"].includes(name)) return route.abort();
      return route.fulfill({contentType:name.endsWith(".css")?"text/css":"application/javascript",body:fs.readFileSync(path.join(web,name))});
    }
    if (p === "/api/research/papers") return fulfill({papers:[],catalog:[]});
    if (p === "/api/research/health") return fulfill({});
    if (p === "/api/research/replays") return fulfill({cases:[]});
    if (p === "/api/storage/health") return fulfill({status:"disabled"});
    if (p === "/api/research/runs/r1") return fulfill({run_id:"r1",status:"running",question:"真实轮询夹具",mode:"research",paper_ids:[],report_available:false});
    if (p === "/api/research/runs/r1/events") return fulfill({events:[],next_cursor:0});

    if (p === "/api/memories/stats") return statsFailure ? fulfill({error:{code:"memory_unavailable"}},503) : fulfill({total:21,active:21,superseded:0,expired:0,deleted:0});
    if (p === "/api/memories") {
      if (listFailure) return fulfill({error:{code:"memory_unavailable"}},503);
      const pageNo=Number(u.searchParams.get("page")||1);
      const matches = u.searchParams.get("query") === "no-match" ? [] : records;
      return fulfill({items:matches.slice((pageNo-1)*20,pageNo*20),total:matches.length,page:pageNo,page_size:20});
    }
    if (p.startsWith("/api/memories/")) return fulfill(records.find(r=>r.memory_id===p.split("/").pop()) || {},200);
    return route.abort();
  });
  await page.goto("http://memory.test/");
  await page.waitForSelector("#question");
  await page.locator("#question").fill("保留的研究草稿");
  await page.evaluate(() => { state.run={run_id:"r1",status:"running",question:"真实轮询夹具",mode:"research",paper_ids:[]}; pollRun(); });
  await page.waitForFunction(() => Boolean(state.pollTimer));
  await page.getByRole("button",{name:"记忆",exact:true}).click();
  await page.waitForTimeout(1300);
  assert.ok(requests.filter(p=>p === "/api/research/runs/r1/events").length >= 2, "切换期间事件轮询应继续");

  await page.waitForSelector(".memory-card");
  assert.equal(await page.locator(".memory-card").count(),20);
  assert.match(await page.locator("#memory-stats").innerText(),/21/);
  await page.locator(".memory-card").first().click();
  await page.waitForFunction(() => document.querySelector("#memory-detail").textContent.includes("question_sha256:abc"));
  assert.match(await page.locator("#memory-detail").innerText(),/记录 0/);
  assert.match(await page.locator("#memory-detail").innerText(),/mem_successor/);
  await page.locator("#memory-next").click();
  assert.doesNotMatch(await page.locator("#memory-detail").innerText(),/记录 0/);
  await page.waitForFunction(() => document.querySelector("#memory-page-label").textContent.includes("第 2 页"));
  assert.equal(await page.locator(".memory-card").count(),1);
  await page.locator("#memory-query").fill("no-match");
  await page.locator("#memory-filter").evaluate(form=>form.requestSubmit());
  await page.waitForFunction(() => document.querySelector("#memory-list").textContent.includes("暂无记忆"));
  statsFailure=true; await page.locator("#memory-refresh").click();
  await page.waitForFunction(() => !document.querySelector("#memory-stats-error").hidden);
  assert.match(await page.locator("#memory-list").innerText(),/暂无记忆/);
  listFailure=true; await page.locator("#memory-refresh").click();
  await page.waitForFunction(() => !document.querySelector("#memory-list-error").hidden);
  assert.doesNotMatch(await page.locator("#memory-list").innerText(),/暂无记忆/);
  await page.locator("#memory-back").click();
  assert.equal(await page.locator("#question").inputValue(),"保留的研究草稿");
  assert.equal(await page.locator("#memory-view").isVisible(),false);
  if (process.env.MEMORY_UI_SCREENSHOT) await page.screenshot({path:process.env.MEMORY_UI_SCREENSHOT,fullPage:true});
  assert.ok(requests.includes("/api/memories/stats"));
  await browser.close();
  console.log("PASS: navigation, draft preservation, active polling, listing, detail, pagination, empty, partial failure, error; all network intercepted");
})().catch(error=>{console.error(error);process.exitCode=1});
