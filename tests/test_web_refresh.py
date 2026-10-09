"""Exercise the embedded browser's real polling behavior with a minimal DOM.

These tests need Node.js but no live Pi, network or browser dependencies.
"""
import shutil
import subprocess

import pytest

from redux.web.status_page import render_page


def test_browser_polling_is_nonoverlapping_and_marks_old_health_stale():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for embedded dashboard behavior check")
    script = render_page().split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
const assert=require('assert');
const vm=require('vm');
const fs=require('fs');
const source=fs.readFileSync(0,'utf8');
let now=1000;
let requests=[];
const nodes=new Map();
class Element {
 constructor() {
  this.children=[];
  this.style={};
  this.textContent='';
  this.className='';
  this.innerHTML='';
 }
 appendChild(child){this.children.push(child);return child}
 removeChild(child){this.children.splice(this.children.indexOf(child),1);return child}
 replaceChildren(...children){this.children=children}
 get firstChild(){return this.children[0]||null}
 setAttribute(k,v){this[k]=v}
}
function elem(id){
 if(!nodes.has(id))nodes.set(id,new Element());
 return nodes.get(id);
}
const timers=new Map();
let timerId=0;
const document={
 hidden:false,
 body:new Element(),
 getElementById:elem,
 createElement:()=>new Element(),
 createElementNS:()=>new Element(),
 addEventListener:()=>{},
};
const context={
 document,
 localStorage:{getItem:()=>null,setItem:()=>{}},
 performance:{now:()=>now},
 setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,fn);return id},
 clearTimeout:id=>timers.delete(id),
 setInterval:()=>1,
 AbortController:class {
  constructor(){this.signal={aborted:false}}
  abort(){this.signal.aborted=true}
 },
 fetch:(url,opts)=>new Promise((resolve,reject)=>{
  requests.push({url,opts,resolve,reject})
 }),
};
vm.createContext(context);
vm.runInContext(source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const report=(version,sample)=>({
 runtime:{updated_utc:version,visual_sampled_utc:sample},
 doctor:{overall:'ok',findings:[],
  coverage:{assessed:['live engine'],not_assessed:[]}},
 capture_processing:{available:false,reason:'not observed'},
 narration:[],located:[],position:null,airspace:{},access_points:[],
 sightings:0,face:'‹·_·›',
});
async function deliver(request,data){
 request.resolve({ok:true,json:async()=>data});
 await flush();await flush();
}
(async()=>{
 assert.equal(requests.length,1,'first poll must start immediately');
 context.tick();context.tick();
 assert.equal(requests.length,1,'pending polls must never overlap');
 await deliver(requests.shift(),report(20,20));
 assert.equal(elem('doctorlabel').textContent,'OK');
 assert.equal(elem('sub').textContent,'live · measured');
 const firstMap=elem('map').firstChild;
 assert.ok(firstMap,'a first real visual sample must render');

 now=2000;
 context.tick();
 assert.equal(requests.length,1);
 await deliver(requests.shift(),report(21,20));
 assert.strictEqual(elem('map').firstChild,firstMap,
  'same visual sample must not redraw the map');

 now=3000;
 context.tick();
 await deliver(requests.shift(),report(22,22));
 assert.notStrictEqual(elem('map').firstChild,firstMap,
  'new visual sample must redraw the map');

 now=17000;
 context.tick();
 await deliver(requests.shift(),report(22,22));
 assert.equal(elem('doctorlabel').textContent,'UNKNOWN · STALE');
 assert.match(elem('sub').textContent,/STALE/);

 now=18000;
 context.tick();
 assert.equal(requests.length,1);
 const failed=requests.shift();
 failed.reject({name:'AbortError'});
 await flush();await flush();
 assert.match(elem('sub').textContent,/request timed out/);
 assert.equal(elem('doctorlabel').textContent,'UNKNOWN · STALE');
 assert.equal(context.fetchPending,false,'failed fetch releases polling lock');

 now=19000;
 context.tick();
 await deliver(requests.shift(),report(23,23));
 assert.equal(elem('doctorlabel').textContent,'OK',
  'fresh runtime data restores actual Doctor status');
 assert.equal(elem('sub').textContent,'live · measured');
 console.log('browser polling contract passed');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, capture_output=True,
        text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "browser polling contract passed" in result.stdout


def test_compact_tft_css_and_stale_state_are_explicit():
    html = render_page()
    assert "@media(max-width:520px)" in html
    assert "#map{height:174px}" in html
    assert "min-height:44px" in html
    assert "fetchPending" in html
    assert "lastVisualSample" in html
    assert "UNKNOWN · STALE" in html
    assert "visibilitychange" in html
