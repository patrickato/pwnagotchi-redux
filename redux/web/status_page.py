"""Web status dashboard — the glass-box view in a browser (atlas P-11).

Serves two things from a running Augur: `GET /api/status` (the JSON snapshot)
and `GET /` (a self-contained page that polls it and shows what the device is doing
and *why*). No external assets, no fake data — it renders exactly what the system
reports.

Rendering lives on the *viewer's* browser (phone/laptop), not the Pi: the Pi only
serves a small JSON blob, so the rich moving map / radar / sparkline cost the device
nothing (the heavy pixels are drawn client-side). A `plain` skin drops to a stark,
pwnagotchi-style readout; a `rich` skin adds the live map and extras. Exposure
follows the repo rule: default `bind_scope=localhost`; a wider scope is a deliberate
choice, and `serve()` always logs the exact URL.
"""
from __future__ import annotations

import hmac
import http.cookies
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

_log = logging.getLogger("redux.web")

# bind_scope -> host to bind
_SCOPE_HOST = {
    "localhost": "127.0.0.1",
    "lan": "0.0.0.0",
    "tailscale": "0.0.0.0",   # intended to be reached only over the tailnet
    "auto": "127.0.0.1",
}


def status_payload(augur, *, processing_report=None) -> Dict:
    """The snapshot the dashboard renders: Augur.status() + recent narration +
    located sightings + this device's own position (for the moving map).

    A live runtime may supply its already-read capture-processing report.
    """
    data = dict(augur.status(processing_report=processing_report))
    data["narration"] = [l.text for l in augur.narrator.lines(12)]
    data["located"] = (
        augur.located_sightings() if hasattr(augur, "located_sightings") else []
    )
    data["position"] = (
        augur.current_position() if hasattr(augur, "current_position") else None
    )
    # airspace aggregates from the Cache (reuses the geo helpers) — channel
    # occupancy + RSSI distribution for the dashboard's airspace panel.
    try:
        from ..geo.channel_stats import channel_counts
        from ..geo.rssi_histogram import rssi_histogram
        data["airspace"] = {"channels": channel_counts(augur.store),
                            "rssi": rssi_histogram(augur.store)}
    except Exception:
        data["airspace"] = {"channels": {}, "rssi": {}}
    # access points in range — the survey inventory, sourced from the Cache (the
    # APs this device has actually recorded). Read-only situational awareness.
    try:
        from ..geo.latest_n import latest_n
        aps = latest_n(augur.store, 16, kind="wifi")
        data["access_points"] = [
            {"bssid": s.mac, "ssid": s.ssid, "channel": s.channel, "rssi": s.rssi, "ts": s.ts}
            for s in aps
        ]
    except Exception:
        data["access_points"] = []
    return data


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Augur</title><style>
:root{--bg:#0a0e13;--fg:#e6edf3;--mut:#8b98a5;--dim:#5a7187;--acc:#4ec9b0;--warn:#e3b341;
--crit:#f85149;--card:#121922;--line:#1f2a35;--wifi:#5aa0ff;--ble:#9a7bff;--me:#4ec9b0;
--mono:ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 var(--mono)}
header{position:sticky;top:0;z-index:2;background:var(--bg);padding:8px 12px;
border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}
h1{font-size:17px;margin:0;letter-spacing:1px}.sp{flex:1}
.mut{color:var(--mut)}.dim{color:var(--dim)}
button{font:12px var(--mono);background:#18222d;color:var(--fg);border:1px solid var(--line);
border-radius:7px;padding:5px 10px;min-height:42px;cursor:pointer;touch-action:manipulation}button.on{border-color:var(--acc);color:var(--acc)}
main{max-width:900px;margin:0 auto;padding:16px;display:grid;gap:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.row{display:flex;flex-wrap:wrap;gap:12px}.kv{flex:1 1 120px}
.k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.v{font-size:17px;margin-top:2px}.creature{font-size:19px;color:var(--acc)}
.face{font-size:34px;line-height:1;letter-spacing:2px;color:var(--acc);transition:color .25s}
.face.crit{color:var(--crit)}.face.blinded{color:var(--dim)}
@keyframes fpop{0%{transform:translateY(-3px) scale(1.07)}60%{transform:none}100%{transform:none}}
.face.pop{animation:fpop .5s ease}
.reason{color:var(--mut);font-size:12px;margin-top:4px}
ul{margin:6px 0 0;padding-left:16px}li{color:var(--mut);font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;background:#1d2630;font-size:12px}
.badge.crit{background:#3a1416;color:var(--crit)}.badge.warn{background:#352a12;color:var(--warn)}
#wrap{position:relative}
#map{width:100%;height:340px;display:block;background:#0b1118;border:1px solid var(--line);border-radius:9px}
#map .track{fill:none;stroke:var(--acc);stroke-width:1.4;stroke-opacity:.55}
#map text{font:10px var(--mono);fill:var(--mut)}
.leg{display:flex;gap:14px;margin-top:6px;font-size:11px}.leg i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:middle}
.ping{animation:ping 2.2s ease-out infinite}
@keyframes ping{0%{r:4;opacity:.9}100%{r:26;opacity:0}}
#spark{width:100%;height:40px;display:block}
#chanbars,#rssibars{width:100%;display:block;margin-top:4px}
#chanbars rect{fill:var(--wifi)}#rssibars rect{fill:var(--acc)}
#chanbars text,#rssibars text{font:9px var(--mono);fill:var(--dim)}
.aptbl{display:grid;grid-template-columns:30px 148px 48px 1fr;gap:3px 10px;margin-top:6px;font-size:12px}
.aptbl .h{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.aptbl .c{color:var(--dim)}
body[data-skin="plain"] .rich{display:none}
body[data-skin="plain"]{--card:#0d1319}
body[data-skin="plain"] .card{border-color:#16202a}
.doctor-top{display:flex;align-items:center;gap:12px}
.doctor-heart{font-size:32px;color:var(--dim)}
.doctor-heart.ok,.doctor-title.ok{color:var(--acc)}
.doctor-heart.attention,.doctor-title.attention{color:var(--warn)}
.doctor-heart.degraded,.doctor-heart.action,.doctor-title.degraded,.doctor-title.action{color:var(--crit)}
.doctor-label{font-weight:bold}.doctor-list{display:grid;gap:7px;margin-top:9px}
.doctor-finding{border-top:1px solid var(--line);padding-top:7px;font-size:12px}
.doctor-title{font-weight:bold}.doctor-remedy{color:var(--fg);margin-top:2px}
.doctor-details{margin-top:9px;color:var(--mut);font-size:12px}
.doctor-details summary{cursor:pointer;min-height:44px;display:flex;align-items:center;
touch-action:manipulation}
[hidden]{display:none!important}
.view-nav{width:min(900px,100%);margin:0 auto;padding:7px 12px;display:grid;
grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;background:var(--bg)}
.view-nav button{font-size:12px;min-width:0;white-space:nowrap;padding:5px 2px}
.view-nav button[aria-pressed="true"]{color:var(--acc);border-color:var(--acc);
background:#13332e;font-weight:700}
.view-nav button:focus-visible{outline:2px solid var(--wifi);outline-offset:2px}
@media(max-width:520px){
 html,body{height:100%;height:100dvh}
 body{display:flex;flex-direction:column;overflow:hidden}
 header{flex:0 0 auto}
 #viewnav{flex:0 0 auto;order:3;padding:4px;gap:4px;border-top:1px solid var(--line)}
 #viewnav button{height:46px;min-height:46px;font-size:12px}
 #viewroot{flex:1;min-height:0;width:100%;overflow-y:auto;overscroll-behavior:contain;
 -webkit-overflow-scrolling:touch}
 main{padding:8px;gap:7px}.card{border-radius:7px;padding:8px 10px}
 header{padding:5px 9px}h1{font-size:16px}
 #map{height:174px}.aptbl{grid-template-columns:24px minmax(0,1fr) 37px 42px;
 gap:2px 4px;font-size:11px;overflow-wrap:anywhere}
 .row{gap:7px}.kv{flex:1 1 92px}.v{font-size:15px}
 .leg{gap:7px;flex-wrap:wrap}.doctor-top{gap:8px}
}
</style></head><body data-skin="rich">
<header><h1>Augur</h1><span class="mut" id="sub">glass-box</span><span class="sp"></span>
 <button id="skinbtn" title="toggle skin">rich</button></header>
<nav id="viewnav" class="view-nav" aria-label="Redux workspace pages">
 <button type="button" data-view="overview" aria-pressed="true">Overview</button>
 <button type="button" data-view="radio" aria-pressed="false">Radio</button>
 <button type="button" data-view="captures" aria-pressed="false">Captures</button>
 <button type="button" data-view="doctor" aria-pressed="false">Doctor</button>
</nav>
<main id="viewroot" tabindex="-1">
 <div class="card" data-page="overview"><div class="face" id="face">‹·_·›</div><div class="creature" id="creature">…</div><div class="reason" id="mood"></div></div>
 <div class="card rich" data-page="radio" hidden><div class="k">airspace · real GPS fixes only · moves as you move</div>
   <div id="wrap"><svg id="map" viewBox="0 0 400 340" preserveAspectRatio="xMidYMid meet"
     aria-label="live sighting map"></svg></div>
   <div class="leg mut"><span><i style="background:var(--me)"></i>you</span>
     <span><i style="background:var(--wifi)"></i>wifi</span>
     <span><i style="background:var(--ble)"></i>ble</span><span id="maprange" class="dim"></span></div></div>
 <section class="card" data-page="doctor" hidden aria-label="Device Doctor">
  <div class="k">Doctor · measured device health</div>
  <div class="doctor-top" role="status" aria-live="polite">
   <span id="doctorheart" class="doctor-heart unknown" aria-hidden="true">♡</span>
   <div><div id="doctorlabel" class="doctor-label">UNKNOWN</div>
    <div id="doctorcoverage" class="reason">Waiting for health data</div></div>
  </div>
  <details class="doctor-details"><summary>Findings and recovery advice</summary>
   <div id="doctorlist" class="doctor-list"></div></details>
 </section>
 <section class="card" data-page="captures" hidden aria-label="Capture processing">
  <div class="k">Capture processing · worker ledger</div>
  <div id="pipelinestate" class="doctor-title unknown">UNKNOWN · not assessed</div>
  <div id="pipelinecounts" class="reason">No durable worker scan observed</div>
  <div id="pipelinereason" class="reason"></div>
 </section>
 <section class="card" data-page="captures" hidden aria-label="Capture lifecycle">
  <div class="k">Capture lifecycle · live supervisor</div>
  <div class="row">
   <div class="kv"><div class="k">Capture state</div><div class="v" id="capturestate">UNKNOWN</div></div>
   <div class="kv"><div class="k">Handed off</div><div class="v" id="capturehanded">—</div></div>
   <div class="kv"><div class="k">Pending sightings</div><div class="v" id="capturepending">—</div></div>
  </div>
  <div id="capturefile" class="reason">No active capture verified</div>
  <div id="capturewarning" class="reason">Waiting for a live supervisor</div>
 </section>
 <section class="card" data-page="doctor" hidden aria-label="Runtime diagnostics">
  <div class="k">Runtime diagnostics · measured state</div>
  <div class="row">
   <div class="kv"><div class="k">State</div><div class="v" id="runtimestate">UNKNOWN</div></div>
   <div class="kv"><div class="k">Free storage</div><div class="v" id="runtimefree">—</div></div>
   <div class="kv"><div class="k">Log resets</div><div class="v" id="runtimelogs">—</div></div>
  </div>
  <div id="runtimeerror" class="reason">Waiting for live diagnostics</div>
 </section>
 <div class="card" data-page="overview"><div class="row">
   <div class="kv"><div class="k">persona</div><div class="v" id="persona">—</div></div>
   <div class="kv"><div class="k">intent</div><div class="v" id="intent">—</div></div>
   <div class="kv"><div class="k">capture radio</div><div class="v" id="cap">—</div></div>
   <div class="kv"><div class="k">engine</div><div class="v" id="capeng">—</div></div>
   <div class="kv"><div class="k">sightings</div><div class="v" id="sight">—</div></div>
   <div class="kv"><div class="k">alerts</div><div class="v" id="alerts">—</div></div>
 </div>
 <svg id="spark" class="rich" viewBox="0 0 400 40" preserveAspectRatio="none"></svg></div>
 <div class="card" data-page="overview"><div class="k">brain recommends</div>
   <div class="v"><span class="badge" id="rec">—</span></div><div class="reason" id="recwhy"></div></div>
 <div class="card rich" data-page="radio" hidden id="sensecard" style="display:none"><div class="k">presence (CSI)</div>
   <div class="v"><span class="badge" id="sense">—</span> <span class="badge" id="occ">—</span></div>
   <div class="reason" id="sensewhy"></div></div>
 <div class="card" data-page="radio" hidden id="sentcard" style="display:none"><div class="k">sentinel</div>
   <div class="v"><span class="badge" id="sentarm">—</span> dispatched <span id="sentd">0</span> · suppressed <span id="sents">0</span></div>
   <div class="reason" id="sentlast"></div></div>
 <div class="card rich" data-page="radio" hidden><div class="k">airspace · channel occupancy / RSSI distribution</div>
   <svg id="chanbars" viewBox="0 0 400 92" preserveAspectRatio="none"></svg>
   <svg id="rssibars" viewBox="0 0 400 72" preserveAspectRatio="none"></svg>
   <div class="leg mut"><span id="airnote">no channel/RSSI data yet</span></div></div>
 <div class="card" data-page="radio" hidden><div class="k">access points · seen in range (from the Cache · listen-only)</div>
   <div class="aptbl" id="aptbl"></div>
   <div class="leg mut"><span id="apnote">no APs recorded yet</span></div></div>
 <div class="card" data-page="overview"><div class="k">recent narration</div><ul id="narr"></ul></div>
</main>
<script>
var TRACK=[],SPARK=[],lastSight=null,lastVisualSample=null;
var fetchPending=false,lastRuntimeVersion=null,lastVersionChanged=0;
function markStale(why){
 document.getElementById('sub').textContent='STALE · '+why;
 document.getElementById('doctorheart').className='doctor-heart unknown';
 document.getElementById('doctorheart').textContent='♡';
 document.getElementById('doctorlabel').textContent='UNKNOWN · STALE';
 document.getElementById('doctorcoverage').textContent=
  'Live health cannot be verified. Last known findings may be out of date.';
 const pipeline=document.getElementById('pipelinestate');
 pipeline.className='doctor-title unknown';
 pipeline.textContent='UNKNOWN · STALE';
 document.getElementById('pipelinereason').textContent=
  'Latest capture-processing reading is unverified until reconnection.';
}
var skin=(function(){try{return localStorage.getItem('augur.skin')||'rich'}catch(e){return 'rich'}})();
function applySkin(){document.body.setAttribute('data-skin',skin);
 var b=document.getElementById('skinbtn');b.textContent=skin;b.className=skin==='rich'?'on':''}
document.getElementById('skinbtn').onclick=function(){skin=(skin==='rich')?'plain':'rich';
 try{localStorage.setItem('augur.skin',skin)}catch(e){}applySkin()};
applySkin();
function setb(id,txt,cls){var e=document.getElementById(id);e.textContent=txt;e.className='badge'+(cls?' '+cls:'')}
function renderDoctor(report){
 const d=report&&Array.isArray(report.findings)?report:null;
 const modes=['ok','attention','degraded','action','unknown'];
 const mode=d&&modes.includes(d.overall)?d.overall:'unknown';
 const labels={ok:'OK',attention:'ATTENTION',degraded:'DEGRADED',
  action:'ACTION REQUIRED',unknown:'UNKNOWN'};
 const heart=document.getElementById('doctorheart');
 heart.className='doctor-heart '+mode;heart.textContent=mode==='ok'?'♥':'♡';
 document.getElementById('doctorlabel').textContent=labels[mode];
 const gaps=d&&d.coverage&&Array.isArray(d.coverage.not_assessed)
  ?d.coverage.not_assessed:null;
 document.getElementById('doctorcoverage').textContent=
  !d?'Doctor snapshot unavailable — health has not been assessed.'
  :gaps===null?'Doctor coverage is unverified.'
  :gaps.length?gaps.length+' unassessed area(s): '+gaps.join(', ')
  :'All reported Doctor areas assessed.';
 const list=document.getElementById('doctorlist');list.replaceChildren();
 if(!d)return;
 d.findings.forEach(function(f){
  const item=document.createElement('div');item.className='doctor-finding';
  const severity=modes.includes(f.status)?f.status:'unknown';
  const header=document.createElement('div');
  header.className='doctor-title '+severity;
  header.textContent=(f.area||'Unknown area')+' · '+severity.toUpperCase();
  item.appendChild(header);
  const reason=document.createElement('div');
  reason.textContent=(f.summary||'')+(f.reason?' — '+f.reason:'');
  item.appendChild(reason);
  if(f.remediation){const remedy=document.createElement('div');
   remedy.className='doctor-remedy';remedy.textContent='Action: '+f.remediation;
   item.appendChild(remedy);}
  list.appendChild(item);
 });
}
function renderPipeline(d){
 const p=d.capture_processing||{},doctor=d.doctor||{};
 const checks=Array.isArray(doctor.findings)?doctor.findings:[];
 const finding=checks.find(function(x){return x.area==='capture processing'})||null;
 const status=finding&&typeof finding.status==='string'?finding.status:'unknown';
 const state=document.getElementById('pipelinestate');
 state.className='doctor-title '+status;
 state.textContent=status.toUpperCase()+' · '+(finding?finding.summary:'Not assessed');
 const counts=document.getElementById('pipelinecounts');
 const recent=p.last_scan;
 if(recent&&typeof recent.scanned==='number'&&recent.outcomes){
  const outcome=Object.entries(recent.outcomes).map(function(pair){
   return pair[0]+': '+pair[1];
  }).join(' · ');
  counts.textContent='Last pass: '+recent.scanned+' file(s) · '+
   (outcome||'no processing outcomes');
 }else{
  counts.textContent=p.available?'No completed worker scan recorded':
   'Processing ledger unavailable';
 }
 document.getElementById('pipelinereason').textContent=
  finding?(finding.reason||''):(p.reason||'No verified worker status');
}
async function tick(){
 if(fetchPending||document.hidden)return; // never queue overlapping polls
 fetchPending=true;
 const ctrl=new AbortController();
 const deadline=setTimeout(function(){ctrl.abort()},5000);
 try{
  const r=await fetch('/api/status',{signal:ctrl.signal,cache:'no-store'});
  if(!r.ok)throw Error('HTTP '+r.status);
  const d=await r.json();
  paint(d);
  const version=d.runtime&&d.runtime.updated_utc;
  const now=performance.now();
  if(typeof version==='number'&&Number.isFinite(version)){
   if(version!==lastRuntimeVersion){lastRuntimeVersion=version;lastVersionChanged=now}
   if(now-lastVersionChanged>12000)markStale('no new supervisor checkpoint');
   else document.getElementById('sub').textContent='live · measured';
  }else document.getElementById('sub').textContent='glass-box · no runtime heartbeat';
 }catch(e){
  markStale(e&&e.name==='AbortError'?'request timed out':'connection unavailable');
 }finally{
  clearTimeout(deadline);fetchPending=false;
 }
}
function paint(d){
 renderDoctor(d.doctor||null);
 renderPipeline(d);
 var fe=document.getElementById('face'),nf=d.face||'‹·_·›';
 var base='face'+(d.face_state==='ruffle'?' crit':'')+(d.face_state==='blind'?' blinded':'');
 if(fe.textContent!==nf){fe.textContent=nf;fe.className=base;void fe.offsetWidth;fe.className=base+' pop';}
 else if(fe.className.replace(' pop','')!==base){fe.className=base;}
 document.getElementById('creature').textContent=d.creature||'…';
 document.getElementById('mood').textContent=d.face_reason||('mood: '+(d.mood||''));
 document.getElementById('persona').textContent=(d.persona||'(none)')+(d.posture?(' · '+d.posture):'');
 document.getElementById('intent').textContent=d.intent||'—';
 document.getElementById('cap').textContent=d.capture_iface||'none';
 document.getElementById('capeng').textContent=d.capture_engine||'none';
 document.getElementById('sight').textContent=d.sightings??'—';
 document.getElementById('alerts').textContent=d.recent_alerts??'—';
 const rc=d.recommendation||{};document.getElementById('rec').textContent=rc.intent||'(steady)';
 document.getElementById('recwhy').textContent=rc.reason||'';
 const se=d.sense,sc=document.getElementById('sensecard');
 if(se){sc.style.display='';setb('sense',se.sense||'—',se.sense==='motion'?'crit':'');
  document.getElementById('occ').textContent='occ: '+(se.occupancy||'—');
  document.getElementById('sensewhy').textContent=se.reason||''}else{sc.style.display='none'}
 const st=d.sentinel,stc=document.getElementById('sentcard');
 if(st){stc.style.display='';setb('sentarm',st.armed?'ARMED':'disarmed',st.armed?'warn':'');
  document.getElementById('sentd').textContent=st.dispatched??0;document.getElementById('sents').textContent=st.suppressed??0;
  document.getElementById('sentlast').textContent=st.last?('last: '+st.last.summary+' ['+st.last.severity+']'):''}
  else{stc.style.display='none'}
 const ul=document.getElementById('narr');ul.innerHTML='';
 (d.narration||[]).slice().reverse().forEach(t=>{const li=document.createElement('li');li.textContent=t;ul.appendChild(li)});
 if(d.position&&d.position.lat!=null){var p=TRACK[TRACK.length-1];
  if(!p||p.lat!==d.position.lat||p.lon!==d.position.lon){TRACK.push({lat:d.position.lat,lon:d.position.lon});if(TRACK.length>400)TRACK.shift()}}
 if(typeof d.sightings==='number'){if(lastSight!==null)SPARK.push(Math.max(0,d.sightings-lastSight));lastSight=d.sightings;if(SPARK.length>120)SPARK.shift()}
 const sample=d.runtime&&(typeof d.runtime.visual_revision==='number'
  ?d.runtime.visual_revision:d.runtime.visual_sampled_utc);
 const changed=typeof sample!=='number'||sample!==lastVisualSample;
 if(changed){
  lastVisualSample=typeof sample==='number'?sample:null;
  renderMap(d.located||[],d.position||null);
  renderSpark();renderAirspace(d.airspace||{});renderAPs(d.access_points||[]);
 }
}
function _bars(svgId,pairs,W,H,labEvery){const NS='http://www.w3.org/2000/svg',svg=document.getElementById(svgId);
 while(svg.firstChild)svg.removeChild(svg.firstChild);if(!pairs.length)return;
 var mx=Math.max(1,...pairs.map(p=>p[1])),n=pairs.length,bw=W/n;
 pairs.forEach(function(p,i){var h=(p[1]/mx)*(H-14),x=i*bw;
  var r=document.createElementNS(NS,'rect');r.setAttribute('x',(x+1).toFixed(1));r.setAttribute('y',(H-12-h).toFixed(1));
  r.setAttribute('width',Math.max(1,bw-2).toFixed(1));r.setAttribute('height',Math.max(0,h).toFixed(1));svg.appendChild(r);
  if(i%labEvery===0){var t=document.createElementNS(NS,'text');t.setAttribute('x',(x+bw/2).toFixed(1));t.setAttribute('y',H-2);
   t.setAttribute('text-anchor','middle');t.textContent=p[0];svg.appendChild(t)}});}
function renderAirspace(a){var ch=a.channels||{},rs=a.rssi||{};
 var cp=Object.keys(ch).map(k=>[+k,ch[k]]).sort((x,y)=>x[0]-y[0]);
 var rp=Object.keys(rs).map(k=>[+k,rs[k]]).sort((x,y)=>x[0]-y[0]);
 _bars('chanbars',cp.map(p=>[String(p[0]),p[1]]),400,92,1);
 _bars('rssibars',rp.map(p=>[p[0]+'dBm',p[1]]),400,72,1);
 var note=document.getElementById('airnote');
 note.textContent=cp.length?(cp.length+' channels · '+rp.length+' RSSI buckets · real sightings only'):'no channel/RSSI data yet';
}
function renderAPs(aps){var el=document.getElementById('aptbl'),note=document.getElementById('apnote');
 el.innerHTML='';
 if(!aps.length){note.textContent='no APs recorded yet';return}
 aps=aps.slice().sort(function(a,b){return (b.rssi==null?-999:b.rssi)-(a.rssi==null?-999:a.rssi)});
 ['CH','BSSID','SIG','SSID'].forEach(function(h){var d=document.createElement('div');d.className='h';d.textContent=h;el.appendChild(d)});
 function cell(t,cls){var d=document.createElement('div');if(cls)d.className=cls;d.textContent=t;el.appendChild(d)}
 aps.forEach(function(a){cell(a.channel!=null?String(a.channel):'-','c');cell(a.bssid||'—');
  cell(a.rssi!=null?(a.rssi+''):'—','c');cell(a.ssid||'<hidden>')});
 note.textContent=aps.length+' AP'+(aps.length>1?'s':'')+' · strongest first · real sightings only';
}
function renderMap(pts,pos){const NS='http://www.w3.org/2000/svg',svg=document.getElementById('map'),rng=document.getElementById('maprange');
 while(svg.firstChild)svg.removeChild(svg.firstChild);
 var all=pts.map(p=>[p.lat,p.lon]).concat(TRACK.map(t=>[t.lat,t.lon]));if(pos&&pos.lat!=null)all.push([pos.lat,pos.lon]);
 if(!all.length){var t=document.createElementNS(NS,'text');t.setAttribute('x',12);t.setAttribute('y',24);
  t.textContent='no located sightings yet — needs a GPS fix';svg.appendChild(t);rng.textContent='';return}
 const W=400,H=340,P=22;var la=all.map(a=>a[0]),lo=all.map(a=>a[1]);
 var laMin=Math.min(...la),laMax=Math.max(...la),loMin=Math.min(...lo),loMax=Math.max(...lo);
 var laSpan=(laMax-laMin)||1e-4,loSpan=(loMax-loMin)||1e-4;
 function X(lon){return P+((lon-loMin)/loSpan)*(W-2*P)}function Y(lat){return P+(1-(lat-laMin)/laSpan)*(H-2*P)}
 if(TRACK.length>1){var d='';TRACK.forEach((t,i)=>{d+=(i?'L':'M')+X(t.lon).toFixed(1)+' '+Y(t.lat).toFixed(1)+' '});
  var pl=document.createElementNS(NS,'path');pl.setAttribute('d',d);pl.setAttribute('class','track');svg.appendChild(pl)}
 pts.forEach(p=>{var c=document.createElementNS(NS,'circle');c.setAttribute('cx',X(p.lon).toFixed(1));c.setAttribute('cy',Y(p.lat).toFixed(1));
  c.setAttribute('r','3.4');c.setAttribute('fill',p.kind==='ble'?'var(--ble)':'var(--wifi)');c.setAttribute('fill-opacity','.85');
  var ti=document.createElementNS(NS,'title');var when=p.ts?(' · '+new Date(p.ts*1000).toISOString().slice(0,19).replace('T',' ')):'';
  ti.textContent=(p.kind||'?')+' '+(p.ssid||p.mac||'')+when+' @ '+p.lat.toFixed(5)+','+p.lon.toFixed(5);c.appendChild(ti);svg.appendChild(c)});
 if(pos&&pos.lat!=null){var g=document.createElementNS(NS,'g');
  var ring=document.createElementNS(NS,'circle');ring.setAttribute('cx',X(pos.lon).toFixed(1));ring.setAttribute('cy',Y(pos.lat).toFixed(1));
  ring.setAttribute('r','4');ring.setAttribute('fill','none');ring.setAttribute('stroke','var(--me)');ring.setAttribute('class','ping');g.appendChild(ring);
  var me=document.createElementNS(NS,'circle');me.setAttribute('cx',X(pos.lon).toFixed(1));me.setAttribute('cy',Y(pos.lat).toFixed(1));
  me.setAttribute('r','4');me.setAttribute('fill','var(--me)');g.appendChild(me);svg.appendChild(g)}
 rng.textContent='· '+pts.length+' located · '+(TRACK.length>1?TRACK.length+' track pts':'no track yet');
}
function renderSpark(){const NS='http://www.w3.org/2000/svg',svg=document.getElementById('spark');
 while(svg.firstChild)svg.removeChild(svg.firstChild);if(!SPARK.length)return;
 var mx=Math.max(1,...SPARK),n=SPARK.length,d='';for(var i=0;i<n;i++){var x=(i/(n-1||1))*400,y=40-(SPARK[i]/mx)*36-2;d+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' '}
 var pl=document.createElementNS(NS,'path');pl.setAttribute('d',d);pl.setAttribute('fill','none');pl.setAttribute('stroke','var(--acc)');pl.setAttribute('stroke-width','1.3');svg.appendChild(pl)}
tick();setInterval(tick,2000);
document.addEventListener('visibilitychange',function(){if(!document.hidden)tick()});
</script></body></html>"""


def render_page() -> str:
    return PAGE


# A tiny, self-contained unlock page shown when a token is required and absent.
# It stores the token in a scoped cookie (so the dashboard's own fetch carries it)
# and reloads — the token never rides in a URL, where it would leak to logs/history.
LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Augur</title><style>
:root{--bg:#0a0e13;--fg:#e6edf3;--mut:#8b98a5;--acc:#4ec9b0;--line:#1f2a35;--card:#121922;
--mono:ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 var(--mono);display:grid;place-items:center;min-height:100vh}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:26px;width:300px;max-width:92vw}
.face{font-size:40px;color:var(--acc);text-align:center;margin-bottom:6px}
h1{font-size:18px;margin:0 0 2px;letter-spacing:1px}.mut{color:var(--mut);font-size:12px;margin:0 0 14px}
input,button{width:100%;font:14px var(--mono);padding:9px 11px;border-radius:8px;border:1px solid var(--line);margin-top:8px}
input{background:#0b1118;color:var(--fg)}button{background:#16202a;color:var(--acc);border-color:var(--acc);cursor:pointer}
</style></head><body>
<form id="f"><div class="face">&#8249;-_-&#8250;</div><h1>Augur</h1>
<p class="mut">this dashboard is protected — enter the access token</p>
<input id="t" type="password" placeholder="access token" autofocus autocomplete="off">
<button type="submit">unlock</button></form>
<script>
document.getElementById('f').onsubmit=function(e){e.preventDefault();
 var t=document.getElementById('t').value.trim();if(!t)return;
 document.cookie='augur_token='+encodeURIComponent(t)+';path=/;max-age=86400;samesite=strict';
 location.replace('/');};
</script></body></html>"""


def auth_token(bind_scope: str, token: Optional[str] = None) -> Optional[str]:
    """The effective access token for a bind scope. An explicit token always
    wins. On `localhost` (single-user loopback) auth is off by default; any wider
    exposure is fail-closed — a token is required, and one is minted here if you
    didn't supply one (serve() prints it so you can reach the dashboard)."""
    if token:
        return token
    if bind_scope == "localhost":
        return None
    return secrets.token_urlsafe(18)


def make_handler(status_provider: Callable[[], Dict], token: Optional[str] = None):
    """Build a request handler for `/` and `/api/status`.

    If `token` is set, every route is gated: `/api/status` needs it (401 without),
    and `/` serves the unlock page until the cookie is present. Token travels via
    an `Authorization: Bearer <token>` header or an `augur_token` cookie, compared
    in constant time. `token=None` means open (the localhost default)."""
    def _present(headers) -> Optional[str]:
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        raw = headers.get("Cookie", "")
        if raw:
            try:
                c = http.cookies.SimpleCookie(raw)
                if "augur_token" in c:
                    return c["augur_token"].value
            except Exception:
                return None
        return None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            if not token:
                return True
            got = _present(self.headers)
            # compare as bytes: hmac.compare_digest rejects non-ASCII str, and the
            # presented value is attacker-controlled — a bad/odd token must 401, not crash
            return bool(got) and hmac.compare_digest(got.encode("utf-8", "replace"),
                                                      token.encode("utf-8"))

        def do_GET(self):
            authed = self._authed()
            if self.path.rstrip("/") == "/api/status" or self.path == "/api/status":
                if not authed:
                    self._send(401, b'{"error":"unauthorized"}', "application/json")
                    return
                self._send(200, json.dumps(status_provider()).encode(), "application/json")
            elif self.path == "/" or self.path == "":
                if not authed:
                    self._send(401, LOGIN_PAGE.encode(), "text/html; charset=utf-8")
                    return
                self._send(200, render_page().encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *a):  # quiet by default
            pass
    return Handler


def resolve_host(bind_scope: str) -> str:
    return _SCOPE_HOST.get(bind_scope, "127.0.0.1")


def serve(augur, port: int = 8080, bind_scope: str = "localhost",
          interval: float = 2.0, pump: bool = True, token: Optional[str] = None,
          _cycles=None):
    """Run the dashboard (foreground loop). The HTTP server runs in a daemon thread
    and serves a cached snapshot; this thread owns the SightingStore, so it is the
    only one that pumps and recomputes the snapshot (SQLite is single-thread). Logs
    the exact URL. `_cycles` bounds the loop for tests; otherwise runs until Ctrl-C."""
    host = resolve_host(bind_scope)
    tok = auth_token(bind_scope, token)
    holder = {"d": status_payload(augur)}
    httpd = ThreadingHTTPServer((host, port), make_handler(lambda: holder["d"], token=tok))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{host}:{port}/"
    _log.info("Augur web dashboard on %s (bind_scope=%s, auth=%s)", url, bind_scope, "on" if tok else "off")
    print(f"Augur web dashboard: {url}  (bind_scope={bind_scope})")
    if tok:
        print(f"  access token: {tok}")
        print(f"  reach it: open the page and paste the token, or")
        print(f"            curl -H 'Authorization: Bearer {tok}' {url}api/status")
        if not token:
            print("  (auto-generated because this bind is reachable off-box — set your own with --token)")
    n = 0
    try:
        while _cycles is None or n < _cycles:
            if pump:
                augur.pump()
            holder["d"] = status_payload(augur)   # store touched only in this thread
            n += 1
            if _cycles is None or n < _cycles:
                # Governor stretches the cadence under heat/battery load (1.0 until
                # real readings say otherwise), so hot/low-power = slower loop = fewer writes.
                scale = augur.govern_scale() if hasattr(augur, "govern_scale") else 1.0
                time.sleep(interval * scale)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return httpd
