"""Live local SVG view of backend-reported or USB-serial ESP-NOW topology."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import queue
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
NAMES_PATH = ROOT / "data" / "worker_names.json"
NAMES_LOCK = threading.Lock()


def load_worker_names():
    try:
        data = json.loads(NAMES_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_worker_name(target: str, device_id: str, name: str):
    with NAMES_LOCK:
        names = load_worker_names()
        names[target] = name
        if device_id:
            names[device_id] = name
        NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        NAMES_PATH.write_text(json.dumps(names, indent=2) + "\n")
    return names
sys.path.insert(0, str(ROOT))

from backend.config import Settings


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HeatSentinel mesh topology</title>
<style>
:root{color-scheme:dark;font:15px system-ui,sans-serif;background:#090d12;color:#e8edf4}*{box-sizing:border-box}body{margin:0;padding:24px}header{display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap}h1{font-size:24px;margin:0 0 5px}.sub,.muted{color:#91a0b3}.state{border:1px solid #263446;border-radius:999px;padding:8px 13px;color:#b9c8d8}.state.ok{color:#79e0a8;border-color:#24593d}.state.err{color:#ff9c9c;border-color:#713d3d}.card{margin-top:20px;background:#101720;border:1px solid #263446;border-radius:14px;padding:14px;overflow:auto;min-height:280px}svg{display:block;min-width:720px;width:100%;height:auto}.edge{fill:none;stroke:#57708a;stroke-width:3}.edge.new{stroke:#52e3a2;stroke-width:5;filter:drop-shadow(0 0 5px #52e3a2)}.edge.unknown{stroke:#b7833e;stroke-dasharray:8 7}.node rect{fill:#182330;stroke:#465d76;stroke-width:2;rx:12}.node.gateway rect{fill:#14283a;stroke:#4badea}.node.online rect{stroke:#45cb89}.node.offline rect{stroke:#bd6262;stroke-dasharray:7 5}.node.new rect{filter:drop-shadow(0 0 10px #47dfa0);stroke:#52e3a2;stroke-width:4}.title{font-weight:700;font-size:15px;fill:#eff5fc}.detail{font-size:12px;fill:#a8b6c7}.badge{font-size:11px;font-weight:700;fill:#6ee0a3}.notice{margin:16px 4px 4px;color:#a9b7c9;min-height:24px}.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;color:#aab7c6;font-size:13px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:#4badea}.dot.green{background:#45cb89}.dot.red{background:#bd6262}.dot.dash{background:#b7833e}footer{margin-top:18px;color:#78889c;font-size:12px}
</style>
<style>.node{cursor:pointer}.node.selected rect{stroke:#f2c75c;stroke-width:4}.detail-card{margin-top:16px;background:#101720;border:1px solid #263446;border-radius:14px;padding:18px}.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}.metric{padding:12px;background:#0b1118;border-radius:10px}.metric label{display:block;color:#91a0b3;font-size:12px;margin-bottom:5px}.metric strong{font-size:17px}.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.controls button{background:#1a2735;border:1px solid #43566c;border-radius:8px;color:#e8edf4;padding:9px 13px;font-weight:650;cursor:pointer}.controls button:hover{border-color:#80b4e6}.controls .red{color:#ff8a8a}.controls .green{color:#79e0a8}.controls .yellow{color:#ffda74}</style></head><body>
<header><div><h1>HeatSentinel mesh topology</h1><div class="sub" id="source">Live view from the connected ESP32</div></div><div id="status" class="state">Connecting…</div></header>
<div class="card"><svg id="graph" viewBox="0 0 1000 360" role="img" aria-label="ESP32 mesh topology"></svg></div>
<div id="notice" class="notice">Waiting for mesh telemetry…</div>
<section class="detail-card"><h2 id="detail-title" style="margin:0 0 14px;font-size:18px">Select a worker to inspect its readings</h2><div class="controls" id="name-controls" hidden><label for="worker-name">Worker name</label><input id="worker-name" maxlength="48" placeholder="e.g. Adam" style="background:#0b1118;border:1px solid #43566c;border-radius:8px;color:#e8edf4;padding:9px 11px"><button id="save-name">Save name</button><span id="name-result" class="muted" style="align-self:center"></span></div><div id="detail-grid" class="detail-grid"></div><p class="muted">G/Y/R send complete, clearly labeled simulated demo scenarios. A switches to real worker sensors.</p><div class="controls" id="worker-controls" hidden><button class="green" data-mode="green">G · Green test (default)</button><button class="yellow" data-mode="yellow">Y · Caution test</button><button class="red" data-mode="red">R · Critical buzzer test</button><button data-mode="auto">A · Real sensors</button></div><div class="controls"><span class="muted" style="align-self:center">All workers:</span><button data-all-mode="green">G</button><button data-all-mode="yellow">Y</button><button class="red" data-all-mode="red">R</button><button data-all-mode="auto">A</button><span id="control-result" class="muted" style="align-self:center"></span></div></section>
<div class="legend"><span><i class="dot"></i>Gateway / master</span><span><i class="dot green"></i>Online worker</span><span><i class="dot red"></i>Offline or stale</span><span><i class="dot dash"></i>Reachable, parent unknown</span></div>
<footer>Refreshes every second. This graph shows reported radio links, not worker GPS or physical position.</footer>
<script>
const $=id=>document.getElementById(id);let previous=new Set(),newlyJoined=new Set(),flashUntil=0,lastSignature='',initialized=false,selectedId='';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function nodeKey(n){return String(n?.nodeId??'');}
function heatIndexC(t,r){if(!Number.isFinite(+t)||!Number.isFinite(+r)||+t<27||+r<0||+r>100)return null;const f=+t*1.8+32,simple=.5*(f+61+(f-68)*1.2+ +r*.094),avg=(simple+f)/2;if(avg<80)return null;let hi=-42.379+2.04901523*f+10.14333127*r-.22475541*f*r-.00683783*f*f-.05481717*r*r+.00122874*f*f*r+.00085282*f*r*r-.00000199*f*f*r*r;if(+r<13&&f>=80&&f<=112)hi-=((13-r)/4)*Math.sqrt((17-Math.abs(f-95))/17);else if(+r>85&&f>=80&&f<=87)hi+=((r-85)/10)*((87-f)/5);return (hi-32)/1.8}
function render(data){
 $('source').textContent=data.source==='esp-now-serial'?'Live ESP-NOW radio topology · USB serial':data.source==='painlessmesh-serial'?'Live mesh topology · USB serial':'Live view from gateway-reported backend topology';
 const items=(data.items||[]).filter(d=>d.source==='hardware');
 const byNode=new Map(items.map(d=>[String(d.latest?.mesh_node_id??''),d]).filter(x=>x[0]));
 const gateway=items.find(d=>d.latest?.mesh_role==='gateway');
 const tree=gateway?.latest?.mesh_topology;
 const nodes=[],edges=[],included=new Set();
 function walk(t,parent=null,depth=0){if(!t||t.nodeId==null)return;const id=nodeKey(t);nodes.push({id,depth,device:byNode.get(id),root:!!t.root||id===String(gateway?.latest?.mesh_node_id??''),tree:t});included.add(id);if(parent)edges.push({a:parent,b:id});for(const child of (Array.isArray(t.subs)?t.subs:[]))walk(child,id,depth+1)}
 if(tree)walk(tree);
 const connectedItems=items.filter(d=>d.latest?.mesh_role==='worker'&&d.latest?.mesh_connected);
 if(!nodes.length&&gateway){const id=String(gateway.latest?.mesh_node_id??`device:${gateway.device_id}`);nodes.push({id,depth:0,device:gateway,root:true});included.add(id);for(const d of connectedItems){const wid=String(d.latest?.mesh_node_id??`device:${d.device_id}`);if(wid===id)continue;nodes.push({id:wid,depth:1,device:d,root:false});edges.push({a:id,b:wid,unknown:true});included.add(wid)}}
 for(const d of items){const meshId=d.latest?.mesh_node_id;const id=String(meshId??`device:${d.device_id}`);if(!included.has(id)){nodes.push({id,depth:1,device:d,root:d.latest?.mesh_role==='gateway',orphan:true});included.add(id)}}
 const ids=new Set(nodes.map(n=>n.id));const added=initialized?[...ids].filter(id=>!previous.has(id)):[];if(!initialized){initialized=true;$('notice').textContent=tree?'Current gateway topology loaded.':'Current devices loaded; waiting for gateway topology.'}else if(added.length){newlyJoined=new Set(added);flashUntil=Date.now()+3500;const names=added.map(id=>{const n=nodes.find(x=>x.id===id);return n?.device?.worker_name||n?.device?.device_id||`node ${id}`});$('notice').textContent=`Joined / newly detected: ${names.join(', ')}. Topology refreshed.`}else if(Date.now()>flashUntil){newlyJoined.clear();$('notice').textContent=tree?'Topology is current.':'Waiting for gateway topology; showing reported mesh reachability where available.'}
 const leaves=new Map();function leafCount(n){const kids=(n.tree?.subs||[]).filter(c=>nodes.some(x=>x.id===nodeKey(c)));const count=kids.reduce((a,c)=>a+leafCount(nodes.find(x=>x.id===nodeKey(c))),0)||1;leaves.set(n.id,count);return count}
 const root=nodes.find(n=>n.root)||nodes[0];if(tree&&root)leafCount(root);
 const nodeW=205,nodeH=76,gapX=58,gapY=118;let totalLeaves=tree&&root?leaves.get(root.id):nodes.length;totalLeaves=Math.max(1,totalLeaves||1);
 let viewW=Math.max(760,totalLeaves*(nodeW+gapX)+100),maxDepth=Math.max(1,...nodes.map(n=>n.depth));let viewH=120+maxDepth*gapY+nodeH;let cursor=50;const pos=new Map();
 function place(n){const kids=(n.tree?.subs||[]).map(c=>nodes.find(x=>x.id===nodeKey(c))).filter(Boolean);let x;if(kids.length){kids.forEach(place);x=kids.reduce((s,k)=>s+pos.get(k.id).x,0)/kids.length}else{x=cursor+nodeW/2;cursor+=nodeW+gapX}const y=40+n.depth*gapY;pos.set(n.id,{x,y})}
 if(tree&&root)place(root);else{nodes.forEach((n,i)=>pos.set(n.id,{x:100+nodeW/2+i*(nodeW+gapX),y:45+n.depth*gapY}));viewW=Math.max(viewW,nodes.length*(nodeW+gapX)+100)}
 $('graph').setAttribute('viewBox',`0 0 ${viewW} ${viewH}`);
 const svg=[];
 for(const e of edges){const a=pos.get(e.a),b=pos.get(e.b);if(!a||!b)continue;const y1=a.y+nodeH,y2=b.y,mid=(y1+y2)/2;svg.push(`<path class="edge ${e.unknown?'unknown':''} ${newlyJoined.has(e.b)&&Date.now()<flashUntil?'new':''}" d="M ${a.x} ${y1} V ${mid} H ${b.x} V ${y2}"/>`)}
 for(const n of nodes){const p=pos.get(n.id);if(!p)continue;const d=n.device,online=d?.connection_state==='online',label=n.root?'Site Manager':(d?.worker_name||d?.device_id||`Mesh node ${n.id}`);const role=n.root?'MASTER NODE':(d?.latest?.mesh_role||'NODE').toUpperCase();const state=d?(online?'ONLINE':'OFFLINE'):'NODE ID ONLY';const meshId=d?.latest?.mesh_node_id;const idText=meshId==null?'mesh ID unavailable':`mesh node ${meshId}`;const cls=['node',n.root?'gateway':'',online?'online':'offline',selectedId===n.id?'selected':'',newlyJoined.has(n.id)&&Date.now()<flashUntil?'new':''].join(' ');svg.push(`<g class="${cls}" data-node-id="${esc(n.id)}" tabindex="0" role="button" aria-label="Select ${esc(label)}"><rect x="${p.x-nodeW/2}" y="${p.y}" width="${nodeW}" height="${nodeH}"/><text class="badge" x="${p.x-nodeW/2+14}" y="${p.y+19}">${esc(role)} · ${esc(state)}</text><text class="title" x="${p.x-nodeW/2+14}" y="${p.y+42}">${esc(label).slice(0,27)}</text><text class="detail" x="${p.x-nodeW/2+14}" y="${p.y+61}">${esc(idText)}</text></g>`)}
 $('graph').innerHTML=svg.join('')||`<text x="28" y="80" class="detail">No ESP32 devices have reported yet.</text>`;
 $('graph').querySelectorAll('[data-node-id]').forEach(el=>el.addEventListener('click',()=>{selectedId=el.getAttribute('data-node-id');render(data)}));
 const selected=nodes.find(n=>n.id===selectedId),detail=$('detail-grid'),controlBox=$('worker-controls');
 if(!selected){$('detail-title').textContent='Select a worker to inspect its readings';detail.innerHTML='<div class="muted">Click any worker node to see its latest sensor payload and demo controls.</div>';controlBox.hidden=true}
 else if(selected.root){$('detail-title').textContent='Site Manager · sensorless gateway';detail.innerHTML=`<div class="metric"><label>Mesh node</label><strong>${esc(selected.id)}</strong></div><div class="metric"><label>Connected workers</label><strong>${nodes.filter(n=>!n.root).length}</strong></div><div class="metric"><label>Uplink</label><strong>${esc(selected.device?.latest?.uplink||'mesh / Wi-Fi bridge')}</strong></div>`;controlBox.hidden=true;$('name-controls').hidden=true}
 else {const d=selected.device||{},t=d.latest||{},offline=d.connection_state!=='online',name=d.worker_name||d.device_id||`Worker ${selected.id}`;$('detail-title').textContent=offline?`${name} · DEVICE OFFLINE`:`${name} · live worker data`;$('name-controls').hidden=false;const input=$('worker-name');if(input.dataset.target!==String(selected.id)){input.value=name;input.dataset.target=String(selected.id);input.dataset.deviceId=d.backend_device_id||d.device_id||'';}if(offline){detail.innerHTML='<div class="metric" style="grid-column:1/-1;border:1px solid #713d3d"><strong style="color:#ff9c9c">DEVICE OFFLINE</strong><div class="muted" style="margin-top:6px">No current sensor readings are available. Reconnect the worker to restore live data.</div></div>';controlBox.hidden=true}else{const fmt=(v,s='')=>{if(v===null||v===undefined||!Number.isFinite(Number(v)))return 'Unavailable';const n=Number(v),shown=s.includes('%')?String(Math.round(n)):String(Number(n.toFixed(2)));return `${shown}${s}`},duration=v=>{if(v===null||v===undefined)return 'Unavailable';const n=Number(v);return `${Math.floor(n/3600)}h ${Math.floor(n%3600/60)}m ${n%60}s`},quality=t.pulse_quality==='simulated'?' (simulated)':t.pulse_quality==='experimental'?' (experimental)':t.pulse_quality?' ('+t.pulse_quality+')':'',hi=t.heat_index_c!==null&&t.heat_index_c!==undefined&&Number.isFinite(Number(t.heat_index_c))?Number(t.heat_index_c):heatIndexC(t.temperature_c,t.humidity_pct);detail.innerHTML=`<div class="metric"><label>Device ID</label><strong>${esc(d.device_id||'Unavailable')}</strong></div><div class="metric"><label>Temperature</label><strong>${esc(fmt(t.temperature_c,' °C'))}${t.demo_scenario_simulated?' (simulated)':''}</strong></div><div class="metric"><label>Humidity</label><strong>${esc(fmt(t.humidity_pct,' %'))}${t.demo_scenario_simulated?' (simulated)':''}</strong></div><div class="metric"><label>Calculated heat index</label><strong>${hi===null?'Unavailable (<27 °C or missing data)':esc(fmt(hi,' °C'))}${t.demo_scenario_simulated?' (simulated)':''}</strong></div><div class="metric"><label>Heat-index category</label><strong>${esc(t.heat_index_category||'Unavailable')}</strong></div><div class="metric"><label>Site UV index · ${esc(t.site_uv_index_source==='simulated_demo'?'simulated demo':'weather')}</label><strong>${esc(fmt(t.site_uv_index))}${t.site_uv_index_source==='simulated_demo'?' (simulated)':''}</strong></div><div class="metric"><label>Demo risk score</label><strong>${esc(fmt(t.risk_score_demo,'/100'))}${t.demo_scenario_simulated?' (simulated)':''}</strong></div><div class="metric"><label>Scenario data</label><strong>${esc(t.demo_scenario_simulated?`${String(t.demo_scenario||'').toUpperCase()} · simulated`: 'real sensors / weather')}</strong></div><div class="metric"><label>Alert level / mode</label><strong>${esc(t.alert_level||'unknown')} / ${esc(t.alert_mode||'auto')}</strong></div><div class="metric"><label>Heart rate</label><strong>${esc(fmt(t.pulse_bpm,' BPM')+quality)}</strong><small class="muted"> · raw ${t.demo_scenario_simulated?esc(fmt(t.pulse_raw,' ADC')+' (simulated)'):esc(fmt(t.pulse_raw,' ADC'))}</small></div><div class="metric"><label>Photoresistor brightness</label><strong>${esc(fmt(t.light_intensity_pct,' %'))}${t.demo_scenario_simulated?' (simulated)':''}</strong><small class="muted"> · raw ${esc(fmt(t.light_raw,' ADC'))}, threshold ${esc(fmt(t.bright_light_threshold_pct,' %'))}; ${t.bright_light_active?'BRIGHT':'below threshold'}</small></div><div class="metric"><label>Bright-light exposure · estimated</label><strong>${esc(duration(t.bright_exposure_seconds))}</strong><small class="muted"> · continuous ${esc(duration(t.bright_continuous_seconds))}</small></div><div class="metric"><label>Elevated-heat exposure</label><strong>${esc(duration(t.heat_exposure_seconds))}</strong><small class="muted"> · continuous ${esc(duration(t.heat_continuous_seconds))}</small></div><div class="metric"><label>Activity / acclimatization</label><strong>${esc(t.activity_level||'unknown')} / ${esc(t.acclimatization_status||'unknown')}</strong></div><div class="metric"><label>Sensor validity / mesh</label><strong>${t.demo_scenario_simulated?'Simulated demo scenario':t.environment_sensor_ok===false?'Environment unavailable':t.environment_sensor_ok===true?'Environment OK':'Awaiting payload'} · ${esc(d.connection_state||'unknown')}</strong></div><div class="metric"><label>Why this alert</label><strong>${esc(t.alert_explanation||'Waiting for risk assessment')}</strong></div><div class="metric"><label>Recommended action</label><strong>${esc(t.recommended_action||'Waiting for risk assessment')}</strong></div>`;controlBox.hidden=false}}
 const sig=[...ids].sort().join(',')+'|'+edges.map(e=>e.a+'>'+e.b).sort().join(',');if(sig!==lastSignature){lastSignature=sig;if(!added.length&&previous.size)$('notice').textContent='Mesh links changed. Topology refreshed.'}previous=ids;
 const count=items.filter(d=>d.connection_state==='online').length;const badge=$('status');badge.className='state '+(gateway&&gateway.connection_state==='online'?'ok':'');badge.textContent=`${count}/${items.length} ESP32 online · ${edges.length} links`;
}
async function sendControl(mode,target){const result=$('control-result');result.textContent='Sending…';try{const r=await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,target})});const body=await r.json();if(!r.ok)throw new Error(body.error||`HTTP ${r.status}`);result.textContent=`${mode.toUpperCase()} sent to ${target}`}catch(e){result.textContent=String(e.message||e)}}
async function saveWorkerName(){const input=$('worker-name'),result=$('name-result'),name=input.value.trim();if(!name){result.textContent='Enter a name first.';return}result.textContent='Saving…';try{const r=await fetch('/name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:input.dataset.target,device_id:input.dataset.deviceId,name})});const body=await r.json();if(!r.ok)throw new Error(body.error||`HTTP ${r.status}`);result.textContent=body.backend_updated?'Name saved':'Name saved on this computer';poll()}catch(e){result.textContent=String(e.message||e)}}
document.addEventListener('click',e=>{if(e.target.id==='save-name')saveWorkerName()});
document.addEventListener('click',e=>{const button=e.target.closest('button');if(!button)return;if(button.dataset.mode&&selectedId)sendControl(button.dataset.mode,selectedId);if(button.dataset.allMode)sendControl(button.dataset.allMode,'all')});
async function poll(){try{const r=await fetch('/topology.json',{cache:'no-store'});if(!r.ok)throw new Error((await r.json()).error||`HTTP ${r.status}`);render(await r.json())}catch(e){const s=$('status');s.className='state err';s.textContent='Topology source unavailable';$('notice').textContent=String(e.message||e)}setTimeout(poll,1000)}poll();
</script></body></html>'''


class SerialTopology:
    """Read mesh topology from either firmware's master serial output."""

    HEADER = re.compile(r"\[TOPOLOGY\] revision=(\d+) role=(\w+) master=([0-9A-Fa-f]+)")
    NODE = re.compile(r'"([0-9A-Fa-f]+)" \[label="(.*?)\\n(.*?)"\];')
    EDGE = re.compile(r'"([0-9A-Fa-f]+)" -> "([0-9A-Fa-f]+)";')
    GATEWAY = re.compile(r"\[MESH\] role=gateway node=(\d+) known_nodes=(\d+) uplink=(.+)")
    MESH_RX = re.compile(r"\[MESH RX\] (?:sender=|queue full; dropped sender=)(\d+)")
    MESH_DATA = re.compile(r"\[MESH DATA\] sender=(\d+) payload=(\{.*\})")
    MESH_OFFLINE = re.compile(r"\[MESH\] node offline id=([0-9A-Fa-f]+)")

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.lock = threading.Lock()
        self.state = {"master": None, "nodes": {}, "edges": [], "revision": 0,
                      "last_line_at": 0, "source": "esp-now-serial"}
        self.error = "waiting for the ESP32 serial stream"
        self.commands = queue.Queue()

    def start(self):
        thread = threading.Thread(target=self._read_forever, daemon=True)
        thread.start()

    def _read_forever(self):
        try:
            import serial
        except ImportError:
            self.error = "pyserial is missing; install requirements-dev.txt"
            return
        while True:
            try:
                with serial.Serial(self.port, self.baud, timeout=1) as device:
                    self.error = ""
                    print(f"[serial] opened {self.port} at {self.baud} baud")
                    while True:
                        while True:
                            try:
                                device.write(self.commands.get_nowait())
                            except queue.Empty:
                                break
                        line = device.readline().decode("utf-8", errors="ignore").strip()
                        if line:
                            self._parse(line)
            except Exception as error:
                self.error = f"serial read failed ({type(error).__name__})"
                print(f"[serial] {self.error}: {error}")
                time.sleep(2)

    def _parse(self, line: str):
        with self.lock:
            self.state["last_line_at"] = time.time()
            header = self.HEADER.search(line)
            if header:
                # A topology refresh only changes links. Keep the most recent
                # worker telemetry while the gateway is reporting membership.
                self.state.update(revision=int(header.group(1)), role=header.group(2),
                                  master=header.group(3).upper(), edges=[])
                return
            node = self.NODE.search(line)
            if node:
                node_id, name, status = node.groups()
                current = self.state["nodes"].setdefault(node_id.upper(), {"id": node_id.upper()})
                current.update({"name": name, "status": status.upper()})
                return
            edge = self.EDGE.search(line)
            if edge:
                pair = [edge.group(1).upper(), edge.group(2).upper()]
                if pair not in self.state["edges"]:
                    self.state["edges"].append(pair)
                return

            gateway = self.GATEWAY.search(line)
            if gateway:
                node_id = format(int(gateway.group(1)), "X")
                self.state.update(master=node_id, source="painlessmesh-serial")
                current = self.state["nodes"].setdefault(node_id, {"id": node_id})
                current.update({
                    "id": node_id, "name": "Site Manager", "status": "MASTER",
                    "last_seen": time.time(), "known_nodes": int(gateway.group(2)),
                    "uplink": gateway.group(3),
                })
                return

            received = self.MESH_RX.search(line)
            if received:
                worker_id = format(int(received.group(1)), "X")
                master_id = self.state.get("master")
                # MESH RX is a packet-arrival marker; do not erase the last
                # complete payload while the following MESH DATA line is read.
                current = self.state["nodes"].setdefault(worker_id, {"id": worker_id})
                current.update({
                    "id": worker_id, "name": f"Worker {worker_id[-4:]}",
                    "status": "ONLINE", "last_seen": time.time(),
                })
                if master_id and master_id != worker_id:
                    pair = [master_id, worker_id]
                    if pair not in self.state["edges"]:
                        self.state["edges"].append(pair)
                self.state["revision"] += 1
                return

            data_match = self.MESH_DATA.search(line)
            if data_match:
                try:
                    telemetry = json.loads(data_match.group(2))
                except json.JSONDecodeError:
                    return
                worker_id = format(int(data_match.group(1)), "X")
                node = self.state["nodes"].setdefault(worker_id, {
                    "id": worker_id, "name": telemetry.get("worker_name") or f"Worker {worker_id[-4:]}",
                    "status": "ONLINE", "last_seen": time.time(),
                })
                node["last_seen"] = time.time()
                node["telemetry"] = telemetry
                node["name"] = telemetry.get("worker_name") or telemetry.get("device_id") or node["name"]
                return

            offline = self.MESH_OFFLINE.search(line)
            if offline:
                node_id = offline.group(1).upper()
                if node_id in self.state["nodes"]:
                    self.state["nodes"][node_id]["status"] = "STALE"
                    self.state["nodes"][node_id]["last_seen"] = 0
                return

    def payload(self):
        with self.lock:
            snapshot = json.loads(json.dumps(self.state))
        master = snapshot.get("master")
        if not master:
            raise RuntimeError(self.error or "Waiting for a mesh master topology update")
        nodes = snapshot.get("nodes", {})
        if master not in nodes:
            nodes[master] = {"id": master, "name": "Site Manager", "status": "MASTER"}
        edge_by_child = {child: parent for parent, child in snapshot.get("edges", [])}
        children = {}
        for parent, child in snapshot.get("edges", []):
            children.setdefault(parent, []).append(child)

        def make_tree(node_id, visited):
            if node_id in visited:
                return None
            visited = visited | {node_id}
            node = nodes.get(node_id, {"id": node_id, "name": "Worker", "status": "ONLINE"})
            result = {"nodeId": int(node_id, 16), "root": node_id == master, "subs": []}
            for child_id in children.get(node_id, []):
                child = make_tree(child_id, visited)
                if child:
                    result["subs"].append(child)
            return result

        items = []
        now = time.time()
        for node_id, node in nodes.items():
            is_master = node_id == master
            node_tree = make_tree(master, set()) if is_master else None
            online = is_master or node.get("status") != "STALE"
            if snapshot.get("source") == "painlessmesh-serial" and not is_master:
                online = now - node.get("last_seen", 0) <= 20
            telemetry = node.get("telemetry") or {}
            latest = dict(telemetry)
            latest.update({"mesh_role": "gateway" if is_master else "worker",
                           "mesh_connected": True, "mesh_node_id": int(node_id, 16),
                           "mesh_topology": node_tree,
                           "uplink": node.get("uplink") if is_master else None})
            names = load_worker_names()
            alias = names.get(node_id) or names.get(f"mesh-{node_id.lower()}") or names.get(telemetry.get("device_id", ""))
            items.append({
                "device_id": f"mesh-{node_id.lower()}",
                "worker_name": "Site Manager" if is_master else (alias or telemetry.get("worker_name") or node.get("name") or telemetry.get("device_id") or f"Worker {node_id[-4:]}"),
                "backend_device_id": telemetry.get("device_id"),
                "source": "hardware",
                "connection_state": "online" if online else "offline",
                "latest": latest,
            })
        return {"source": snapshot.get("source", "esp-now-serial"), "items": items,
                "revision": snapshot.get("revision", 0)}

    def send_demo_control(self, mode: str, target: str):
        if mode not in {"green", "yellow", "red", "auto"}:
            raise ValueError("mode must be green, yellow, red, or auto")
        if target != "all" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target):
            raise ValueError("target must be all or a worker device/node ID")
        if not self.state.get("master"):
            raise RuntimeError("No Site Manager has been detected on the serial port")
        self.commands.put(f"{mode[0]} {target}\n".encode("ascii"))


