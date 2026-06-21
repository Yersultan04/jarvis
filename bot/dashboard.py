"""Веб-дашборд Sana Corp (Ф3) — живой «кто чем занят».

Лёгкий Flask, читает шину состояния (events.StateStore) + флаг автономии и рисует
сетку агентов: за столом (работает) / на диване (idle). Это визуальный мост к
3D-офису (Ф4). Запускается ПОТОКОМ внутри бота (без отдельного сервиса), слушает
127.0.0.1 — смотреть с ноута через SSH-туннель.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("jarvis.dashboard")

_PAGE = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Sana Corp</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b0f17;color:#e6edf3;font:15px/1.4 system-ui,Segoe UI,Roboto,sans-serif;padding:24px}
h1{font-size:22px;font-weight:700;margin-bottom:4px}
.sub{color:#8b98a9;font-size:13px;margin-bottom:20px}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px}
.on{background:#10381f;color:#3fb950}.off{background:#3a1115;color:#f85149}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-top:8px}
.card{background:#11161f;border:1px solid #1f2733;border-radius:12px;padding:14px;transition:.2s}
.card.busy{border-color:#2ea043;box-shadow:0 0 0 1px #2ea04340,0 6px 18px #2ea04318}
.card.couch{opacity:.5}
.nm{font-weight:700;font-size:15px}.role{color:#8b98a9;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.st{margin-top:10px;font-size:13px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.detail{color:#8b98a9;font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.foot{color:#5b6675;font-size:11px;margin-top:22px}
</style></head><body>
<h1>🏢 Sana Corp</h1>
<div class=sub>живой статус команды · <span id=auto></span> · <span id=clock></span></div>
<div class=grid id=grid></div>
<div class=foot>обновление каждые 3с · источник: шина событий на VM</div>
<script>
const ICON={idle:"🛋",working:"🟢",coordinating:"🧭",reviewing:"👁",testing:"🧪",meeting:"💬",done:"✅",error:"⚠️"};
const COLOR={idle:"#5b6675",working:"#2ea043",coordinating:"#58a6ff",reviewing:"#d29922",testing:"#a371f7",meeting:"#58a6ff",done:"#2ea043",error:"#f85149"};
async function tick(){
 try{
  const r=await fetch('/api/state'); const d=await r.json();
  const a=d.autonomy||{};
  document.getElementById('auto').innerHTML = a.enabled
    ? `<span class="pill on">автономия ВКЛ</span> ${a.done_today||0}/${a.max||10} сегодня`
    : `<span class="pill off">автономия ВЫКЛ</span>`;
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ru-RU');
  const g=document.getElementById('grid'); g.innerHTML='';
  for(const ag of d.agents){
   const busy = ag.state!=='idle' && ag.state!=='done';
   const el=document.createElement('div');
   el.className='card '+(busy?'busy':'couch');
   el.innerHTML=`<div class=nm>${ICON[ag.state]||'•'} ${ag.name}</div>
     <div class=role>${ag.role||''}</div>
     <div class=st><span class=dot style="background:${COLOR[ag.state]||'#5b6675'}"></span>${busy?ag.state:'на диване'}</div>
     ${ag.detail?`<div class=detail title="${ag.detail}">${ag.detail}</div>`:''}`;
   g.appendChild(el);
  }
 }catch(e){}
}
tick(); setInterval(tick,3000);
</script></body></html>"""


def run_dashboard(store, autonomy_state_file, roles, roster, host: str, port: int) -> None:
    """Запустить Flask-дашборд (блокирующе — вызывать в потоке-демоне)."""
    try:
        from flask import Flask, Response, jsonify
    except ImportError:
        logger.warning("flask не установлен — дашборд выключен")
        return

    app = Flask(__name__)

    @app.get("/")
    def index() -> Response:
        return Response(_PAGE, mimetype="text/html")

    @app.get("/api/state")
    def state():
        snap = store.snapshot()
        agents = [
            {"name": a, "role": roles.get(a, ""),
             "state": snap.get(a, {}).get("state", "idle"),
             "detail": snap.get(a, {}).get("detail", "")}
            for a in roster
        ]
        auto = {}
        try:
            auto = json.loads(Path(autonomy_state_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return jsonify({"agents": agents, "autonomy": auto})

    logger.info("дашборд: http://%s:%d", host, port)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
