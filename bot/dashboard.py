"""Веб-дашборд / живой офис Sana Corp (Ф3 + Ф4).

Лёгкий Flask, читает шину состояния (events.StateStore) + флаг автономии.
- "/"      → ЖИВОЙ ОФИС (Ф4): агенты-токены по зонам (столы / переговорка / лаундж),
             плавно скользят между зонами по состоянию. Премиум тёмный вид.
- "/grid"  → простая сетка-карточки (Ф3).
- "/api/state" → JSON снимка (общий для обоих).

Запускается ПОТОКОМ внутри бота, слушает 127.0.0.1 — смотреть с ноута по SSH-туннелю.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("jarvis.dashboard")


# ---------------------------------------------------------------- ЖИВОЙ ОФИС (Ф4)
_OFFICE = r"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Sana Corp · офис</title>
<link rel=preconnect href=https://fonts.googleapis.com>
<link rel=preconnect href=https://fonts.gstatic.com crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=Manrope:wght@400;500;600&display=swap" rel=stylesheet>
<style>
:root{
  --bg:#070a12; --bg2:#0b1020; --panel:rgba(255,255,255,.04); --line:rgba(255,255,255,.08);
  --ink:#eaf0f7; --mut:#7d8aa3; --accent:#34d399; --accent2:#38bdf8;
  --desk:#141b2c; --floor:#0a0f1c;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:
    radial-gradient(1200px 700px at 70% -10%, #15233f55, transparent 60%),
    radial-gradient(900px 600px at 0% 110%, #10241f55, transparent 60%),
    var(--bg);
  color:var(--ink); font-family:Manrope,system-ui,sans-serif; overflow:hidden;
}
.top{display:flex;align-items:center;gap:18px;padding:18px 26px}
.brand{font-family:Sora,sans-serif;font-weight:700;font-size:20px;letter-spacing:.3px;display:flex;align-items:center;gap:10px}
.brand .mk{width:11px;height:11px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px var(--accent)}
.spacer{flex:1}
.pill{font-size:12.5px;font-weight:600;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--mut)}
.pill b{color:var(--ink)}
.pill.on{color:#062;background:#34d39922;border-color:#34d39955;color:#7ff0c2}
.pill.off{background:#f8514922;border-color:#f8514955;color:#ffb4ad}
.clock{font-variant-numeric:tabular-nums;color:var(--mut);font-size:13px;font-weight:600;margin-left:8px}
.btn{font-family:Manrope,sans-serif;font-size:12.5px;font-weight:600;color:var(--ink);cursor:pointer;
  padding:7px 13px;border-radius:999px;border:1px solid var(--line);background:var(--panel);transition:.2s}
.btn:hover{border-color:var(--accent);color:var(--accent);box-shadow:0 0 14px #34d39933}
.btn.stop:hover{border-color:#f85149;color:#ff9d96;box-shadow:0 0 14px #f8514933}
.btn:active{transform:scale(.96)}
.btn.busy{opacity:.5;pointer-events:none}

.stage-wrap{position:relative;width:100%;height:calc(100vh - 74px);display:flex;align-items:center;justify-content:center}
.stage{position:relative;width:1180px;height:660px;max-width:97vw;
  transform-origin:center; }
.floor{position:absolute;inset:0;border-radius:26px;
  background:
    linear-gradient(180deg,#0c1322,#080d18);
  border:1px solid var(--line);
  box-shadow:0 40px 120px #0009, inset 0 1px 0 #ffffff0a;
  overflow:hidden}
.floor:before{content:"";position:absolute;inset:0;opacity:.5;
  background-image:linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff05 1px,transparent 1px);
  background-size:40px 40px}

/* зоны */
.zone{position:absolute;border:1px solid var(--line);border-radius:18px;background:#ffffff03}
.zone .zlbl{position:absolute;top:12px;left:14px;font-family:Sora,sans-serif;font-size:11px;font-weight:600;
  letter-spacing:2px;text-transform:uppercase;color:var(--mut)}
.zone.work{left:34px;top:64px;width:680px;height:360px}
.zone.meet{right:34px;top:64px;width:398px;height:360px}
.zone.lounge{left:34px;right:34px;bottom:30px;top:452px;width:auto}

/* мебель */
.desk{position:absolute;width:120px;height:64px;border-radius:10px;background:linear-gradient(180deg,#16203400,#0000);
  border:1px solid var(--line)}
.desk .top{position:absolute;inset:8px;border-radius:7px;background:linear-gradient(180deg,#1a2540,#121a2e);box-shadow:inset 0 1px 0 #ffffff10}
.desk .mon{position:absolute;left:50%;top:-7px;transform:translateX(-50%);width:34px;height:8px;border-radius:3px;
  background:#0d1322;border:1px solid var(--line);transition:.4s}
.desk.busy .mon{background:linear-gradient(90deg,var(--accent2),var(--accent));box-shadow:0 0 12px var(--accent2)}
.table{position:absolute;left:50%;top:54%;transform:translate(-50%,-50%);width:188px;height:188px;border-radius:50%;
  background:radial-gradient(circle at 50% 35%,#1a2440,#0e1526);border:1px solid var(--line);
  box-shadow:inset 0 2px 16px #0008}
.couch{position:absolute;left:34px;bottom:26px;width:330px;height:62px;border-radius:14px;
  background:linear-gradient(180deg,#202c49,#16203a);border:1px solid var(--line);box-shadow:inset 0 6px 14px #0006}
.couch:before,.couch:after{content:"";position:absolute;top:-10px;width:30px;height:24px;border-radius:10px 10px 0 0;background:#202c49;border:1px solid var(--line)}
.couch:before{left:-1px}.couch:after{right:-1px}
.tv{position:absolute;right:40px;bottom:30px;width:230px;height:120px;border-radius:12px;border:1px solid var(--line);
  background:linear-gradient(135deg,#0a1120,#0a1426);overflow:hidden;box-shadow:0 0 0 6px #0000000d, 0 18px 40px #0007}
.tv:before{content:"";position:absolute;inset:0;background:
  linear-gradient(115deg,transparent 30%,#38bdf833 50%,transparent 70%);
  animation:flick 5s ease-in-out infinite}
.tv .lbl{position:absolute;left:12px;bottom:9px;font-size:10px;letter-spacing:1px;color:#5f7298;font-family:Sora,sans-serif}
@keyframes flick{0%,100%{transform:translateX(-30%)}50%{transform:translateX(30%)}}

/* агенты */
.agent{position:absolute;left:0;top:0;width:96px;margin-left:-48px;margin-top:-58px;text-align:center;
  transition:transform 1.1s cubic-bezier(.6,.05,.2,1);pointer-events:auto;z-index:5}
.av{position:relative;width:50px;height:50px;margin:0 auto;border-radius:50%;display:grid;place-items:center;
  font-family:Sora,sans-serif;font-weight:700;font-size:17px;color:#06121f;
  box-shadow:0 8px 20px #0007, inset 0 1px 0 #ffffff55;transition:.4s}
.av .ring{position:absolute;inset:-4px;border-radius:50%;border:2px solid transparent;transition:.4s}
.agent.busy .ring{border-color:var(--accent);box-shadow:0 0 16px var(--accent)}
.agent.busy .av{animation:pulse 2.2s ease-in-out infinite}
.agent.idle{animation:bob 4s ease-in-out infinite}
.nm{margin-top:7px;font-size:12px;font-weight:600;color:var(--ink);white-space:nowrap}
.rl{font-size:9.5px;letter-spacing:1px;text-transform:uppercase;color:var(--mut)}
.sdot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;vertical-align:middle}
.tip{position:absolute;left:50%;top:-30px;transform:translateX(-50%);background:#0d1424;border:1px solid var(--line);
  color:var(--ink);font-size:11px;padding:4px 9px;border-radius:8px;white-space:nowrap;opacity:0;transition:.2s;pointer-events:none;max-width:240px;overflow:hidden;text-overflow:ellipsis}
.agent:hover .tip{opacity:1}
@keyframes bob{0%,100%{margin-top:-58px}50%{margin-top:-64px}}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.06)}}
@media (prefers-reduced-motion:reduce){.agent,.agent.idle,.agent.busy .av,.tv:before{animation:none!important;transition:none!important}}
</style></head><body>
<div class=top>
  <div class=brand><span class=mk></span> Sana&nbsp;Corp</div>
  <span class=pill id=auto>—</span>
  <span class=pill id=counts>—</span>
  <div class=spacer></div>
  <button class=btn id=btnGo>▶ Включить авто</button>
  <button class="btn stop" id=btnStop>■ Стоп</button>
  <button class=btn id=btnRun>⚡ Цикл сейчас</button>
  <span class=clock id=clock></span>
</div>
<div class=stage-wrap><div class=stage id=stage>
  <div class=floor></div>
  <div class="zone work"><span class=zlbl>Work Area</span></div>
  <div class="zone meet"><span class=zlbl>Meeting Room</span><div class=table></div></div>
  <div class="zone lounge"><span class=zlbl>Lounge</span><div class=couch></div><div class=tv><span class=lbl>● LIVE</span></div></div>
</div></div>

<script>
const ROLECOL={Sana:"#34d399",Maya:"#38bdf8",Kai:"#a78bfa",Shield:"#f472b6",Leo:"#fbbf24",
 Zara:"#fb7185",Vex:"#f87171",Nova:"#22d3ee",Alex:"#4ade80",Rex:"#facc15","Макс":"#e879f9",Sora:"#60a5fa"};
const ICON={idle:"🛋",working:"💻",coordinating:"🧭",reviewing:"👁",testing:"🧪",meeting:"💬",done:"✅",error:"⚠️"};
const WORK=new Set(["working","coordinating"]), MEET=new Set(["reviewing","testing","meeting"]);
function zoneOf(s){ if(WORK.has(s))return "work"; if(MEET.has(s))return "meet"; return "lounge"; }

// слоты-координаты (центры) внутри сцены 1180x660
const SLOTS={
  work:[[150,150],[360,150],[570,150],[150,300],[360,300],[570,300]],
  meet:[[915,120],[1050,180],[1050,300],[915,360],[800,300],[800,180]],
  lounge:[[120,560],[230,560],[340,560],[470,575],[560,575],[650,575],
          [740,575],[120,500],[230,500],[340,500],[450,500],[560,500]],
};
const DESKS=[[150,150],[360,150],[570,150],[150,300],[360,300],[570,300]];
const stage=document.getElementById('stage');
const tokens={};

function ensureDesks(){
  if(stage.querySelector('.desk'))return;
  for(const [x,y] of DESKS){
    const d=document.createElement('div'); d.className='desk';
    d.style.left=(x-60)+'px'; d.style.top=(y-18)+'px';
    d.innerHTML='<div class=top></div><div class=mon></div>';
    stage.appendChild(d);
  }
}
function deskAt(i,busy){
  const desks=stage.querySelectorAll('.desk');
  if(desks[i]) desks[i].classList.toggle('busy',busy);
}

function token(a){
  if(tokens[a.name])return tokens[a.name];
  const el=document.createElement('div'); el.className='agent';
  const col=ROLECOL[a.name]||'#9aa7bd';
  el.innerHTML=`<div class=tip></div>
    <div class=av style="background:radial-gradient(circle at 35% 30%, ${col}, ${col}bb)">
      <span class=ring></span>${(a.name[0]||'?')}</div>
    <div class=nm>${a.name}</div>
    <div class=rl><span class=sdot></span><span class=stt></span></div>`;
  stage.appendChild(el); tokens[a.name]=el; return el;
}

async function tick(){
 try{
  const d=await (await fetch('/api/state')).json();
  const a=d.autonomy||{};
  document.getElementById('auto').className='pill '+(a.enabled?'on':'off');
  document.getElementById('auto').innerHTML=a.enabled?`● автономия ВКЛ · <b>${a.done_today||0}/${a.max||10}</b>`:'○ автономия ВЫКЛ';
  document.getElementById('clock').textContent=new Date().toLocaleTimeString('ru-RU');

  const byZone={work:[],meet:[],lounge:[]};
  for(const ag of d.agents) byZone[zoneOf(ag.state)].push(ag);

  let working=0, meeting=0;
  stage.querySelectorAll('.desk').forEach(x=>x.classList.remove('busy'));
  for(const z of ['work','meet','lounge']){
    byZone[z].forEach((ag,i)=>{
      const slot=SLOTS[z][i%SLOTS[z].length];
      const el=token(ag);
      const busy=ag.state!=='idle'&&ag.state!=='done';
      el.style.transform=`translate(${slot[0]}px,${slot[1]}px)`;
      el.classList.toggle('busy',busy);
      el.classList.toggle('idle',!busy);
      const col=ROLECOL[ag.name]||'#9aa7bd';
      el.querySelector('.sdot').style.background=busy?'var(--accent)':'#5b6675';
      el.querySelector('.stt').textContent=busy?ag.state:ag.role;
      el.querySelector('.tip').textContent=ag.detail?`${ag.name}: ${ag.detail}`:`${ag.name} · ${busy?ag.state:'свободен'}`;
      if(z==='work'){ deskAt(i,busy); working++; }
      if(z==='meet') meeting++;
    });
  }
  document.getElementById('counts').innerHTML=`💻 <b>${working}</b> за столами · 👁 <b>${meeting}</b> в переговорке · 🛋 <b>${byZone.lounge.length}</b> отдыхают`;
 }catch(e){}
}
async function ctrl(action){
  try{ await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}); }
  catch(e){} tick();
}
document.getElementById('btnGo').onclick=()=>ctrl('go');
document.getElementById('btnStop').onclick=()=>ctrl('stop');
document.getElementById('btnRun').onclick=(e)=>{
  const b=e.target; b.classList.add('busy'); b.textContent='⚡ запускаю…';
  ctrl('run').finally(()=>setTimeout(()=>{b.classList.remove('busy');b.textContent='⚡ Цикл сейчас';},4000));
};
ensureDesks(); tick(); setInterval(tick,3000);
</script></body></html>"""