def make_handler(api_url: str, dashboard_key: str, serial_topology=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif self.path == "/topology.json":
                try:
                    if serial_topology:
                        data = serial_topology.payload()
                    else:
                        request = Request(api_url.rstrip("/") + "/v1/devices",
                                          headers={"X-Dashboard-Key": dashboard_key})
                        with urlopen(request, timeout=3) as response:
                            data = json.loads(response.read().decode("utf-8"))
                        data = {"items": data.get("items", []), "source": "backend"}
                        names = load_worker_names()
                        for item in data["items"]:
                            latest = item.get("latest") or {}
                            alias = names.get(str(latest.get("mesh_node_id", ""))) or names.get(item.get("device_id", ""))
                            if alias:
                                item["worker_name"] = alias
                    body = json.dumps(data, separators=(",", ":")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                except RuntimeError as error:
                    body = json.dumps({"error": str(error)}).encode()
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                    body = json.dumps({"error": f"Backend request failed: {type(error).__name__}"}).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                body = b"Not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/name":
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 2048)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    target = str(payload.get("target", ""))[:64]
                    device_id = str(payload.get("device_id", ""))[:64]
                    name = str(payload.get("name", "")).strip()[:48]
                    if not target or not name:
                        raise ValueError("A worker and a name are required")
                    save_worker_name(target, device_id, name)
                    backend_updated = False
                    if device_id:
                        request = Request(api_url.rstrip("/") + "/v1/devices/" + device_id,
                                          data=json.dumps({"worker_name": name}).encode(),
                                          headers={"Content-Type": "application/json",
                                                   "X-Dashboard-Key": dashboard_key},
                                          method="PATCH")
                        try:
                            with urlopen(request, timeout=3) as response:
                                backend_updated = response.status < 300
                        except (HTTPError, URLError, TimeoutError, OSError):
                            pass
                    body = json.dumps({"saved": True, "backend_updated": backend_updated}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                except (ValueError, json.JSONDecodeError) as error:
                    body = json.dumps({"error": str(error)}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/control" or serial_topology is None:
                body = json.dumps({"error": "Control requires the USB-connected Site Manager graph reader"}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 1024)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    mode = str(payload.get("mode", "")).lower()
                    target = str(payload.get("target", "all"))
                    serial_topology.send_demo_control(mode, target)
                    body = json.dumps({"mode": mode, "target": target, "queued": True}).encode()
                    self.send_response(202)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                except (ValueError, RuntimeError, json.JSONDecodeError) as error:
                    body = json.dumps({"error": str(error)}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Live SVG graph of ESP32 mesh topology")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--serial-port", help="Read the connected mesh master directly over USB serial")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()
    serial_topology = SerialTopology(args.serial_port, args.baud) if args.serial_port else None
    dashboard_key = Settings.from_env().dashboard_key
    if serial_topology:
        serial_topology.start()
        print(f"Reading mesh master serial: {args.serial_port} at {args.baud} baud")
    else:
        print("Polling the backend every second. Press Ctrl+C to stop.")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.api_url, dashboard_key, serial_topology))
    print(f"Mesh topology viewer: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMesh topology viewer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
