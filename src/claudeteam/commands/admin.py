"""Writable admin dashboard for a running ClaudeTeam deployment.

`monitor serve` stays read-only. This command exposes a separate HTTP surface
for operators who need live pane details plus roster management.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from claudeteam.agents import known_clis
from claudeteam.commands import fire, hire, monitor, restart
from claudeteam.runtime import config, tmux
from claudeteam.runtime import paths
from claudeteam.store import learning, local_facts, radio, tasks
from claudeteam.util import error_exit, maybe_print_help, print_json


USAGE = "usage: claudeteam admin [--json] | admin serve [--host HOST] [--port PORT]"
_AGENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,40}$")


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _validate_agent_name(name: str) -> str:
    name = str(name or "").strip()
    if not _AGENT_RE.match(name):
        raise ValueError("agent name must start with a letter and use letters, numbers, _ or -")
    return name


def _agent_config_from_payload(payload: dict[str, Any], *, current: dict | None = None) -> dict:
    cfg = dict(current or {})
    for key in ("cli", "model", "role", "playbook", "card_color", "reasoning_effort", "tone", "notes"):
        if key in payload:
            val = payload.get(key)
            if val is None:
                cfg.pop(key, None)
            else:
                cfg[key] = str(val).strip()
    if "lazy" in payload:
        cfg["lazy"] = bool(payload.get("lazy"))
    if "specialty" in payload:
        val = payload.get("specialty")
        if isinstance(val, str):
            cfg["specialty"] = [x.strip() for x in val.split(",") if x.strip()]
        elif isinstance(val, list):
            cfg["specialty"] = [str(x).strip() for x in val if str(x).strip()]
        else:
            cfg["specialty"] = []
    cfg.setdefault("cli", "codex-cli")
    if cfg["cli"] not in known_clis():
        raise ValueError(f"unknown cli: {cfg['cli']}")
    return {k: v for k, v in cfg.items() if v not in ("", [], None)}


def _roster() -> dict[str, Any]:
    team = config.load_team()
    return {
        "session": team.get("session") or config.default_session_name(),
        "default_model": team.get("default_model", ""),
        "agents": team.get("agents", {}) or {},
        "known_clis": list(known_clis()),
    }


def _agent_detail(agent: str, *, lines: int = 160) -> dict[str, Any]:
    agent = _validate_agent_name(agent)
    roster = _roster()
    cfg = dict(roster["agents"].get(agent) or {})
    status = local_facts.get_status(agent) or {}
    session = roster["session"]
    target = tmux.Target(session, agent)
    pane_exists = tmux.has_window(target) if tmux.has_session(session) else False
    pane_text = tmux.capture_pane(target, lines=max(20, min(lines, 500))) if pane_exists else ""
    return {
        "agent": agent,
        "config": cfg,
        "status": status,
        "heartbeat_ms": local_facts.get_heartbeat(agent),
        "pane": {
            "exists": pane_exists,
            "text": pane_text,
        },
        "tasks": tasks.list_tasks(assignee=agent),
        "inbox": local_facts.list_messages(agent)[-30:],
        "radio": radio.agent_threads(agent),
        "radio_updates": radio.list_updates(agent, unacked_only=False, limit=30),
        "logs": local_facts.list_logs(agent, limit=80),
    }


def _state() -> dict[str, Any]:
    data = monitor.snapshot()
    data["roster"] = _roster()
    data["learning"] = {
        "drafts": len(learning.list_drafts(status="draft", limit=10_000)),
        "recent": learning.list_drafts(limit=10),
    }
    doctor_path = paths.state_dir() / "doctor-last.json"
    try:
        data["doctor"] = json.loads(doctor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data["doctor"] = None
    return data


def _create_agent(payload: dict[str, Any]) -> dict[str, Any]:
    name = _validate_agent_name(str(payload.get("name") or ""))
    if name in config.agent_names():
        raise ValueError(f"agent already exists: {name}")
    cfg = _agent_config_from_payload(payload)
    ok, msg = config.add_agent(name, cfg)
    if not ok:
        raise RuntimeError(msg)
    lifecycle = "added"
    session = config.session_name()
    if tmux.has_session(session):
        rc = hire.main([name])
        lifecycle = "hired" if rc == 0 else f"hire_failed:{rc}"
    return {"ok": True, "agent": name, "message": msg, "lifecycle": lifecycle}


def _update_agent(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    agent = _validate_agent_name(agent)
    try:
        current = config.agent_config(agent)
    except KeyError:
        raise ValueError(f"unknown agent: {agent}") from None
    cfg = _agent_config_from_payload(payload, current=current)
    ok, msg = config.add_agent(agent, cfg)
    if not ok:
        raise RuntimeError(msg)
    lifecycle = "updated"
    if payload.get("restart"):
        rc = restart.main([agent])
        lifecycle = "restarted" if rc == 0 else f"restart_failed:{rc}"
    return {"ok": True, "agent": agent, "message": msg, "lifecycle": lifecycle}


def _delete_agent(agent: str) -> dict[str, Any]:
    agent = _validate_agent_name(agent)
    if agent == "manager":
        raise ValueError("manager cannot be deleted from the admin UI")
    rc = fire.main([agent])
    return {"ok": rc == 0, "agent": agent, "lifecycle": "fired" if rc == 0 else f"fire_failed:{rc}"}


def _agent_action(agent: str, action: str) -> dict[str, Any]:
    agent = _validate_agent_name(agent)
    if action == "hire":
        rc = hire.main([agent])
    elif action == "restart":
        rc = restart.main([agent])
    elif action == "fire":
        if agent == "manager":
            raise ValueError("manager cannot be fired from the admin UI")
        rc = fire.main([agent])
    else:
        raise ValueError(f"unknown action: {action}")
    return {"ok": rc == 0, "agent": agent, "action": action, "rc": rc}


def _html() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Company Admin</title>
  <style>
    :root { color-scheme: dark; --bg:#101114; --panel:#181a1f; --line:#2a2e37; --text:#e8e9ec; --muted:#9aa0aa; --good:#5fd08f; --warn:#e7bd55; --bad:#ef7777; --accent:#7fb4ff; }
    * { box-sizing:border-box; }
    body { margin:0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
    header { height:56px; display:flex; align-items:center; justify-content:space-between; padding:0 18px; border-bottom:1px solid var(--line); background:#14161a; position:sticky; top:0; z-index:2; }
    h1 { margin:0; font-size:18px; font-weight:650; }
    button, input, select, textarea { font:inherit; }
    button { border:1px solid var(--line); background:#20242b; color:var(--text); border-radius:6px; padding:7px 10px; cursor:pointer; }
    button.primary { background:#1e4f8d; border-color:#2e68ad; }
    button.danger { background:#552226; border-color:#7c3439; }
    input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; background:#111318; color:var(--text); padding:8px; }
    textarea { min-height:78px; resize:vertical; }
    main { display:grid; grid-template-columns:280px 1fr 360px; min-height:calc(100vh - 56px); }
    aside, section { border-right:1px solid var(--line); padding:14px; overflow:auto; }
    section:last-child { border-right:0; }
    .toolbar { display:flex; gap:8px; align-items:center; }
    .cards { display:grid; grid-template-columns:repeat(3,minmax(120px,1fr)); gap:10px; margin-bottom:12px; }
    .metric { border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel); }
    .metric .label, .muted { color:var(--muted); font-size:12px; }
    .metric .value { font-size:22px; margin-top:4px; }
    .agent { display:block; width:100%; text-align:left; margin-bottom:8px; }
    .agent.active { border-color:var(--accent); background:#1b2738; }
    .pill { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:1px 7px; font-size:12px; color:var(--muted); }
    .good { color:var(--good); } .warn { color:var(--warn); } .bad { color:var(--bad); }
    pre { margin:0; white-space:pre-wrap; word-break:break-word; background:#0c0e12; border:1px solid var(--line); border-radius:8px; padding:12px; min-height:360px; max-height:58vh; overflow:auto; }
    table { width:100%; border-collapse:collapse; margin-top:10px; }
    td, th { border-bottom:1px solid var(--line); padding:7px; text-align:left; vertical-align:top; }
    th { color:var(--muted); font-weight:600; }
    .grid { display:grid; gap:10px; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .tabs { display:flex; gap:8px; margin:10px 0; }
    .tabs button.active { border-color:var(--accent); }
    .hidden { display:none; }
    @media (max-width: 1080px) { main { grid-template-columns:1fr; } aside, section { border-right:0; border-bottom:1px solid var(--line); } }
  </style>
</head>
<body>
  <header>
    <h1>AI Company Admin</h1>
    <div class="toolbar"><span id="stamp" class="muted">loading</span><button onclick="loadAll()">Refresh</button></div>
  </header>
  <main>
    <aside>
      <div class="toolbar" style="justify-content:space-between;margin-bottom:10px"><strong>Agents</strong><button class="primary" onclick="newAgent()">New</button></div>
      <div id="agents"></div>
    </aside>
    <section>
      <div class="cards" id="cards"></div>
      <div class="toolbar" style="justify-content:space-between">
        <div><strong id="detailTitle">Select an agent</strong><div id="detailMeta" class="muted"></div></div>
        <div class="toolbar"><button onclick="restartAgent()">Restart</button><button class="danger" onclick="deleteAgent()">Delete</button></div>
      </div>
      <div class="tabs"><button id="tabPane" class="active" onclick="showTab('pane')">Pane</button><button id="tabTasks" onclick="showTab('tasks')">Tasks</button><button id="tabInbox" onclick="showTab('inbox')">Inbox</button><button id="tabRadio" onclick="showTab('radio')">Radio</button><button id="tabLogs" onclick="showTab('logs')">Logs</button></div>
      <pre id="pane"></pre>
      <div id="tasks" class="hidden"></div>
      <div id="inbox" class="hidden"></div>
      <div id="radio" class="hidden"></div>
      <div id="logs" class="hidden"></div>
    </section>
    <section>
      <strong>Agent Editor</strong>
      <div class="grid" style="margin-top:10px">
        <label>Name<input id="fName" /></label>
        <div class="row"><label>CLI<select id="fCli"></select></label><label>Model<input id="fModel" placeholder="gpt-5.4-mini" /></label></div>
        <div class="row"><label>Reasoning<input id="fReasoning" placeholder="low / medium / high" /></label><label>Playbook<input id="fPlaybook" placeholder="ops.md" /></label></div>
        <label>Role<textarea id="fRole"></textarea></label>
        <label>Specialty<textarea id="fSpecialty" placeholder="日志分析, Docker Compose"></textarea></label>
        <label>Notes<textarea id="fNotes"></textarea></label>
        <label><input id="fLazy" type="checkbox" style="width:auto" /> Lazy start</label>
        <label><input id="fRestart" type="checkbox" style="width:auto" /> Restart after save</label>
        <div class="toolbar"><button class="primary" onclick="saveAgent()">Save</button><button onclick="loadSelectedIntoForm()">Reset</button></div>
        <div id="formMsg" class="muted"></div>
      </div>
    </section>
  </main>
<script>
let state = null, selected = "", detail = null, tab = "pane";
const $ = id => document.getElementById(id);
function age(sec){ if(sec == null) return "never"; if(sec < 60) return sec+"s"; if(sec < 3600) return Math.floor(sec/60)+"m"; return Math.floor(sec/3600)+"h"; }
async function api(path, opts){ const r = await fetch(path, opts || {cache:"no-store"}); const t = await r.text(); let data; try{ data = JSON.parse(t); }catch{ data = {ok:false,error:t}; } if(!r.ok) throw new Error(data.error || r.statusText); return data; }
async function loadAll(){
  state = await api("/api/admin/state");
  $("stamp").textContent = new Date(state.generated_at_ms).toLocaleString();
  renderCards(); renderAgents(); fillCliOptions();
  if(!selected && state.agents[0]) selected = state.agents[0].agent;
  if(selected) await loadDetail(selected);
}
function renderCards(){
  const q = state.queue;
    const doctor = state.doctor ? `${state.doctor.counts.fail}/${state.doctor.counts.warn}` : "none";
    const learning = state.learning ? state.learning.drafts : 0;
    const rows = [["Overall", state.ok ? "OK" : "Check", state.ok?"good":"bad"],["Doctor", doctor, state.doctor && state.doctor.counts.fail ? "bad" : ""],["Learning", learning, learning ? "warn" : ""],["Pending", q.pending, ""],["Running", q.in_progress, ""],["Background", q.background || 0, ""],["Unread", state.agents.reduce((n,a)=>n+a.unread_count,0), ""]];
  $("cards").innerHTML = rows.map(x=>`<div class="metric"><div class="label">${x[0]}</div><div class="value ${x[2]}">${x[1]}</div></div>`).join("");
}
function renderAgents(){
  $("agents").innerHTML = state.agents.map(a=>`<button class="agent ${a.agent===selected?'active':''}" onclick="selectAgent('${a.agent}')"><strong>${a.agent}</strong> <span class="pill">${a.status}</span><br><span class="muted">${a.model || ""} · ${a.pane_state} · hb ${age(a.heartbeat_age_sec)}</span><br><span class="muted">${a.active_task || a.task || "ready"}</span></button>`).join("");
}
async function selectAgent(name){ selected = name; renderAgents(); await loadDetail(name); }
async function loadDetail(name){
  detail = await api(`/api/admin/agents/${encodeURIComponent(name)}?lines=220`);
  $("detailTitle").textContent = name;
  $("detailMeta").textContent = `${detail.config.cli || ""} · ${detail.config.model || ""} · ${detail.status.status || "unknown"} · heartbeat ${detail.heartbeat_ms ? "seen" : "never"}`;
  $("pane").textContent = detail.pane.text || "(no pane output)";
  $("tasks").innerHTML = table(detail.tasks, ["id","status","title","created_at"]);
  $("inbox").innerHTML = table(detail.inbox, ["local_id","from","priority","task_id","read","content"]);
  $("radio").innerHTML = table(detail.radio_updates, ["task_id","from","local_id","acked","summary"]);
  $("logs").innerHTML = table(detail.logs, ["type","ref","content"]);
  loadSelectedIntoForm();
}
function table(rows, keys){ if(!rows || !rows.length) return '<div class="muted">No rows</div>'; return `<table><thead><tr>${keys.map(k=>`<th>${k}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${keys.map(k=>`<td>${String(r[k] ?? "").slice(0,500)}</td>`).join("")}</tr>`).join("")}</tbody></table>`; }
function showTab(name){ tab = name; ["pane","tasks","inbox","radio","logs"].forEach(x=>{$(x).classList.toggle("hidden", x!==name); $("tab"+x[0].toUpperCase()+x.slice(1)).classList.toggle("active", x===name);}); }
function fillCliOptions(){ const opts = state.roster.known_clis.map(c=>`<option value="${c}">${c}</option>`).join(""); if($("fCli").innerHTML !== opts) $("fCli").innerHTML = opts; }
function loadSelectedIntoForm(){
  const cfg = detail ? detail.config : {};
  $("fName").value = selected || "";
  $("fName").disabled = !!selected;
  $("fCli").value = cfg.cli || "codex-cli";
  $("fModel").value = cfg.model || "";
  $("fReasoning").value = cfg.reasoning_effort || "";
  $("fPlaybook").value = cfg.playbook || "";
  $("fRole").value = cfg.role || "";
  $("fSpecialty").value = (cfg.specialty || []).join(", ");
  $("fNotes").value = cfg.notes || "";
  $("fLazy").checked = !!cfg.lazy;
  $("fRestart").checked = false;
}
function payload(){
  return {name:$("fName").value, cli:$("fCli").value, model:$("fModel").value, reasoning_effort:$("fReasoning").value, playbook:$("fPlaybook").value, role:$("fRole").value, specialty:$("fSpecialty").value, notes:$("fNotes").value, lazy:$("fLazy").checked, restart:$("fRestart").checked};
}
function newAgent(){ selected = ""; detail = {config:{}}; renderAgents(); $("detailTitle").textContent = "New agent"; $("detailMeta").textContent = ""; loadSelectedIntoForm(); }
async function saveAgent(){
  try{
    const body = JSON.stringify(payload());
    const path = selected ? `/api/admin/agents/${encodeURIComponent(selected)}` : "/api/admin/agents";
    const method = selected ? "PUT" : "POST";
    const res = await api(path, {method, headers:{"Content-Type":"application/json"}, body});
    $("formMsg").textContent = res.lifecycle || "saved";
    selected = res.agent; await loadAll();
  }catch(e){ $("formMsg").textContent = e.message; }
}
async function restartAgent(){ if(!selected) return; await api(`/api/admin/agents/${encodeURIComponent(selected)}/action`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"restart"})}); await loadAll(); }
async function deleteAgent(){ if(!selected || !confirm(`Delete ${selected}?`)) return; await api(`/api/admin/agents/${encodeURIComponent(selected)}`, {method:"DELETE"}); selected = ""; await loadAll(); }
loadAll().catch(e => {$("stamp").textContent = e.message;});
setInterval(()=>{ if(selected) loadDetail(selected).catch(()=>{}); loadAll().catch(()=>{}); }, 5000);
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, exc: Exception) -> None:
        self._json(status, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/admin"}:
                self._send(200, _html().encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/admin/state":
                self._json(200, _state())
                return
            m = re.match(r"^/api/admin/agents/([^/]+)$", parsed.path)
            if m:
                qs = parse_qs(parsed.query)
                lines = int((qs.get("lines") or ["160"])[0])
                self._json(200, _agent_detail(m.group(1), lines=lines))
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            self._error(400, e)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            payload = _json_body(self)
            if parsed.path == "/api/admin/agents":
                self._json(200, _create_agent(payload))
                return
            m = re.match(r"^/api/admin/agents/([^/]+)/action$", parsed.path)
            if m:
                self._json(200, _agent_action(m.group(1), str(payload.get("action") or "")))
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            self._error(400, e)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            payload = _json_body(self)
            m = re.match(r"^/api/admin/agents/([^/]+)$", parsed.path)
            if m:
                self._json(200, _update_agent(m.group(1), payload))
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            self._error(400, e)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            m = re.match(r"^/api/admin/agents/([^/]+)$", parsed.path)
            if m:
                self._json(200, _delete_agent(m.group(1)))
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            self._error(400, e)


def _serve(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"admin listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if rest and rest[0] == "serve":
        parser = argparse.ArgumentParser(prog="claudeteam admin serve", add_help=True)
        parser.add_argument("--host", default=os.environ.get("CLAUDETEAM_ADMIN_HOST", "127.0.0.1"))
        parser.add_argument("--port", type=int, default=int(os.environ.get("CLAUDETEAM_ADMIN_PORT", "8766")))
        ns = parser.parse_args(rest[1:])
        return _serve(ns.host, ns.port)
    if rest == ["--json"] or not rest:
        print_json(_state())
        return 0
    return error_exit(USAGE)