# ---------------------------------------------------------------- простая сетка (Ф3)
_GRID = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Sana Corp · сетка</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0b0f17;color:#e6edf3;font:15px/1.4 system-ui,sans-serif;padding:24px}
h1{font-size:20px}.sub{color:#8b98a9;font-size:13px;margin:4px 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.card{background:#11161f;border:1px solid #1f2733;border-radius:12px;padding:14px}.card.busy{border-color:#2ea043}.card.couch{opacity:.5}
.nm{font-weight:700}.role{color:#8b98a9;font-size:11px;text-transform:uppercase}.st{margin-top:10px;font-size:13px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}.detail{color:#8b98a9;font-size:12px;margin-top:4px}</style></head>
<body><h1>🏢 Sana Corp — сетка</h1><div class=sub id=auto></div><div class=grid id=g></div>
<script>const I={idle:"🛋",working:"🟢",coordinating:"🧭",reviewing:"👁",testing:"🧪",meeting:"💬",done:"✅",error:"⚠️"};
const C={idle:"#5b6675",working:"#2ea043",coordinating:"#58a6ff",reviewing:"#d29922",testing:"#a371f7",meeting:"#58a6ff",done:"#2ea043",error:"#f85149"};
async function t(){try{const d=await(await fetch('/api/state')).json();const a=d.autonomy||{};
document.getElementById('auto').textContent=(a.enabled?'автономия ВКЛ · '+(a.done_today||0)+'/'+(a.max||10):'автономия ВЫКЛ');
const g=document.getElementById('g');g.innerHTML='';for(const x of d.agents){const b=x.state!=='idle'&&x.state!=='done';
const e=document.createElement('div');e.className='card '+(b?'busy':'couch');
e.innerHTML=`<div class=nm>${I[x.state]||'•'} ${x.name}</div><div class=role>${x.role||''}</div>
<div class=st><span class=dot style="background:${C[x.state]||'#5b6675'}"></span>${b?x.state:'на диване'}</div>${x.detail?`<div class=detail>${x.detail}</div>`:''}`;
g.appendChild(e);}}catch(e){}}t();setInterval(t,3000);</script></body></html>"""


def run_dashboard(store, autonomy_state_file, roles, roster, host: str, port: int,
                  control_cb=None) -> None:
    """Запустить Flask живой офис + сетку (блокирующе — вызывать в потоке-демоне).

    control_cb(action) — колбэк для кнопок сайта: "go" / "stop" / "run".
    """
    try:
        from flask import Flask, Response, jsonify, request
    except ImportError:
        logger.warning("flask не установлен — дашборд выключен")
        return

    app = Flask(__name__)

    @app.post("/api/control")
    def control():
        action = (request.get_json(silent=True) or {}).get("action", "")
        if control_cb and action in ("go", "stop", "run"):
            try:
                control_cb(action)
            except Exception:  # noqa: BLE001
                logger.exception("control_cb failed")
            return jsonify({"ok": True, "action": action})
        return jsonify({"ok": False}), 400

    @app.get("/")
    def office() -> "Response":
        return Response(_OFFICE, mimetype="text/html")

    @app.get("/grid")
    def grid() -> "Response":
        return Response(_GRID, mimetype="text/html")

    @app.get("/api/state")
    def state():
        snap = store.snapshot()
        agents = [
            {"name": a, "role": roles.get(a, ""),
             "state": snap.get(a, {}).get("state", "idle"),
             "detail": snap.get(a, {}).get("detail", "")}
            for a in roster
        ]
        auto = {"max": 10}
        try:
            auto.update(json.loads(Path(autonomy_state_file).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return jsonify({"agents": agents, "autonomy": auto})

    logger.info("офис Sana Corp: http://%s:%d", host, port)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
