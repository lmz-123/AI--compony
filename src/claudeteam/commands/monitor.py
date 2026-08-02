"""Read-only monitor snapshot and tiny HTTP dashboard.

`claudeteam monitor --json` returns a compact machine-readable status snapshot.
`claudeteam monitor serve` exposes the same data at `/api/monitor` plus a
minimal HTML page at `/`. It never writes state and never controls agents.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from claudeteam.agents import get_adapter
from claudeteam.feishu import catchup
from claudeteam.runtime import config, paths, tmux, watchdog
from claudeteam.store import local_facts, tasks
from claudeteam.util import ago_ms, maybe_print_help, now_ms, print_json


USAGE = "usage: claudeteam monitor [--json] | monitor serve [--host HOST] [--port PORT]"


def _age_sec(ts_ms: int | None) -> int | None:
    if not ts_ms:
        return None
    return max(0, int((now_ms() - int(ts_ms)) / 1000))


def _daemon_status() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for spec in watchdog.all_known_specs():
        pid = spec.pid_file.read_text().strip() if spec.pid_file.exists() else ""
        alive = watchdog.is_alive(spec) if pid else False
        out[spec.name] = {
            "alive": alive,
            "pid": pid,
            "state": "alive" if alive else ("stale-pid" if pid else "missing"),
        }
    return out


def _agent_pane_state(session: str, agent: str, cfg: dict, *, session_alive: bool) -> dict[str, Any]:
    if not session_alive:
        return {"pane_exists": False, "pane_ready": False, "pane_state": "session_down"}
    target = tmux.Target(session, agent)
    if not tmux.has_window(target):
        return {"pane_exists": False, "pane_ready": False, "pane_state": "missing"}
    cli = cfg.get("cli", "claude-code")
    try:
        adapter = get_adapter(cli)
        text = tmux.capture_pane(target, lines=80)
        ready = any(marker in text for marker in adapter.ready_markers())
        return {
            "pane_exists": True,
            "pane_ready": ready,
            "pane_state": "ready" if ready else ("lazy" if cfg.get("lazy") else "not_ready"),
        }
    except Exception as exc:  # noqa: BLE001 - monitor must be best-effort
        return {
            "pane_exists": True,
            "pane_ready": False,
            "pane_state": "probe_failed",
            "pane_error": str(exc),
        }


def _tasks_by_assignee(agent: str) -> dict[str, Any]:
    rows = tasks.list_tasks(assignee=agent)
    active = [t for t in rows if t.get("status") in {"进行中", "需审批"}]
    pending = [t for t in rows if t.get("status") == "待处理"]
    return {
        "active_task": active[0]["id"] if active else "",
        "active_title": active[0].get("title", "") if active else "",
        "active_status": active[0].get("status", "") if active else "",
        "pending_count": len(pending),
        "pending": [{"id": t["id"], "title": t.get("title", "")} for t in pending[:5]],
    }


def _router_state() -> dict[str, Any]:
    cur = catchup.read_cursor()
    if not cur:
        return {
            "cursor": None,
            "last_inbound_age_sec": None,
            "last_inbound": "none observed",
        }
    cts = int(cur.get("create_time_ms") or 0)
    return {
        "cursor": cur.get("message_id", ""),
        "create_time": cur.get("create_time", ""),
        "last_inbound_age_sec": _age_sec(cts),
        "last_inbound": ago_ms(cts) if cts else "",
    }


def snapshot() -> dict[str, Any]:
    team = config.load_team()
    agents_cfg = team.get("agents", {}) or {}
    roster = sorted(agents_cfg)
    session = team.get("session") or config.default_session_name()
    session_alive = tmux.has_session(session)
    heartbeats = local_facts.all_heartbeats()

    agents: list[dict[str, Any]] = []
    for agent in roster:
        cfg = agents_cfg.get(agent, {})
        status = local_facts.get_status(agent) or {}
        hb = int(heartbeats.get(agent) or 0)
        agent_tasks = _tasks_by_assignee(agent)
        unread_count = len(local_facts.list_messages(agent, unread_only=True))
        pane = _agent_pane_state(session, agent, cfg, session_alive=session_alive)
        agents.append({
            "agent": agent,
            "role": cfg.get("role", ""),
            "cli": cfg.get("cli", "claude-code"),
            "model": cfg.get("model", team.get("default_model", "")),
            "status": status.get("status", "unknown"),
            "task": status.get("task", ""),
            "blocker": status.get("blocker", ""),
            "updated_at_ms": status.get("updated_at", 0),
            "updated_age_sec": _age_sec(status.get("updated_at", 0)),
            "heartbeat_ms": hb,
            "heartbeat_age_sec": _age_sec(hb),
            "unread_count": unread_count,
            **agent_tasks,
            **pane,
        })

    all_tasks = tasks.list_tasks()
    queue = {
        "pending": sum(1 for t in all_tasks if t.get("status") == "待处理"),
        "in_progress": sum(1 for t in all_tasks if t.get("status") == "进行中"),
        "needs_approval": sum(1 for t in all_tasks if t.get("status") == "需审批"),
        "completed": sum(1 for t in all_tasks if t.get("status") == "已完成"),
        "cancelled": sum(1 for t in all_tasks if t.get("status") == "已取消"),
    }
    daemons = _daemon_status()
    ok = session_alive and all(d["alive"] for d in daemons.values())
    return {
        "ok": ok,
        "generated_at_ms": now_ms(),
        "state_dir": str(paths.state_dir()),
        "session": {"name": session, "alive": session_alive},
        "config": {"chat_id_set": bool(config.chat_id()), "agent_count": len(roster)},
        "daemons": daemons,
        "router": _router_state(),
        "queue": queue,
        "agents": agents,
    }


def _html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Company Monitor</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#0f172a; color:#e5e7eb; }
    header { padding: 20px 24px; border-bottom: 1px solid #243044; display:flex; justify-content:space-between; gap:16px; align-items:center; }
    h1 { margin: 0; font-size: 20px; }
    main { padding: 20px 24px; display:grid; gap:18px; }
    .cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:12px; }
    .card, table { background:#111827; border:1px solid #263244; border-radius:14px; }
    .card { padding:14px; }
    .label { color:#94a3b8; font-size:12px; }
    .value { font-size:24px; margin-top:6px; }
    .ok { color:#86efac; } .warn { color:#fde68a; } .bad { color:#fca5a5; }
    table { width:100%; border-collapse:collapse; overflow:hidden; }
    th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #263244; font-size:14px; vertical-align:top; }
    th { color:#94a3b8; font-weight:600; background:#0b1220; }
    tr:last-child td { border-bottom:0; }
    code { color:#bae6fd; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#1f2937; }
    footer { color:#64748b; font-size:12px; padding:0 24px 20px; }
  </style>
</head>
<body>
  <header>
    <h1>AI Company Monitor</h1>
    <div id="stamp" class="label">loading…</div>
  </header>
  <main>
    <section class="cards" id="cards"></section>
    <section>
      <table>
        <thead><tr><th>Agent</th><th>Status</th><th>Task</th><th>Heartbeat</th><th>Pane</th><th>Queue</th><th>Unread</th></tr></thead>
        <tbody id="agents"></tbody>
      </table>
    </section>
  </main>
  <footer>Read-only dashboard. Data refreshes every 5 seconds from <code>/api/monitor</code>.</footer>
<script>
function age(sec){ if(sec === null || sec === undefined) return "never"; if(sec < 60) return sec+"s"; if(sec < 3600) return Math.floor(sec/60)+"m"; return Math.floor(sec/3600)+"h"; }
function cls(ok){ return ok ? "ok" : "bad"; }
async function load(){
  const r = await fetch('/api/monitor', {cache:'no-store'});
  const d = await r.json();
  document.getElementById('stamp').textContent = new Date(d.generated_at_ms).toLocaleString();
  const daemonOk = Object.values(d.daemons).every(x=>x.alive);
  const cards = [
    ['Overall', d.ok ? 'OK' : 'Check', d.ok],
    ['Session', d.session.alive ? d.session.name : 'down', d.session.alive],
    ['Router', d.daemons.router?.state || 'unknown', d.daemons.router?.alive],
    ['Watchdog', d.daemons.watchdog?.state || 'unknown', d.daemons.watchdog?.alive],
    ['Pending', d.queue.pending, d.queue.pending === 0],
    ['Approval', d.queue.needs_approval, d.queue.needs_approval === 0],
    ['Inbound', d.router.last_inbound || 'none', true],
  ];
  document.getElementById('cards').innerHTML = cards.map(c=>`<div class="card"><div class="label">${c[0]}</div><div class="value ${cls(c[2])}">${c[1]}</div></div>`).join('');
  document.getElementById('agents').innerHTML = d.agents.map(a=>{
    const task = a.active_task ? `${a.active_task} · ${a.active_title}` : (a.task || '—');
    const paneClass = a.pane_ready ? 'ok' : (a.pane_exists ? 'warn' : 'bad');
    return `<tr>
      <td><strong>${a.agent}</strong><br><span class="label">${a.model}</span></td>
      <td><span class="pill">${a.status}</span>${a.blocker ? '<br><span class="bad">'+a.blocker+'</span>' : ''}</td>
      <td>${task}</td>
      <td>${age(a.heartbeat_age_sec)}</td>
      <td class="${paneClass}">${a.pane_state}</td>
      <td>${a.pending_count}</td>
      <td>${a.unread_count}</td>
    </tr>`;
  }).join('');
}
load().catch(e => { document.body.insertAdjacentHTML('beforeend', '<pre>'+e+'</pre>'); });
setInterval(load, 5000);
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

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        if self.path in {"/", "/monitor"}:
            self._send(200, _html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/monitor":
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")


def _serve(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"📊 monitor listening on http://{host}:{port}")
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
        parser = argparse.ArgumentParser(prog="claudeteam monitor serve", add_help=True)
        parser.add_argument("--host", default=os.environ.get("CLAUDETEAM_MONITOR_HOST", "127.0.0.1"))
        parser.add_argument("--port", type=int, default=int(os.environ.get("CLAUDETEAM_MONITOR_PORT", "8765")))
        ns = parser.parse_args(rest[1:])
        return _serve(ns.host, ns.port)
    if rest == ["--json"] or not rest:
        print_json(snapshot())
        return 0
    print(USAGE)
    return 2
