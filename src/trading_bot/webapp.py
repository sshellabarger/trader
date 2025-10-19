from __future__ import annotations
import time
from typing import Any, Dict
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from .state import list_events, get_positions, get_health, get_kv, set_kv
from .settings import get_settings, update_settings

app = FastAPI(title="Trading Bot Web UI", version="1.2")

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Trading Bot</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    :root { --bg:#0f1115; --card:#151924; --muted:#9aa4b2; --text:#e6ebf2; --accent:#4da3ff; --good:#37c977; --bad:#ff6b6b; --warn:#ffb020; --border:#232838; }
    *{ box-sizing:border-box } body{ margin:0; padding:16px; background:var(--bg); color:var(--text); font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }
    h1{ font-size:22px; margin:0 0 12px } h3{ margin:0 0 8px; font-size:16px } .muted{ color:var(--muted); font-size:12px }
    .row{ display:grid; grid-template-columns:repeat(5, minmax(160px, 1fr)); gap:10px } .wrap{ display:grid; grid-template-columns:1fr 1fr; gap:12px; align-items:start }
    .card{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:12px; box-shadow:0 6px 20px rgb(0 0 0 / 25%) }
    .section-actions{ display:flex; align-items:baseline; justify-content:space-between; gap:10px } .controls{ display:grid; grid-template-columns:1fr 1fr; gap:12px }
    label{ display:inline-block; font-size:12px; color:var(--muted); margin-bottom:4px } input[type="range"]{ width:100% }
    input[type="number"], input[type="text"]{ background:#0b0f17; color:#e6ebf2; border:1px solid var(--border); padding:6px 8px; border-radius:8px; width:100% }
    input[type="checkbox"]{ transform: translateY(1px) } button{ background:var(--accent); color:#00142a; border:none; padding:8px 10px; border-radius:10px; font-weight:600; cursor:pointer }
    button.secondary{ background:#253049; color:#e6ebf2 } table{ width:100%; border-collapse:collapse; font-size:13px }
    thead th{ text-align:left; font-weight:600; color:var(--muted); border-bottom:1px solid var(--border); padding:6px 4px }
    tbody td{ border-bottom:1px dashed rgba(255,255,255,0.05); padding:6px 4px; vertical-align:top } .tight{ width:1%; white-space:nowrap }
    .ok{ color:var(--good); font-weight:600 } .bad{ color:var(--bad); font-weight:600 } .warn{ color:var(--warn); font-weight:600 }
    .toolbar{ display:flex; align-items:center; gap:10px; margin-bottom:12px } .pill{ background:#0b0f17; border:1px solid var(--border); border-radius:999px; padding:4px 10px; font-size:12px; color:var(--muted) }
    .grid-2{ display:grid; grid-template-columns:1fr 1fr; gap:12px } @media(max-width:1100px){ .wrap,.controls,.row,.grid-2{ grid-template-columns:1fr } }
  </style>
</head>
<body>
  <div class="toolbar">
    <h1>Trading Bot</h1>
    <span class="pill">Auto refresh: <span id="countdown">20</span>s</span>
    <button id="refreshBtn" class="secondary">Refresh now</button>
    <span id="saveMsg" class="muted"></span>
  </div>

  <div class="controls card">
    <div class="card">
      <h3>Strategies</h3>
      <label><input type="checkbox" id="momentum"> Momentum</label><br/>
      <label><input type="checkbox" id="mean_reversion"> Mean-reversion</label><br/>
      <label><input type="checkbox" id="vwap"> VWAP filter</label><br/>
      <label><input type="checkbox" id="news"> News</label><br/>
      <label><input type="checkbox" id="earnings"> Earnings</label><br/>
      <label><input type="checkbox" id="longterm_trend"> Long-term Trend</label><br/>
      <label><input type="checkbox" id="longterm_momentum"> Long-term Momentum</label><br/>
      <label><input type="checkbox" id="crypto_enabled"> Crypto Enabled</label>
    </div>

    <div class="card">
      <h3>Thresholds & Weights</h3>
      <div class="grid-2">
        <div><label>Enter <span id="enter_val"></span></label><input type="range" id="enter" min="0.40" max="0.95" step="0.01"></div>
        <div><label>Exit <span id="exit_val"></span></label><input type="range" id="exit"  min="0.25" max="0.90" step="0.01"></div>
        <div><label>Momentum wt <span id="w_mom_val"></span></label><input type="range" id="w_mom" min="0.0" max="1.0" step="0.05"></div>
        <div><label>Mean-rev wt <span id="w_mr_val"></span></label><input type="range" id="w_mr" min="0.0" max="1.0" step="0.05"></div>
        <div><label>News wt <span id="w_news_val"></span></label><input type="range" id="w_news" min="0.0" max="1.0" step="0.05"></div>
        <div><label>Earnings wt <span id="w_er_val"></span></label><input type="range" id="w_er" min="0.0" max="1.0" step="0.05"></div>
        <div><label>Long-term wt <span id="w_lt_val"></span></label><input type="range" id="w_lt" min="0.0" max="1.0" step="0.05"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Schedulers &amp; Data</h3>
    <div class="row">
      <div><label>Candidate max</label><input type="number" id="cand_max" min="20" max="500" step="10"></div>
      <div><label>Candidate refresh (min)</label><input type="number" id="cand_min" min="5" max="120" step="1"></div>
      <div><label>News interval (sec)</label><input type="number" id="news_sec" min="60" max="3600" step="30"></div>
      <div><label>Earnings refresh (min)</label><input type="number" id="earn_min" min="15" max="720" step="15"></div>
      <div><label>Long-term refresh (min)</label><input type="number" id="lt_min" min="60" max="1440" step="30"></div>
    </div>
    <label style="margin-top:8px; display:block"><input type="checkbox" id="batch_only"> Batch snapshots only (no per-symbol fallback)</label>
  </div>
  
  <div style="margin-top:12px; display:flex; gap:8px">
    <button id="saveBtn">Save settings</button>
    <span id="saveMsg" class="muted"></span>
  </div>

  <div class="wrap" style="margin-top:12px">
    <div class="card">
      <h3>Positions</h3>
      <div style="max-height: 320px; overflow:auto">
        <table id="pos_tbl">
          <thead><tr><th class="tight">Symbol</th><th class="tight">Qty</th><th>Avg Entry</th><th class="tight">Updated</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="section-actions">
        <h3>Candidates</h3>
        <span class="muted">Top movers prioritized for quotes/trades</span>
      </div>
      <div style="max-height: 380px; overflow:auto">
        <table id="cand_tbl">
          <thead><tr><th class="tight">Symbol</th><th class="tight">Mover</th><th>Mid</th><th>News</th><th class="tight">Earnings</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="section-actions">
        <h3>Health</h3>
        <div>
          <button id="runHealthBtn" class="secondary" title="Trigger a full health check now">Run health now</button>
          <span class="muted" id="health_last_line" style="margin-left:8px;">Last run: —</span>
          <span class="muted" id="health_run_msg" style="margin-left:8px;"></span>
        </div>
      </div>
      <div style="max-height: 280px; overflow:auto">
        <table id="health_tbl">
          <thead><tr><th class="tight">Check</th><th class="tight">OK</th><th>Detail</th><th class="tight">TS</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="section-actions"><h3>Status (events)</h3><span class="muted">Latest activity, warnings, and errors</span></div>
      <div style="max-height: 380px; overflow:auto">
        <table id="evt_tbl">
          <thead><tr><th class="tight">TS</th><th class="tight">Level</th><th>Message</th><th>Meta</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="muted" style="margin-top:6px">Tip: set <code>LOG_LEVEL=INFO</code> to suppress successful HTTP calls; WARN/ERROR will still appear.</div>
    </div>
  </div>

<script>
  const el = id => document.getElementById(id);
  const tbody = id => el(id).querySelector('tbody');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const momentum = el('momentum'), mean_reversion = el('mean_reversion'), vwap = el('vwap'),
        news = el('news'), earnings = el('earnings'),
        longterm_trend = el('longterm_trend'), longterm_momentum = el('longterm_momentum'),
        crypto_enabled = el('crypto_enabled');

  const enter = el('enter'), exit = el('exit'), enter_val = el('enter_val'), exit_val = el('exit_val');
  const w_mom = el('w_mom'), w_mr = el('w_mr'), w_news = el('w_news'), w_er = el('w_er'), w_lt = el('w_lt');
  const w_mom_val = el('w_mom_val'), w_mr_val = el('w_mr_val'), w_news_val = el('w_news_val'), w_er_val = el('w_er_val'), w_lt_val = el('w_lt_val');

  const cand_max = el('cand_max'), cand_min = el('cand_min'), news_sec = el('news_sec'), earn_min = el('earn_min'), lt_min = el('lt_min'), batch_only = el('batch_only');

  const refreshBtn = el('refreshBtn'), saveBtn = el('saveBtn'), saveMsg = el('saveMsg');
  const runHealthBtn = el('runHealthBtn'), health_last_line = el('health_last_line'), health_run_msg = el('health_run_msg');

  function bindLiveLabels() {
    enter.oninput = e => enter_val.textContent = e.target.value;
    exit.oninput  = e => exit_val.textContent = e.target.value;
    w_mom.oninput = e => w_mom_val.textContent = e.target.value;
    w_mr.oninput  = e => w_mr_val.textContent = e.target.value;
    w_news.oninput= e => w_news_val.textContent = e.target.value;
    w_er.oninput  = e => w_er_val.textContent = e.target.value;
    w_lt.oninput  = e => w_lt_val.textContent = e.target.value;
  }

  async function loadSettings() {
    const s = await fetch('/api/settings').then(r=>r.json());
    momentum.checked = s.strategies.momentum; mean_reversion.checked = s.strategies.mean_reversion; vwap.checked = s.strategies.vwap;
    news.checked = s.strategies.news; earnings.checked = s.strategies.earnings;
    longterm_trend.checked = s.strategies.longterm_trend; longterm_momentum.checked = s.strategies.longterm_momentum;
    crypto_enabled.checked = (s.crypto && s.crypto.enabled) ? true : false;

    enter.value = s.thresholds.enter; enter_val.textContent = enter.value;
    exit.value  = s.thresholds.exit;  exit_val.textContent  = exit.value;

    w_mom.value = s.weights.momentum; w_mom_val.textContent = w_mom.value;
    w_mr.value  = s.weights.mean_reversion; w_mr_val.textContent = w_mr.value;
    w_news.value= s.weights.news; w_news_val.textContent = w_news.value;
    w_er.value  = s.weights.earnings; w_er_val.textContent   = w_er.value;
    w_lt.value  = (s.weights.longterm ?? 0.15); w_lt_val.textContent = w_lt.value;

    cand_max.value = s.scheduling.candidate_max_symbols;
    cand_min.value = s.scheduling.candidate_refresh_min;
    news_sec.value = s.scheduling.news_interval_s;
    earn_min.value = s.scheduling.earnings_refresh_min;
    lt_min.value   = s.scheduling.longterm_refresh_min;
    batch_only.checked = !!(s.data && s.data.strict_batch_only);
  }

  async function saveSettings() {
    const body = {
      strategies:{
        momentum: momentum.checked, mean_reversion: mean_reversion.checked, vwap: vwap.checked,
        news: news.checked, earnings: earnings.checked, longterm_trend: longterm_trend.checked, longterm_momentum: longterm_momentum.checked
      },
      thresholds:{ enter: parseFloat(enter.value), exit: parseFloat(exit.value) },
      weights:{ momentum: parseFloat(w_mom.value), mean_reversion: parseFloat(w_mr.value), news: parseFloat(w_news.value), earnings: parseFloat(w_er.value), longterm: parseFloat(w_lt.value) },
      scheduling:{ candidate_max_symbols: parseInt(cand_max.value,10), candidate_refresh_min: parseInt(cand_min.value,10), news_interval_s: parseInt(news_sec.value,10), earnings_refresh_min: parseInt(earn_min.value,10), longterm_refresh_min: parseInt(lt_min.value,10) },
      data:{ strict_batch_only: batch_only.checked },
      crypto:{ enabled: crypto_enabled.checked }
    };
    await fetch('/api/settings',{ method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    saveMsg.textContent = 'Saved.'; setTimeout(()=> saveMsg.textContent='',1500);
  }

  function renderTable(tbodyEl, rowsHtml) {
    tbodyEl.innerHTML = rowsHtml || '<tr><td colspan="8" class="muted">No data</td></tr>';
  }

  async function refreshPositions() {
    const data = await fetch('/api/positions').then(r=>r.json()).catch(()=>({positions:[]}));
    const rows = (data.positions||[]).map(p => `
      <tr><td class="tight">${esc(p.symbol)}</td><td class="tight">${esc(p.qty)}</td><td>${esc(p.avg_entry)}</td><td class="tight">${p.updated ? new Date(p.updated*1000).toLocaleTimeString() : ''}</td></tr>
    `).join('');
    renderTable(tbody('pos_tbl'), rows);
  }

  async function refreshCandidates() {
    const data = await fetch('/api/candidates').then(r=>r.json()).catch(()=>({candidates:{}}));
    const rows = Object.entries(data.candidates||{}).map(([sym, rec]) => `
      <tr><td class="tight">${esc(sym)}</td><td class="tight">${(rec.mover ?? '').toFixed ? rec.mover.toFixed(3) : esc(rec.mover)}</td><td>${rec.mid ?? ''}</td><td>${rec.news ?? ''}</td><td class="tight">${(rec.score ?? '').toFixed ? rec.score.toFixed(3) : esc(rec.score)}</td></tr>
    `).join('');
    renderTable(tbody('cand_tbl'), rows);
  }

  async function refreshHealth() {
    const data = await fetch('/api/health').then(r=>r.json()).catch(()=>({health:[]}));
    const items = data.health || [];
    const last = items.find(h => String(h.name) === 'health_last_run');
    el('health_last_line').textContent = last && last.ts ? ('Last run: ' + new Date(last.ts*1000).toLocaleString()) : 'Last run: —';

    const rows = items.map(h => `
      <tr><td class="tight">${esc(h.name)}</td><td class="tight">${h.ok ? '<span class="ok">OK</span>' : '<span class="bad">FAIL</span>'}</td><td>${esc(h.detail)}</td><td class="tight">${h.ts ? new Date(h.ts*1000).toLocaleTimeString() : ''}</td></tr>
    `).join('');
    renderTable(tbody('health_tbl'), rows);
  }

  async function refreshEvents() {
    const data = await fetch('/api/status').then(r=>r.json()).catch(()=>({events:[]}));
    const rows = (data.events||[]).slice(-200).reverse().map(e => {
      const lvl = String(e.level||'').toUpperCase();
      const cls = lvl==='ERROR' ? 'bad' : (lvl==='WARN' || lvl==='WARNING') ? 'warn' : '';
      return `<tr><td class="tight">${e.ts ? new Date(e.ts*1000).toLocaleTimeString() : ''}</td><td class="tight ${cls}">${esc(lvl)}</td><td>${esc(e.msg)}</td><td class="tight">${esc(JSON.stringify(e.meta||{}))}</td></tr>`;
    }).join('');
    renderTable(tbody('evt_tbl'), rows);
  }

  async function load() {
    bindLiveLabels();
    await loadSettings();
    await Promise.all([refreshPositions(), refreshCandidates(), refreshHealth(), refreshEvents()]);
  }

  el('refreshBtn').addEventListener('click', async ()=>{
    await Promise.all([refreshPositions(), refreshCandidates(), refreshHealth(), refreshEvents()]);
    countdown = REFRESH_SEC + 1;
  });

  el('runHealthBtn').addEventListener('click', async ()=>{
    try { el('runHealthBtn').disabled = true; } catch(_){}
    try {
      el('health_run_msg').textContent = 'Running...';
      await fetch('/api/health/run', {method:'POST'});
      setTimeout(async ()=>{
        await refreshHealth();
        el('health_run_msg').textContent = 'Done.';
        setTimeout(()=> el('health_run_msg').textContent = '', 1500);
        try { el('runHealthBtn').disabled = false; } catch(_){}
      }, 1500);
    } catch(e) {
      el('health_run_msg').textContent = 'Failed.';
      try { el('runHealthBtn').disabled = false; } catch(_){}
    }
  });

  const REFRESH_SEC = 20;
  el('saveBtn').addEventListener('click', saveSettings);

  let countdown = REFRESH_SEC;
  setInterval(async ()=>{
    countdown -= 1;
    if (countdown <= 0) {
      await Promise.all([refreshPositions(), refreshCandidates(), refreshHealth(), refreshEvents()]);
      countdown = REFRESH_SEC;
    }
    document.getElementById('countdown').textContent = countdown;
  }, 1000);

  load();
</script>

</body>
</html>
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(HTML)

@app.get("/api/settings")
def api_get_settings() -> Dict[str, Any]:
    return get_settings()

@app.post("/api/settings")
async def api_update_settings(req: Request) -> JSONResponse:
    body = await req.json()
    updated = update_settings(body or {})
    return JSONResponse(updated)

@app.get("/api/positions")
def api_positions() -> Dict[str, Any]:
    return {"positions": get_positions()}

@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    return {"health": get_health()}

@app.post("/api/health/run")
def api_health_run() -> Dict[str, Any]:
    set_kv("health_run_request", {"ts": time.time()})
    return {"queued": True}

@app.get("/api/candidates")
def api_candidates() -> Dict[str, Any]:
    return {"candidates": get_kv("candidates", {}) or {}}

@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    return {"events": list_events(200)}

@app.get("/logs", response_class=PlainTextResponse)
def logs_tail() -> str:
    return "Use your filesystem/log stack to view full logs."
