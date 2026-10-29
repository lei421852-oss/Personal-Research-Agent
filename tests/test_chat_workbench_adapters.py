"""离线执行浏览器边界适配器，不调用 Provider 或数据库。"""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def node_binary():
    executable = shutil.which("node")
    if executable:
        return executable
    spec = importlib.util.find_spec("playwright")
    if spec and spec.submodule_search_locations:
        executable = (
            Path(next(iter(spec.submodule_search_locations))) / "driver" / "node"
        )
        if executable.is_file():
            return str(executable)
    pytest.skip("前端适配器执行测试需要 Node 或项目 ui-test 中的 Playwright Node")


@pytest.mark.parametrize(
    "scenario",
    [
        """const ledger=new EventLedger(); const e=event(1); ledger.merge('r',{events:[e,e]}); assert.equal(ledger.records.size,1); assert.equal(ledger.cursor,1);""",
        """const ledger=new EventLedger(); let r=ledger.merge('r',{events:[event(3)],next_cursor:3}); assert.equal(r.cursor,0);assert.equal(r.gap,true); r=ledger.merge('r',{events:[event(2),event(1)]});assert.deepEqual(r.events.map(e=>e.event_id),[1,2,3]);assert.equal(r.cursor,3);assert.equal(r.gap,false);""",
        """const ledger=new EventLedger();const r=ledger.merge('r',{events:[event(1,'foreign'),event(-1),{...event(2),event_id:'2'}]});assert.equal(r.events.length,0);""",
        """const ledger=new EventLedger();ledger.merge('r',{events:[event(1)]});const r=ledger.merge('r',{events:[{...event(1),summary:'changed'}]});assert.equal(r.conflict,true);assert.equal(r.events[0].summary,'record');""",
        """const ledger=new EventLedger();const r=ledger.merge('r',{events:[event(5)],truncated:true,next_cursor:5});assert.equal(r.truncated,true);assert.equal(r.cursor,5);assert.equal(r.events.length,1);""",
        """const ledger=new EventLedger();const r=ledger.merge('r',{events:[],last_event_seq:2});assert.equal(r.gap,true);assert.equal(r.cursor,0);assert.equal(r.events.length,0);""",
        """assert.equal(safeLink('javascript:alert(1)'),null);assert.equal(safeLink('https://user:password@example.org'),null);assert.equal(safeLink('https://arxiv.org/abs/2103.00020'),'https://arxiv.org/abs/2103.00020');""",
        """const error=new ApiError('submission_outcome_unknown',503,'reconcile_only','run-1');assert.equal(error.retryMode,'reconcile_only');assert.equal(error.runId,'run-1');assert.match(error.message,/未知/);""",
        """global.fetch=async()=>({ok:false,status:503,json:async()=>({error:{code:'submission_outcome_unknown',retry_mode:'reconcile_only',run_id:'run-1',message:'postgresql://secret:password@private-host'}})});await assert.rejects(request('/runs'),e=>e.code==='submission_outcome_unknown' && e.runId==='run-1' && !e.message.includes('password'));""",
        """global.fetch=async()=>{throw new Error('secret raw network stack')};await assert.rejects(request('/runs'),e=>e.code==='connection_interrupted' && !e.message.includes('secret'));""",
        """global.fetch=async()=>({ok:true,status:202,json:async()=>{throw new Error('raw')}});await assert.rejects(request('/runs'),e=>e.code==='invalid_response');""",
        """let observed;global.fetch=async(url,options)=>{observed={url,options};return{ok:true,json:async()=>({run_id:'r'})}};assert.deepEqual(await request('/runs',{method:'POST',headers:{'Idempotency-Key':'k','Idempotency-Replay-Only':'true'},body:'{}'}),{run_id:'r'});assert.equal(observed.options.headers['Content-Type'],'application/json');assert.equal(observed.options.headers['Idempotency-Replay-Only'],'true');""",
    ],
)
def test_browser_adapter_contract(scenario):
    source = (ROOT / "app/workbench_web/adapters.js").read_text()
    harness = """
const assert=require('node:assert/strict');
const {EventLedger,ApiError,safeLink,request}=ResearchUI;
function event(id,run='r'){return{run_id:run,event_id:id,node_id:'node',node_type:'MainAgent',status:'running',summary:'record'}}
(async()=>{SCENARIO})().catch(error=>{console.error(error);process.exit(1)});
""".replace("SCENARIO", scenario)
    result = subprocess.run(
        [node_binary(), "-"],
        input=source + harness,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
