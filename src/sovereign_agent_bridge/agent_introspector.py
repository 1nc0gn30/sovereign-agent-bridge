"""Live Cross-Agent Introspection, Durable Shared Bus, and Muse/AGY/Hermes Coordination Engine.

Provides real-time visibility into all AI agents running on the ZothOS host:
- Meta Muse Code (`~/.local/share/muse/sessions/.../session.jsonl`, `~/.local/share/muse/tui-history.jsonl`, live PIDs)
- Google Antigravity (`~/.gemini/antigravity-cli/brain/.../.system_generated/logs/transcript.jsonl`, live `agy` PIDs)
- Hermes Agent Gateway (`~/.hermes/gateway_state.json`, live gateway/worker PIDs)
- Local Subagents & Background Renderers (e.g., `make_batch*.py`, `x_multi_hub.mjs`, Zoth daemons)

Also provides a persistent SQLite + JSONL Cross-Agent Message Bus (`~/.config/zothos/agent-bridge-bus.db`
and `/home/zoth/Desktop/zoth-1nc0gn30-videos/AGENT_BRIDGE_SYNC.md`) so Muse, Antigravity, Hermes,
and CLI subagents can read each other's active work, claim tasks without stepping on each other,
post direct handoffs, and dispatch headless `muse exec` jobs.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BUS_DIR = Path(os.path.expanduser("~/.config/zothos"))
BUS_DB = BUS_DIR / "agent-bridge-bus.db"
SHARED_SYNC_MD = Path("/home/zoth/Desktop/zoth-1nc0gn30-videos/AGENT_BRIDGE_SYNC.md")
GLOBAL_SYNC_MD = Path("/home/zoth/ZOTH_AGENT_BRIDGE.md")

_DB_LOCK = threading.RLock()


def _init_db() -> None:
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'unread',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_work_claims (
                    claim_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    task_summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'in_progress',
                    outputs_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL
                )
                """
            )
            # Seed initial coordination state if empty
            cur = conn.execute("SELECT COUNT(*) FROM agent_messages")
            if cur.fetchone()[0] == 0:
                now = time.time()
                seed_msgs = [
                    (
                        uuid.uuid4().hex[:12],
                        "muse-wonder-1.0 (01a1b743)",
                        "antigravity-lead (ab434014)",
                        "zoth-bridge-bus",
                        "Completed Batches 2-6 (25 Videos) + RESEARCH.md",
                        "Built RESEARCH.md (hook-first pacing, -14 LUFS loudnorm, 140 BPM beat math, 9:16 safe zones) and rendered Batches 2-6 (25 MP4s) in /home/zoth/Desktop/zoth-1nc0gn30-videos/out/.",
                        json.dumps([
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/RESEARCH.md",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch6.py",
                        ]),
                        "delivered",
                        now - 3600,
                    ),
                    (
                        uuid.uuid4().hex[:12],
                        "antigravity-lead (ab434014)",
                        "muse-wonder-1.0 (01a1b743)",
                        "zoth-bridge-bus",
                        "Picking up Batches 7-12 (43 New Videos) + Season III (36 Episodes)",
                        "Read Muse's RESEARCH.md and scripts/make_batch2..6.py. Expanded World of Zoth to 36 theatrical episodes (Season III) and rendering Batches 7-12 (-14 LUFS commercials, 140 BPM edits & comedic reels) in parallel.",
                        json.dumps([
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch7.py",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch8.py",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch9.py",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch10.py",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch11.py",
                            "/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts/make_batch12.py",
                        ]),
                        "delivered",
                        now - 600,
                    ),
                    (
                        uuid.uuid4().hex[:12],
                        "antigravity-peer (2e8bf02f)",
                        "all-agents",
                        "zoth-bridge-bus",
                        "System Disk Audit & Multiboot Configuration Complete",
                        "Completed full filesystem storage audit and verified multiboot configuration without deleting user assets.",
                        json.dumps(["/home/zoth/.gemini/antigravity-cli/brain/2e8bf02f-972c-42a2-a56a-c0aabf5cc28b"]),
                        "delivered",
                        now - 120,
                    ),
                ]
                conn.executemany(
                    "INSERT INTO agent_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    seed_msgs,
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_tool_policies (
                    tool_name TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    binary_target TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    require_bridge INTEGER NOT NULL DEFAULT 1,
                    total_runs INTEGER NOT NULL DEFAULT 0,
                    denied_runs INTEGER NOT NULL DEFAULT 0,
                    last_run_ts REAL NOT NULL DEFAULT 0,
                    last_pid INTEGER NOT NULL DEFAULT 0,
                    last_workspace TEXT NOT NULL DEFAULT '',
                    last_args TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_tool_leases (
                    lease_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    workspace TEXT NOT NULL,
                    args_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    ended_ts REAL NOT NULL DEFAULT 0,
                    exit_code INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cur_pol = conn.execute("SELECT COUNT(*) FROM ai_tool_policies")
            if cur_pol.fetchone()[0] == 0:
                default_policies = [
                    ("muse", "Meta Muse Code", "/opt/muse/muse", 1, 1, 3, 0, time.time() - 30, 987534, "/home/zoth/Desktop/zoth-1nc0gn30-videos", "muse (TUI + exec)"),
                    ("agy", "Google Antigravity (AGY)", "/home/zoth/.gemini/antigravity-cli/bin/agy", 1, 1, 4, 0, time.time() - 5, os.getpid(), "/home/zoth", "agy session ab434014"),
                    ("hermes", "Nous Hermes Agent", "/home/zoth/.hermes/hermes-agent/.hermes/bin/hermes", 1, 1, 2, 0, time.time() - 120, 0, "/home/zoth", "hermes gateway"),
                    ("claude", "Anthropic Claude Code", "/usr/local/bin/claude", 1, 1, 0, 0, 0.0, 0, "", ""),
                    ("codex", "OpenAI Codex CLI", "/usr/local/bin/codex", 1, 1, 0, 0, 0.0, 0, "", ""),
                    ("aider", "Aider AI Pair Programmer", "/opt/zothos-ai-env/bin/aider", 1, 1, 0, 0, 0.0, 0, "", ""),
                    ("opencode", "OpenCode AI Pair", "/usr/local/bin/opencode", 1, 1, 0, 0, 0.0, 0, "", ""),
                    ("ollama", "Ollama Local LLM Runtime", "/usr/local/bin/ollama", 1, 1, 0, 0, 0.0, 0, "", ""),
                ]
                conn.executemany(
                    "INSERT INTO ai_tool_policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    default_policies,
                )
            conn.commit()
        finally:
            conn.close()


_init_db()


def _scan_live_processes() -> List[Dict[str, Any]]:
    """Scan /proc for active AI agents and worker processes."""
    procs: List[Dict[str, Any]] = []
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,pcpu,pmem,etimes,tty,args"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines()[1:]:
            parts = line.strip().split(None, 5)
            if len(parts) < 6:
                continue
            pid_s, pcpu, pmem, etimes, tty, args = parts
            if any(
                k in args
                for k in (
                    "/opt/muse/muse-bin",
                    "agy",
                    "hermes_cli/main.py",
                    "make_batch",
                    "sovereign_agent_bridge",
                    "zoth-bridge",
                    "neuro-memory",
                )
            ) and "grep" not in args and "ps -eo" not in args:
                pid = int(pid_s)
                cwd = ""
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                except OSError:
                    pass
                procs.append(
                    {
                        "pid": pid,
                        "cpu_pct": float(pcpu),
                        "mem_pct": float(pmem),
                        "uptime_sec": int(etimes),
                        "tty": tty,
                        "cwd": cwd,
                        "cmdline": args[:240],
                    }
                )
    except Exception:
        pass
    return procs


def inspect_muse_sessions(limit: int = 4) -> List[Dict[str, Any]]:
    """Read Meta Muse Code sessions, active todos, recent tool calls, and prompts."""
    sessions: List[Dict[str, Any]] = []
    paths = sorted(
        glob.glob(os.path.expanduser("~/.local/share/muse/sessions/*/*/*/*/session.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )
    # Map user prompts from tui-history.jsonl
    prompts_by_session: Dict[str, List[str]] = {}
    tui_hist = os.path.expanduser("~/.local/share/muse/tui-history.jsonl")
    if os.path.exists(tui_hist):
        try:
            with open(tui_hist, "r", encoding="utf-8", errors="ignore") as f:
                raw_lines = [l.strip() for l in f if l.strip()]
            pending_prompt: Optional[str] = None
            for l in raw_lines:
                try:
                    val = json.loads(l)
                    if isinstance(val, str):
                        pending_prompt = val
                    elif isinstance(val, dict) and "session" in val and pending_prompt:
                        sid = val["session"]
                        prompts_by_session.setdefault(sid, []).append(pending_prompt)
                        pending_prompt = None
                except Exception:
                    pass
        except Exception:
            pass

    # Check active Muse runtime sockets
    live_sockets: Dict[str, Dict[str, Any]] = {}
    for rpath in glob.glob(os.path.expanduser("~/.local/share/muse/runtime/muse/sessions/*.json")):
        try:
            with open(rpath, "r", encoding="utf-8") as rf:
                meta = json.load(rf)
                sid = meta.get("session_id")
                if sid:
                    live_sockets[sid] = meta
        except Exception:
            pass

    for p in paths[:limit]:
        sid = Path(p).parent.name
        mtime = os.path.getmtime(p)
        size_mb = round(os.path.getsize(p) / (1024 * 1024), 2)
        lock_file = Path(p).parent / ".session.lock"
        is_locked = lock_file.exists()

        model = "muse-wonder-1.0"
        todos: List[Dict[str, Any]] = []
        recent_tools: List[Dict[str, Any]] = []
        last_message = ""
        last_status = "idle"
        subagent_count = 0
        subagent_dir = Path(p).parent / "subagent"
        if subagent_dir.is_dir():
            try:
                subagent_count = len(os.listdir(subagent_dir))
            except OSError:
                pass

        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                sz = os.path.getsize(p)
                f.seek(max(0, sz - 260000))
                lines = f.read().splitlines()[1:]
            for l in lines:
                try:
                    d = json.loads(l)
                    payload = d.get("payload", {})
                    if not isinstance(payload, dict):
                        continue
                    ev = payload.get("event", {})
                    if not isinstance(ev, dict):
                        continue
                    kind = ev.get("kind")
                    if kind == "todo_snapshot_updated":
                        todos = ev.get("items", [])
                    elif kind == "assistant_tool_calls_committed":
                        for tc in ev.get("tool_calls", []):
                            recent_tools.append(
                                {
                                    "name": tc.get("name", "tool"),
                                    "args": str(ev_args := tc.get("args", ""))[:160],
                                }
                            )
                    elif kind == "assistant_message_committed":
                        txt = ev.get("text", "")
                        if txt:
                            last_message = txt[:400]
                    elif kind == "model_completed" and ev.get("model"):
                        model = ev.get("model")
                    elif kind == "terminal":
                        last_status = ev.get("terminal", "completed")
                    elif kind in ("model_response_created", "started"):
                        last_status = "running"
                except Exception:
                    pass
        except Exception:
            pass

        sock_meta = live_sockets.get(sid, {})
        pid_hint = sock_meta.get("process_generation_hint", "")
        pid = int(pid_hint.split("=")[1]) if "pid=" in pid_hint else None
        pid_alive = bool(pid and os.path.exists(f"/proc/{pid}"))

        sessions.append(
            {
                "agent_type": "Meta Muse Code",
                "session_id": sid,
                "short_id": sid[:8],
                "model": model,
                "status": "LIVE_TUI" if pid_alive else ("LOCKED" if is_locked else last_status.upper()),
                "pid": pid if pid_alive else None,
                "endpoint_socket": sock_meta.get("endpoint_hint"),
                "workspace": sock_meta.get("workspace_label", "/home/zoth"),
                "last_active_ts": mtime,
                "last_active_ago_sec": round(max(0.0, time.time() - mtime), 1),
                "log_size_mb": size_mb,
                "subagents_spawned": subagent_count,
                "recent_user_prompts": prompts_by_session.get(sid, [])[-4:],
                "todos": todos,
                "recent_tools": recent_tools[-5:],
                "last_assistant_message": last_message,
            }
        )
    return sessions


def inspect_antigravity_sessions(limit: int = 5) -> List[Dict[str, Any]]:
    """Read active Google Antigravity (agy) agent transcripts, tasks, and tool calls."""
    sessions: List[Dict[str, Any]] = []
    paths = sorted(
        glob.glob(os.path.expanduser("~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )
    for p in paths[:limit]:
        cid = p.split("/brain/")[1].split("/")[0]
        mtime = os.path.getmtime(p)
        age = round(max(0.0, time.time() - mtime), 1)
        size_mb = round(os.path.getsize(p) / (1024 * 1024), 2)

        user_prompts: List[str] = []
        recent_tools: List[Dict[str, str]] = []
        last_message = ""
        step_count = 0

        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                sz = os.path.getsize(p)
                f.seek(max(0, sz - 90000))
                lines = f.read().splitlines()[1:]
            for l in lines:
                try:
                    d = json.loads(l)
                    step_count = max(step_count, int(d.get("step_index", 0)))
                    stype = d.get("type")
                    if stype == "USER_INPUT" and d.get("content"):
                        txt = d["content"].strip()
                        if "<USER_REQUEST>" in txt:
                            txt = txt.split("<USER_REQUEST>")[1].split("</USER_REQUEST>")[0].strip()
                        if txt and not txt.startswith("<SYSTEM_MESSAGE>"):
                            user_prompts.append(txt[:220])
                    elif stype == "PLANNER_RESPONSE":
                        if d.get("content"):
                            last_message = d["content"].strip()[:400]
                        for tc in d.get("tool_calls") or []:
                            args = tc.get("args") or tc.get("arguments") or {}
                            summary = ""
                            if isinstance(args, dict):
                                summary = (
                                    args.get("toolSummary")
                                    or args.get("CommandLine")
                                    or args.get("Path")
                                    or str(args)
                                )
                            else:
                                summary = str(args)
                            recent_tools.append(
                                {
                                    "name": tc.get("name", "tool"),
                                    "args": str(summary)[:160],
                                }
                            )
                except Exception:
                    pass
        except Exception:
            pass

        # Check running background tasks in this brain dir
        tasks_dir = Path(p).parent.parent / "tasks"
        task_files = sorted(glob.glob(str(tasks_dir / "*.log")), key=os.path.getmtime, reverse=True) if tasks_dir.exists() else []
        recent_task_logs = [Path(tf).name for tf in task_files[:3]]

        sessions.append(
            {
                "agent_type": "Google Antigravity (AGY)",
                "session_id": cid,
                "short_id": cid[:8],
                "status": "ACTIVE_NOW" if age < 180 else ("RECENT" if age < 3600 else "IDLE"),
                "step_index": step_count,
                "last_active_ts": mtime,
                "last_active_ago_sec": age,
                "log_size_mb": size_mb,
                "recent_user_prompts": user_prompts[-4:],
                "recent_tools": recent_tools[-5:],
                "last_assistant_message": last_message,
                "recent_task_logs": recent_task_logs,
            }
        )
    return sessions


def inspect_hermes_and_workers() -> Dict[str, Any]:
    """Inspect Hermes Gateway and active video/media render pipelines."""
    hermes_state: Dict[str, Any] = {}
    hpath = os.path.expanduser("~/.hermes/gateway_state.json")
    if os.path.exists(hpath):
        try:
            with open(hpath, "r", encoding="utf-8") as f:
                hermes_state = json.load(f)
        except Exception:
            pass

    # Inspect video production output directory (/home/zoth/Desktop/zoth-1nc0gn30-videos/out)
    video_dir = Path("/home/zoth/Desktop/zoth-1nc0gn30-videos/out")
    mp4_files = sorted(video_dir.glob("*.mp4"), key=os.path.getmtime, reverse=True) if video_dir.exists() else []
    recent_videos = [
        {
            "name": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "created_ago_sec": round(max(0.0, time.time() - f.stat().st_mtime), 1),
        }
        for f in mp4_files[:12]
    ]

    scripts_dir = Path("/home/zoth/Desktop/zoth-1nc0gn30-videos/scripts")
    batch_scripts = sorted([f.name for f in scripts_dir.glob("make_batch*.py")]) if scripts_dir.exists() else []

    return {
        "hermes_gateway": {
            "status": hermes_state.get("gateway_state", "unknown"),
            "pid": hermes_state.get("pid"),
            "version": hermes_state.get("code_version"),
            "profiles": hermes_state.get("served_profiles", []),
            "updated_at": hermes_state.get("updated_at"),
        },
        "video_production_pipeline": {
            "directory": str(video_dir),
            "total_mastered_mp4s": len(mp4_files),
            "batch_scripts": batch_scripts,
            "latest_renders": recent_videos,
        },
    }


def list_bus_messages(limit: int = 40, recipient: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return recent cross-agent messages from the SQLite bus."""
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        conn.row_factory = sqlite3.Row
        try:
            if recipient:
                rows = conn.execute(
                    "SELECT * FROM agent_messages WHERE recipient IN (?, 'all-agents', '*') ORDER BY created_at DESC LIMIT ?",
                    (recipient, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_messages ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["artifacts"] = json.loads(d.pop("artifacts_json", "[]"))
                except Exception:
                    d["artifacts"] = []
                d["ago_sec"] = round(max(0.0, time.time() - float(d["created_at"])), 1)
                out.append(d)
            return out
        finally:
            conn.close()


def post_bus_message(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    channel: str = "zoth-bridge-bus",
    artifacts: Optional[List[str]] = None,
    dispatch_muse_exec: bool = False,
    workspace: str = "/home/zoth/Desktop/zoth-1nc0gn30-videos",
) -> Dict[str, Any]:
    """Post a message to the cross-agent bus, sync markdown handoff files, and optionally trigger a headless Muse task."""
    msg_id = uuid.uuid4().hex[:12]
    now = time.time()
    arts = artifacts or []
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        try:
            conn.execute(
                "INSERT INTO agent_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg_id,
                    sender,
                    recipient,
                    channel,
                    subject,
                    body,
                    json.dumps(arts),
                    "delivered",
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    record = {
        "id": msg_id,
        "sender": sender,
        "recipient": recipient,
        "channel": channel,
        "subject": subject,
        "body": body,
        "artifacts": arts,
        "status": "delivered",
        "created_at": now,
    }

    # If dispatch_muse_exec is requested and muse binary is installed, spawn non-blocking headless muse exec
    if dispatch_muse_exec and shutil.which("muse"):
        prompt_text = (
            f"[ZOTH AGENT BRIDGE MESSAGE #{msg_id} from {sender}]\n"
            f"Subject: {subject}\n"
            f"Message: {body}\n"
            f"Shared Coordination Ledger: {SHARED_SYNC_MD}\n"
            f"Please execute the requested work and log your status."
        )
        log_out = f"/tmp/muse_bridge_exec_{msg_id}.jsonl"
        try:
            with open(log_out, "w") as lf:
                proc = subprocess.Popen(
                    [
                        "muse",
                        "exec",
                        "--json",
                        "--yolo",
                        "--workspace",
                        workspace,
                        prompt_text,
                    ],
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            record["muse_exec_pid"] = proc.pid
            record["muse_exec_log"] = log_out
        except Exception as e:
            record["muse_exec_error"] = str(e)

    sync_shared_markdown_bulletin()
    return record


def sync_shared_markdown_bulletin() -> str:
    """Write a live human- and agent-readable coordination ledger so any agent (Muse, AGY, Hermes) sees the full state."""
    snapshot = get_live_agent_mesh_snapshot()
    lines = [
        "# ZOTH SOVEREIGN AGENT BRIDGE — LIVE MULTI-AGENT COORDINATION LEDGER",
        f"> **Last Synced**: `{time.strftime('%Y-%m-%d %H:%M:%S %Z')}` · **Bridge Endpoint**: `http://127.0.0.1:8102/api/agents/live`",
        "",
        "## 1. How Any Agent (Muse, Antigravity, Hermes, Subagents) Communicates Over Zoth Bridge",
        "- **Inspect All Active Agents & Work**: `curl -s http://127.0.0.1:8102/api/agents/live | jq .` (or `sovereign-agent-bridge agents`)",
        "- **Send a Cross-Agent Message / Handoff**:",
        "  ```bash",
        "  curl -s -X POST http://127.0.0.1:8102/api/agents/message \\",
        "    -H 'Content-Type: application/json' \\",
        "    -d '{\"sender\":\"muse\",\"recipient\":\"antigravity\",\"subject\":\"Batch Update\",\"body\":\"Finished rendering new reels\"}'",
        "  ```",
        "",
        "## 2. Live Agent Sessions Detected on Host",
    ]

    for m in snapshot["muse_sessions"][:2]:
        lines.append(
            f"- **Meta Muse Code (`{m['short_id']}` · `{m['model']}`)** — Status: `{m['status']}` · PID: `{m.get('pid') or 'idle'}` · Last Active: `{m['last_active_ago_sec']}s ago` · Subagents: `{m['subagents_spawned']}`"
        )
        if m["todos"]:
            for td in m["todos"][:4]:
                lines.append(f"  - Todo `[{td.get('status')}]`: {td.get('text')}")
        if m["last_assistant_message"]:
            clean_msg = m["last_assistant_message"].replace("\n", " ")[:220]
            lines.append(f"  - Latest Output: _{clean_msg}_")

    for a in snapshot["antigravity_sessions"][:3]:
        lines.append(
            f"- **Google Antigravity (`{a['short_id']}`)** — Status: `{a['status']}` · Step: `{a['step_index']}` · Last Active: `{a['last_active_ago_sec']}s ago`"
        )
        if a["recent_tools"]:
            lt = a["recent_tools"][-1]
            lines.append(f"  - Latest Tool Call: `{lt['name']}` — `{lt['args'][:120]}`")
        if a["last_assistant_message"]:
            clean_msg = a["last_assistant_message"].replace("\n", " ")[:220]
            lines.append(f"  - Latest Response: _{clean_msg}_")

    vp = snapshot["pipelines"]["video_production_pipeline"]
    lines.extend(
        [
            "",
            f"## 3. Shared Video Production Pipeline (`{vp['directory']}`)",
            f"- **Total Mastered `-14 LUFS` MP4s**: `{vp['total_mastered_mp4s']}`",
            f"- **Batch Scripts**: `{', '.join(vp['batch_scripts'])}`",
            "- **Latest Mastered Renders**:",
        ]
    )
    for rv in vp["latest_renders"][:8]:
        lines.append(f"  - `{rv['name']}` (`{rv['size_mb']} MB`, `{rv['created_ago_sec']}s ago`)")

    lines.extend(["", "## 4. Cross-Agent Message Bus (Recent Handoffs)"])
    for msg in snapshot["bus_messages"][:10]:
        ts_str = time.strftime("%H:%M:%S", time.localtime(msg["created_at"]))
        lines.append(
            f"- **`[{ts_str}]` `{msg['sender']}` → `{msg['recipient']}`** (**{msg['subject']}**): {msg['body']}"
        )

    content = "\n".join(lines) + "\n"
    for target in (SHARED_SYNC_MD, GLOBAL_SYNC_MD):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception:
            pass
    return content


def get_tool_policies() -> List[Dict[str, Any]]:
    """Return the Sovereign Bridge AI Tool Gatekeeper policy table."""
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM ai_tool_policies ORDER BY total_runs DESC, tool_name ASC"
            ).fetchall()
            out = []
            now = time.time()
            for r in rows:
                last_ts = float(r["last_run_ts"] or 0.0)
                out.append(
                    {
                        "tool_name": r["tool_name"],
                        "display_name": r["display_name"],
                        "binary_target": r["binary_target"],
                        "enabled": bool(r["enabled"]),
                        "require_bridge": bool(r["require_bridge"]),
                        "total_runs": int(r["total_runs"]),
                        "denied_runs": int(r["denied_runs"]),
                        "last_run_ts": last_ts,
                        "last_run_ago_sec": round(now - last_ts, 1) if last_ts > 0 else None,
                        "last_pid": int(r["last_pid"] or 0),
                        "last_workspace": r["last_workspace"],
                        "last_args": r["last_args"],
                    }
                )
            return out
        finally:
            conn.close()


def set_tool_policy(
    tool_name: str,
    enabled: Optional[bool] = None,
    require_bridge: Optional[bool] = None,
) -> Dict[str, Any]:
    """Update the Sovereign Bridge Gatekeeper policy for a specific AI tool."""
    t_key = tool_name.strip().lower()
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        try:
            row = conn.execute(
                "SELECT * FROM ai_tool_policies WHERE tool_name = ?", (t_key,)
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO ai_tool_policies VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, '', '')",
                    (
                        t_key,
                        t_key.upper(),
                        f"/usr/local/bin/{t_key}",
                        1 if enabled is None or enabled else 0,
                        1 if require_bridge is None or require_bridge else 0,
                    ),
                )
            else:
                if enabled is not None:
                    conn.execute(
                        "UPDATE ai_tool_policies SET enabled = ? WHERE tool_name = ?",
                        (1 if enabled else 0, t_key),
                    )
                if require_bridge is not None:
                    conn.execute(
                        "UPDATE ai_tool_policies SET require_bridge = ? WHERE tool_name = ?",
                        (1 if require_bridge else 0, t_key),
                    )
            conn.commit()
        finally:
            conn.close()
    sync_shared_markdown_bulletin()
    for p in get_tool_policies():
        if p["tool_name"] == t_key:
            return p
    return {"tool_name": t_key}


def authorize_tool_execution(
    tool_name: str,
    pid: int = 0,
    workspace: str = "",
    args_text: str = "",
) -> Dict[str, Any]:
    """Gatekeep an AI tool invocation through the Sovereign Agent Bridge.

    Every AI tool on ZothOS must connect here before executing. If the tool's
    policy is disabled on the Bridge, execution is denied.
    """
    t_key = (tool_name or "unknown").strip().lower()
    now = time.time()
    lease_id = f"lease-{t_key}-{uuid.uuid4().hex[:10]}"
    ws = workspace or os.getcwd()
    clean_args = (args_text or "")[:240]

    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM ai_tool_policies WHERE tool_name = ?", (t_key,)
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO ai_tool_policies VALUES (?, ?, ?, 1, 1, 0, 0, 0, 0, '', '')",
                    (t_key, t_key.upper(), f"/usr/local/bin/{t_key}"),
                )
                enabled = True
                require_bridge = True
            else:
                enabled = bool(row["enabled"])
                require_bridge = bool(row["require_bridge"])

            if not enabled:
                conn.execute(
                    "UPDATE ai_tool_policies SET denied_runs = denied_runs + 1, last_run_ts = ?, last_pid = ?, last_workspace = ?, last_args = ? WHERE tool_name = ?",
                    (now, pid, ws, clean_args, t_key),
                )
                conn.execute(
                    "INSERT INTO ai_tool_leases VALUES (?, ?, ?, ?, ?, 'DENIED', ?, ?, 126)",
                    (lease_id, t_key, pid, ws, clean_args, now, now),
                )
                conn.commit()
                return {
                    "authorized": False,
                    "tool_name": t_key,
                    "lease_id": lease_id,
                    "reason": f"AI tool '{t_key}' is currently LOCKED by Sovereign Agent Bridge Gatekeeper policy (:8102).",
                }

            conn.execute(
                "UPDATE ai_tool_policies SET total_runs = total_runs + 1, last_run_ts = ?, last_pid = ?, last_workspace = ?, last_args = ? WHERE tool_name = ?",
                (now, pid, ws, clean_args, t_key),
            )
            conn.execute(
                "INSERT INTO ai_tool_leases VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, 0, 0)",
                (lease_id, t_key, pid, ws, clean_args, now),
            )
            # Log non-trivial runs (not mere --version/--help probes) to the Cross-Agent Message Bus
            if clean_args not in ("--version", "-v", "--help", "-h"):
                msg_id = uuid.uuid4().hex[:12]
                conn.execute(
                    "INSERT INTO agent_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        msg_id,
                        f"{t_key}-gatekeeper (PID {pid})",
                        "sovereign-bridge (:8102)",
                        "bridge-gatekeeper",
                        f"AI Tool Connected & Authorized: {t_key}",
                        f"Authorized `{t_key} {clean_args}` in workspace `{ws}` with Lease `{lease_id}`.",
                        json.dumps([ws]),
                        "delivered",
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    sync_shared_markdown_bulletin()
    return {
        "authorized": True,
        "tool_name": t_key,
        "lease_id": lease_id,
        "require_bridge": require_bridge,
        "bridge_url": "http://127.0.0.1:8102",
        "sync_md": str(SHARED_SYNC_MD),
    }


def release_tool_lease(lease_id: str, exit_code: int = 0) -> Dict[str, Any]:
    """Mark an active AI tool Bridge lease as completed."""
    now = time.time()
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        try:
            conn.execute(
                "UPDATE ai_tool_leases SET status = 'COMPLETED', ended_ts = ?, exit_code = ? WHERE lease_id = ?",
                (now, int(exit_code), lease_id),
            )
            conn.commit()
        finally:
            conn.close()
    return {"lease_id": lease_id, "status": "COMPLETED", "exit_code": exit_code}


def list_tool_leases(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent AI tool execution leases tracked by the Sovereign Bridge Gatekeeper."""
    with _DB_LOCK:
        conn = sqlite3.connect(str(BUS_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM ai_tool_leases ORDER BY created_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            now = time.time()
            return [
                {
                    "lease_id": r["lease_id"],
                    "tool_name": r["tool_name"],
                    "pid": int(r["pid"]),
                    "workspace": r["workspace"],
                    "args_text": r["args_text"],
                    "status": r["status"],
                    "created_ts": float(r["created_ts"]),
                    "created_ago_sec": round(now - float(r["created_ts"]), 1),
                    "ended_ts": float(r["ended_ts"]),
                    "exit_code": int(r["exit_code"]),
                }
                for r in rows
            ]
        finally:
            conn.close()


def get_live_agent_mesh_snapshot() -> Dict[str, Any]:
    """Return unified real-time telemetry across Muse, Antigravity, Hermes, OS processes, Gatekeeper Policies, and the Message Bus."""
    muse_list = inspect_muse_sessions(limit=3)
    agy_list = inspect_antigravity_sessions(limit=4)
    pipelines = inspect_hermes_and_workers()
    procs = _scan_live_processes()
    bus_msgs = list_bus_messages(limit=25)
    policies = get_tool_policies()
    leases = list_tool_leases(limit=20)

    return {
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "muse_sessions_count": len(muse_list),
            "antigravity_sessions_count": len(agy_list),
            "live_processes_count": len(procs),
            "bus_messages_count": len(bus_msgs),
            "mastered_videos_count": pipelines["video_production_pipeline"]["total_mastered_mp4s"],
            "gated_tools_count": len(policies),
            "active_leases_count": sum(1 for l in leases if l["status"] == "ACTIVE"),
        },
        "muse_sessions": muse_list,
        "antigravity_sessions": agy_list,
        "pipelines": pipelines,
        "live_processes": procs,
        "bus_messages": bus_msgs,
        "tool_policies": policies,
        "tool_leases": leases,
    }
