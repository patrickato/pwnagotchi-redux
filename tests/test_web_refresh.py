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
  this.style={
   setProperty:(key,value)=>{this.style[key]=value},
   removeProperty:(key)=>{delete this.style[key]},
  };
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
const routes=['overview','radio','captures','doctor'];
const navTabs=routes.map(name=>{
 const tab=new Element();
 tab.dataset={view:name};
 tab.focus=function(){tab.focused=true};
 return tab;
});
const panes=routes.map(name=>{
 const panel=new Element();
 panel.dataset={page:name};
 return panel;
});
const timers=new Map();
let timerId=0;
const document={
 hidden:false,
 body:new Element(),
 getElementById:elem,
 querySelector:(selector)=>{
  if(selector==='.doctor-primary')return elem('doctor-primary');
  throw Error('unknown selector '+selector);
 },
 querySelectorAll:(selector)=>{
  if(selector==='#viewnav [data-view]')return navTabs;
  if(selector==='#viewroot [data-page]')return panes;
  throw Error('unknown selector '+selector);
 },
 createElement:()=>new Element(),
 createElementNS:()=>new Element(),
 addEventListener:()=>{},
};
const location={hash:''};
const context={
 document,location,
 window:{addEventListener:()=>{}},
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
 runtime:{updated_utc:version,visual_sampled_utc:sample,
  visual_revision:sample},
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
 assert.equal(context.currentView,'overview','startup displays overview');
 assert.equal(elem('palettebtn').textContent,'Signal');
 assert.equal(document.body.style['--acc'],'#4ec9b0');
 assert.equal(context.registerTheme('__proto__',{'--acc':'#ffffff'}),false);
 assert.equal(context.registerTheme('unsafe',{'--acc':'url(javascript:evil)'}),false);
 assert.equal(context.registerTheme('badkey',{'--fake':'#abcdef'}),false);
 assert.equal(context.registerTheme('signal',{'--acc':'#abcdef'}),false);
 assert.equal(context.registerTheme('quartz',{'--acc':'#e5a6f7'}),true);
 assert.equal(context.applyTheme('quartz'),true);
 assert.equal(document.body.style['--acc'],'#e5a6f7');
 assert.equal(context.applyTheme('unknown-id'),true);
 assert.equal(elem('palettebtn').textContent,'Signal');
 elem('palettebtn').onclick();
 assert.equal(elem('palettebtn').textContent,'Ember');
 assert.equal(document.body.style['--acc'],'#ffa36f');
 assert.equal(context.currentTheme,'ember');
 elem('palettebtn').onclick();
 assert.equal(elem('palettebtn').textContent,'Glacier');
 elem('palettebtn').onclick();
 assert.equal(elem('palettebtn').textContent,'Signal');
 assert.equal(navTabs[0].dataset.view,'overview');
 assert.equal(navTabs[0]['aria-pressed'],'true');
 assert.equal(panes[0].hidden,false);
 assert.equal(panes[1].hidden,true);
 assert.equal(requests.length,1,'first poll must start immediately');
 context.tick();context.tick();
 assert.equal(requests.length,1,'pending polls must never overlap');
 await deliver(requests.shift(),report(20,20));
 assert.equal(elem('doctorlabel').textContent,'OK');
 assert.equal(elem('healthjump').className,'health-shortcut ok');
 assert.equal(elem('engineindicator').textContent,'NO SESSION',
  'absent runtime state must never appear running');
 assert.equal(elem('engineindicator').className,'runtime-pill unknown');
 assert.equal(elem('herocapture').textContent,'CAPTURE UNKNOWN');
 assert.equal(elem('sub').textContent,'live · measured');
 elem('healthjump').onclick();
 assert.equal(context.currentView,'doctor');
 assert.equal(elem('healthjump')['aria-label'],'Open Doctor: OK');
 navTabs[3].onclick();
 assert.equal(context.currentView,'doctor');
 assert.equal(location.hash,'doctor');
 assert.equal(panes[3].hidden,false);
 assert.equal(panes[0].hidden,true);
 assert.equal(navTabs[3]['aria-pressed'],'true');
 const key={key:'ArrowRight',preventDefault(){this.prevented=true}};
 elem('viewnav').onkeydown(key);
 assert.equal(context.currentView,'overview','keyboard nav wraps around');
 assert.equal(key.prevented,true);
 assert.equal(navTabs[0].focused,true);
 const root=elem('viewroot');
 const start={pointerType:'touch',clientX:220,clientY:150,
  target:{closest:()=>null}};
 root.onpointerdown(start);
 root.onpointerup({pointerType:'touch',clientX:218,clientY:40});
 assert.equal(context.currentView,'overview','vertical scrolling must not switch pages');
 root.onpointerdown(start);
 root.onpointerup({pointerType:'touch',clientX:120,clientY:145});
 assert.equal(context.currentView,'radio','left swipe switches to next page');
 assert.equal(requests.length,0,'page navigation must not restart telemetry polling');
 assert.equal(elem('capturestate').textContent,'UNKNOWN');
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
 assert.equal(elem('healthjump').className,'health-shortcut unknown');
 assert.equal(elem('engineindicator').textContent,'STALE');
 assert.equal(elem('herocapture').textContent,'CAPTURE UNVERIFIED');
 assert.equal(elem('pipelinestate').textContent,'UNKNOWN · STALE');
 assert.match(elem('sub').textContent,/STALE/);

 now=18000;
 context.tick();
 assert.equal(requests.length,1);
 const failed=requests.shift();
 assert.equal(failed.opts.signal.aborted,false);
 const deadline=[...timers.values()][0];
 assert.equal(typeof deadline,'function');
 deadline(); // simulate the five-second browser deadline
 assert.equal(failed.opts.signal.aborted,true,
  'timeout must actually abort the in-flight HTTP request');
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
 assert.equal(elem('healthjump').className,'health-shortcut ok');
 assert.equal(elem('sub').textContent,'live · measured');
 const critical=report(24,24);
 critical.runtime.state='running';
 critical.doctor.overall='action';
 critical.doctor.findings=[{
  area:'capture storage',status:'action',summary:'REDUXCAP missing',
  reason:'No mounted partition',remediation:'Restore mount before restart',
 }];
 context.paint(critical);
 assert.equal(elem('healthjump').className,'health-shortcut action');
 assert.equal(elem('healthjump')['aria-label'],'Open Doctor: ACTION REQUIRED');
 assert.equal(elem('engineindicator').className,'runtime-pill ok');
 assert.equal(elem('engineindicator').textContent,'RUNNING');
 assert.equal(elem('herocapture').textContent,'ENGINE RUNNING');
 const originalFinding=elem('doctorlist').firstChild;
 assert.ok(originalFinding,'actual Doctor findings render in the dedicated pane');
 context.paint(critical);
 assert.strictEqual(elem('doctorlist').firstChild,originalFinding,
  'same findings must retain their DOM rather than destroying focus on each poll');
 const paused=report(25,25);
 paused.runtime.state='storage_paused';
 paused.doctor.overall='degraded';
 context.paint(paused);
 assert.equal(elem('engineindicator').textContent,'STORAGE PAUSED');
 assert.equal(elem('engineindicator').className,'runtime-pill action');
 assert.equal(elem('healthjump').className,'health-shortcut degraded');
 assert.equal(elem('doctor-primary').className,'card doctor-primary degraded');
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
