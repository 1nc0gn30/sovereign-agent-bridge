"""
Sovereign Agent Bridge - Command Line Interface (CLI) & Runtime Engine.

Provides multi-OS terminal commands, MCP stdio runner, heartbeat watchdog monitor,
claim mutex locks, 3-way dialectic consensus, system doctor diagnostics, self-test suite,
and the embedded Bridge Studio Web UI (design influenced by Material 3).
Zero external dependencies (pure Python standard library).
"""

from __future__ import annotations

import os
import sys
import json
import time
import socket
import logging
import platform
import argparse
import threading
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

# Version metadata
__version__ = "0.1.0"

# Import internal modules
try:
    from .watchdog import (
        HeartbeatWatchdog,
        AgentPulse,
        AgentStatus,
        DeadManSwitchAlert,
        get_default_watchdog,
    )
    from .mcp_server import MCPServer, FallbackClaimManager, FallbackConsensusEngine
except ImportError:
    try:
        from watchdog import (
            HeartbeatWatchdog,
            AgentPulse,
            AgentStatus,
            DeadManSwitchAlert,
            get_default_watchdog,
        )
        from mcp_server import MCPServer, FallbackClaimManager, FallbackConsensusEngine
    except ImportError:
        HeartbeatWatchdog = None
        AgentPulse = None
        AgentStatus = None
        DeadManSwitchAlert = None
        get_default_watchdog = None
        MCPServer = None
        FallbackClaimManager = None
        FallbackConsensusEngine = None


# ---------------------------------------------------------------------------
# ANSI Terminal Styling Helper
# ---------------------------------------------------------------------------

class Color:
    """ANSI color codes with automatic terminal capability detection."""
    _ENABLED = True

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    # Colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright Colors
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Backgrounds
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        cls._ENABLED = enabled

    @classmethod
    def c(cls, text: str, color_code: str) -> str:
        if not cls._ENABLED:
            return text
        return f"{color_code}{text}{cls.RESET}"

    @classmethod
    def bold(cls, text: str) -> str:
        return cls.c(text, cls.BOLD)

    @classmethod
    def cyan(cls, text: str) -> str:
        return cls.c(text, cls.CYAN)

    @classmethod
    def green(cls, text: str) -> str:
        return cls.c(text, cls.GREEN)

    @classmethod
    def yellow(cls, text: str) -> str:
        return cls.c(text, cls.YELLOW)

    @classmethod
    def red(cls, text: str) -> str:
        return cls.c(text, cls.RED)

    @classmethod
    def magenta(cls, text: str) -> str:
        return cls.c(text, cls.MAGENTA)

    @classmethod
    def dim(cls, text: str) -> str:
        return cls.c(text, cls.DIM)


def print_banner(no_banner: bool = False) -> None:
    """Print ASCII Art Header."""
    if no_banner:
        return
    banner = f"""
{Color.cyan("   _____  ____  _      __ ______ ____   ______ _____  _   __ ")}
{Color.cyan("  / ___/ / __ \\| | /| / // ____// __ \\ / ____//  _/ / | / / ")}
{Color.cyan("  \\__ \\ / / / /| |/ |/ // __/  / /_/ // __/   / /  /  |/ /  ")}
{Color.cyan(" ___/ // /_/ / | /| / // /___ / _, _// /___ _/ /  / /|  /   ")}
{Color.cyan("/____/ \\____/  |/_/|/_//_____//_/ |_|/_____//___/ /_/ |_/    ")}
{Color.bold("AGENT BRIDGE")} {Color.dim("v" + __version__)} {Color.yellow("::")} {Color.magenta("SOVEREIGN MULTI-CHANNEL RUNTIME")}
"""
    print(banner)


# ---------------------------------------------------------------------------
# Embedded Google Material 3 Bridge Studio Web UI
# ---------------------------------------------------------------------------

FALLBACK_INDEX_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sovereign Agent Bridge Studio</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;600&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f17;
      --surface: #121824;
      --surface-elevated: #1a2234;
      --primary: #38bdf8;
      --primary-rgb: 56, 189, 248;
      --secondary: #818cf8;
      --accent: #a855f7;
      --success: #34d399;
      --warning: #fbbf24;
      --danger: #f87171;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --border: rgba(255, 255, 255, 0.08);
      --font-main: 'Plus Jakarta Sans', system-ui, sans-serif;
      --font-mono: 'Fira Code', monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: var(--font-main);
      min-height: 100vh;
      line-height: 1.5;
    }
    header {
      background: linear-gradient(180deg, rgba(18, 24, 36, 0.95) 0%, rgba(11, 15, 23, 0.8) 100%);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 1rem 2rem;
      position: sticky;
      top: 0;
      z-index: 50;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 1.25rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: #fff;
    }
    .logo-badge {
      background: linear-gradient(135deg, var(--primary), var(--accent));
      color: #000;
      font-size: 0.7rem;
      font-weight: 800;
      padding: 0.2rem 0.5rem;
      border-radius: 9999px;
      text-transform: uppercase;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(52, 211, 153, 0.1);
      border: 1px solid rgba(52, 211, 153, 0.3);
      color: var(--success);
      padding: 0.35rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 10px var(--success);
      animation: pulse 2s infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    main {
      max-width: 1400px;
      margin: 0 auto;
      padding: 2rem;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 1.5rem;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      position: relative;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }
    .card-title {
      font-size: 1.1rem;
      font-weight: 700;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #fff;
    }
    .col-4 { grid-column: span 4; }
    .col-6 { grid-column: span 6; }
    .col-8 { grid-column: span 8; }
    .col-12 { grid-column: span 12; }
    @media (max-width: 1024px) {
      .col-4, .col-6, .col-8 { grid-column: span 12; }
    }
    .channels-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 1rem;
    }
    .ch-card {
      background: var(--surface-elevated);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem;
      transition: all 0.2s ease;
    }
    .ch-card:hover {
      border-color: var(--primary);
      transform: translateY(-2px);
    }
    .ch-name { font-weight: 700; font-size: 0.95rem; margin-bottom: 0.25rem; }
    .ch-status { font-size: 0.75rem; color: var(--success); font-weight: 600; }
    .form-group { margin-bottom: 1rem; }
    label { display: block; font-size: 0.8rem; font-weight: 600; color: var(--text-muted); margin-bottom: 0.35rem; }
    input, select, textarea {
      width: 100%;
      background: var(--surface-elevated);
      border: 1px solid var(--border);
      color: var(--text);
      font-family: inherit;
      padding: 0.65rem 0.85rem;
      border-radius: 8px;
      font-size: 0.9rem;
      outline: none;
      transition: border-color 0.2s;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--primary); }
    button {
      background: linear-gradient(135deg, var(--primary), var(--secondary));
      color: #0b0f17;
      border: none;
      font-weight: 700;
      font-size: 0.9rem;
      padding: 0.7rem 1.25rem;
      border-radius: 8px;
      cursor: pointer;
      transition: opacity 0.2s, transform 0.1s;
    }
    button:hover { opacity: 0.95; transform: translateY(-1px); }
    button:active { transform: translateY(0); }
    pre {
      background: #06090e;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
      color: #38bdf8;
      font-family: var(--font-mono);
      font-size: 0.8rem;
      max-height: 240px;
      overflow-y: auto;
    }
    .stat-number { font-size: 2rem; font-weight: 800; color: #fff; line-height: 1.1; margin-bottom: 0.25rem; }
    .stat-label { font-size: 0.8rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; }
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span>SOVEREIGN AGENT BRIDGE</span>
      <span class="logo-badge">STUDIO</span>
    </div>
    <div class="status-pill">
      <span class="status-dot"></span>
      <span id="bridge-status">RUNTIME ACTIVE</span>
    </div>
  </header>

  <main>
    <div class="card col-4">
      <div class="stat-number" id="stat-messages">0</div>
      <div class="stat-label">Messages Routed</div>
    </div>
    <div class="card col-4">
      <div class="stat-number" id="stat-pulses">0</div>
      <div class="stat-label">Agent Pulses</div>
    </div>
    <div class="card col-4">
      <div class="stat-number" id="stat-locks">0</div>
      <div class="stat-label">Active Mutex Claims</div>
    </div>

    <div class="card col-12">
      <div class="card-title">
        <span>Sovereign Channel Mesh</span>
        <button onclick="refreshChannels()" style="padding: 0.4rem 0.8rem; font-size: 0.75rem;">Probe Latency</button>
      </div>
      <div class="channels-grid" id="channels-container">
        <!-- Injected dynamically -->
      </div>
    </div>

    <div class="card col-6">
      <div class="card-title">Dispatch Message</div>
      <form onsubmit="sendMessage(event)">
        <div class="form-group">
          <label>Target Channel</label>
          <select id="msg-channel">
            <option value="auto">Auto / Smart Route</option>
            <option value="signal">Signal (E2EE)</option>
            <option value="simplex">SimpleX (Metadata-free)</option>
            <option value="telegram">Telegram Bot</option>
            <option value="matrix">Matrix Mesh</option>
            <option value="webhook">REST Webhook</option>
          </select>
        </div>
        <div class="form-group">
          <label>Recipient (Optional)</label>
          <input type="text" id="msg-recipient" placeholder="Phone, Matrix ID, Chat ID...">
        </div>
        <div class="form-group">
          <label>Payload</label>
          <textarea id="msg-text" rows="3" placeholder="Enter message payload for autonomous agents..."></textarea>
        </div>
        <button type="submit">Send Message</button>
      </form>
    </div>

    <div class="card col-6">
      <div class="card-title">3-Way Dialectic Consensus</div>
      <form onsubmit="runConsensus(event)">
        <div class="form-group">
          <label>Proposal / Thesis</label>
          <textarea id="cons-proposal" rows="3" placeholder="Propose architectural decision or plan..."></textarea>
        </div>
        <button type="submit">Run Dialectic Consensus</button>
      </form>
    </div>

    <div class="card col-12">
      <div class="card-title">Live Telemetry & Logs</div>
      <pre id="output-log">// Sovereign Agent Bridge Studio initialized.\n// Ready for JSON-RPC 2.0 MCP connections & REST invocations.</pre>
    </div>
  </main>

  <script>
    const logEl = document.getElementById('output-log');
    function log(msg) {
      logEl.textContent = msg + '\\n' + logEl.textContent;
    }

    async function loadStats() {
      try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        document.getElementById('stat-messages').textContent = data.telemetry.messages_sent || 0;
        document.getElementById('stat-pulses').textContent = data.telemetry.monitored_agents || 0;
        document.getElementById('stat-locks').textContent = data.telemetry.active_locks || 0;
      } catch (e) {
        console.error(e);
      }
    }

    async function refreshChannels() {
      try {
        const res = await fetch('/api/channels');
        const data = await res.json();
        const container = document.getElementById('channels-container');
        container.innerHTML = '';
        data.channels.forEach(ch => {
          const div = document.createElement('div');
          div.className = 'ch-card';
          div.innerHTML = `
            <div class="ch-name">${ch.name}</div>
            <div class="ch-status">● ${ch.status}</div>
            <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 0.3rem;">${ch.encryption}</div>
          `;
          container.appendChild(div);
        });
        log('Channels probed: ' + data.channels.length + ' active adapters.');
      } catch (e) {
        log('Error fetching channels: ' + e);
      }
    }

    async function sendMessage(e) {
      e.preventDefault();
      const channel = document.getElementById('msg-channel').value;
      const recipient = document.getElementById('msg-recipient').value;
      const message = document.getElementById('msg-text').value;
      if (!message) return;
      try {
        const res = await fetch('/api/send', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ channel, recipient, message })
        });
        const out = await res.json();
        log('Send Result: ' + JSON.stringify(out, null, 2));
        loadStats();
      } catch (err) {
        log('Send Failed: ' + err);
      }
    }

    async function runConsensus(e) {
      e.preventDefault();
      const proposal = document.getElementById('cons-proposal').value;
      if (!proposal) return;
      try {
        const res = await fetch('/api/consensus', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ proposal })
        });
        const out = await res.json();
        log('Consensus Verdict: ' + JSON.stringify(out, null, 2));
      } catch (err) {
        log('Consensus Failed: ' + err);
      }
    }

    refreshChannels();
    loadStats();
    setInterval(loadStats, 5000);
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Threaded HTTP Server Handler for Bridge Studio
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server."""
    daemon_threads = True


class BridgeStudioHTTPHandler(SimpleHTTPRequestHandler):
    """Serves static files and REST API endpoints for Sovereign Bridge."""

    def __init__(self, *args, public_dir: Optional[Path] = None, **kwargs):
        self.public_dir = public_dir or (Path(__file__).parent.parent.parent / "public")
        super().__init__(*args, **kwargs)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        """Route GET API requests or serve static assets."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json({"status": "HEALTHY", "version": __version__, "timestamp": time.time()})
        elif path == "/api/stats":
            srv = MCPServer()
            res = srv.execute_tool("bridge_stats", {"detailed": True})
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/channels":
            srv = MCPServer()
            res = srv.execute_tool("bridge_list_channels", {"test_health": False})
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/heartbeats":
            wd = get_default_watchdog() if get_default_watchdog else HeartbeatWatchdog()
            self._send_json({"agents": wd.list_agents_dict(), "telemetry": wd.get_telemetry()})
        elif path == "/api/claims":
            claims_mgr = FallbackClaimManager()
            self._send_json({"claims": claims_mgr.list_claims()})
        elif path in ("/", "/index.html"):
            index_path = self.public_dir / "index.html"
            if index_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(index_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(FALLBACK_INDEX_HTML.encode("utf-8"))
        else:
            # Try serving static file from public_dir
            static_file = self.public_dir / path.lstrip("/")
            if static_file.exists() and static_file.is_file():
                self.send_response(200)
                content_type = "text/plain"
                if static_file.suffix == ".html":
                    content_type = "text/html"
                elif static_file.suffix == ".css":
                    content_type = "text/css"
                elif static_file.suffix == ".js":
                    content_type = "application/javascript"
                elif static_file.suffix == ".json":
                    content_type = "application/json"
                self.send_header("Content-Type", content_type)
                self.end_headers()
                with open(static_file, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, f"Path not found: {path}")

    def do_POST(self):
        """Route POST API requests."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        srv = MCPServer()

        if path == "/api/send":
            res = srv.execute_tool("bridge_send", payload)
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/broadcast":
            res = srv.execute_tool("bridge_broadcast", payload)
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/claim":
            res = srv.execute_tool("bridge_claim_project", payload)
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/consensus":
            res = srv.execute_tool("bridge_consensus", payload)
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/pulse":
            res = srv.execute_tool("bridge_heartbeat", payload)
            self._send_json(json.loads(res["content"][0]["text"]))
        elif path == "/api/mcp":
            rpc_res = srv.handle_request(body)
            self._send_json(rpc_res or {})
        else:
            self.send_error(404, f"API endpoint not found: {path}")

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard request logging to keep console clean
        pass


# ---------------------------------------------------------------------------
# CLI Command Handlers
# ---------------------------------------------------------------------------

def handle_send(args: argparse.Namespace) -> int:
    """Send a routed message."""
    srv = MCPServer()
    params = {
        "channel": args.channel,
        "message": args.message,
        "recipient": args.recipient,
        "attachment": args.attachment,
        "agent_id": args.agent_id or "cli-agent",
    }
    res = srv.execute_tool("bridge_send", params)
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    if res.get("isError"):
        print(f"{Color.red('✗ FAILED:')} {data.get('error')}")
        return 1

    print(f"{Color.green('✓ Message Sent Successfully')}")
    print(f"  {Color.dim('ID:')}        {data.get('message_id')}")
    print(f"  {Color.dim('Channel:')}   {Color.cyan(data.get('channel'))}")
    print(f"  {Color.dim('Recipient:')} {data.get('recipient')}")
    print(f"  {Color.dim('Latency:')}   {data.get('delivery_latency_ms')} ms")
    return 0


def handle_broadcast(args: argparse.Namespace) -> int:
    """Broadcast a message across sovereign channels."""
    srv = MCPServer()
    channels_list = [c.strip() for c in args.channels.split(",")] if args.channels else None
    params = {
        "message": args.message,
        "channels": channels_list,
        "priority": args.priority,
        "agent_id": args.agent_id or "cli-broadcaster",
    }
    res = srv.execute_tool("bridge_broadcast", params)
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"{Color.green('✓ Broadcast Dispatched')}")
    print(f"  {Color.dim('Broadcast ID:')} {data.get('broadcast_id')}")
    print(f"  {Color.dim('Priority:')}     {Color.yellow(data.get('priority'))}")
    print(f"  {Color.dim('Channels:')}     {', '.join(data.get('channels_targeted', []))}")
    return 0


def handle_channels(args: argparse.Namespace) -> int:
    """List channels and health."""
    srv = MCPServer()
    res = srv.execute_tool("bridge_list_channels", {"test_health": args.test})
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"{Color.bold('Connected Sovereign Channels')} ({data.get('healthy_count')}/{data.get('total_channels')} Healthy)\n")
    for ch in data.get("channels", []):
        cfg = Color.green("CONFIGURED") if ch.get("configured") else Color.dim("AVAILABLE")
        stat = Color.green(ch.get("status")) if ch.get("status") in ("HEALTHY", "ONLINE") else Color.red(ch.get("status"))
        lat = f" ({ch.get('latency_ms')} ms)" if ch.get("latency_ms") is not None else ""
        print(f"  • {Color.bold(ch.get('name')):<28} [{stat}{lat}] [{cfg}]")
        print(f"    {Color.dim('Protocol:')} {ch.get('encryption')}")
    print()
    return 0


def handle_claim(args: argparse.Namespace) -> int:
    """Manage project mutex locks."""
    srv = MCPServer()
    action = "claim"
    if args.release:
        action = "release"
    elif args.handoff_to:
        action = "handoff"
    elif args.status:
        action = "status"
    elif args.list:
        action = "list"

    params = {
        "project_id": args.project_id or "default-project",
        "agent_id": args.agent_id or f"agent-{socket.gethostname()}",
        "action": action,
        "ttl": args.ttl,
        "handoff_to": args.handoff_to,
    }
    res = srv.execute_tool("bridge_claim_project", params)
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    if data.get("success", False):
        print(f"{Color.green('✓ Claim Success:')} {data.get('message', action)}")
        if "expires_at" in data:
            print(f"  {Color.dim('Lock expires in:')} {round(data.get('expires_at', 0) - time.time(), 1)}s")
        return 0
    else:
        print(f"{Color.yellow('! Claim Notice:')} {data.get('message', 'Lock conflict')}")
        if data.get("held_by"):
            print(f"  {Color.dim('Currently held by:')} {Color.cyan(data.get('held_by'))}")
        return 1


def handle_consensus(args: argparse.Namespace) -> int:
    """Execute 3-way dialectic consensus deliberation."""
    srv = MCPServer()
    params = {
        "proposal": args.proposal,
        "proponent": args.proponent,
        "skeptic": args.skeptic,
        "arbitrator": args.arbitrator,
        "mode": args.mode,
        "rounds": args.rounds,
    }
    res = srv.execute_tool("bridge_consensus", params)
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"\n{Color.bold('3-Way Dialectic Consensus Results')}")
    print(f"  {Color.dim('Consensus ID:')}   {data.get('consensus_id')}")
    print(f"  {Color.dim('Proposal:')}       {Color.cyan(data.get('proposal'))}")
    print(f"  {Color.dim('Final Decision:')} {Color.green(Color.bold(data.get('final_decision')))}\n")

    delib = data.get("deliberation", {})
    t = delib.get("thesis", {})
    a = delib.get("antithesis", {})
    s = delib.get("synthesis", {})

    print(f"  {Color.bold('1. THESIS')} ({t.get('agent')}):")
    print(f"     {t.get('argument')}\n")

    print(f"  {Color.bold('2. ANTITHESIS')} ({a.get('agent')}):")
    print(f"     {a.get('argument')}")
    for concern in a.get("concerns", []):
        print(f"     {Color.yellow('•')} {concern}")
    print()

    print(f"  {Color.bold('3. SYNTHESIS')} ({s.get('agent')}):")
    print(f"     {s.get('summary')}")
    for cond in s.get("ratified_conditions", []):
        print(f"     {Color.green('✓')} {cond}")
    print()
    return 0


def handle_pulse(args: argparse.Namespace) -> int:
    """Send heartbeat pulse or check status."""
    srv = MCPServer()
    action = "pulse"
    if args.status:
        action = "status"
    elif args.list:
        action = "list"

    params = {
        "agent_id": args.agent_id,
        "action": action,
        "interval": args.interval,
        "timeout": args.timeout,
        "metadata": {"cli_invoked": True, "task": args.task} if args.task else {},
    }
    res = srv.execute_tool("bridge_heartbeat", params)
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    if action == "pulse":
        pulse = data.get("pulse", {})
        print(f"{Color.green('✓ Heartbeat Pulse Recorded')}")
        print(f"  {Color.dim('Agent ID:')}  {Color.cyan(pulse.get('agent_id'))}")
        print(f"  {Color.dim('Status:')}    {Color.green(pulse.get('status'))}")
        print(f"  {Color.dim('Interval:')}  {pulse.get('interval')}s (timeout: {pulse.get('timeout')}s)")
    elif action == "list":
        agents = data.get("agents", [])
        print(f"{Color.bold('Registered Agent Heartbeats')} ({len(agents)} Total)\n")
        for a in agents:
            stat_color = Color.green if a.get("status") == "ACTIVE" else Color.red
            print(f"  • {Color.bold(a.get('agent_id')):<24} [{stat_color(a.get('status'))}] last pulse: {a.get('elapsed_seconds')}s ago")
    return 0


def handle_watchdog(args: argparse.Namespace) -> int:
    """Start continuous heartbeat watchdog monitoring daemon."""
    print(f"{Color.bold('Starting Sovereign Agent Watchdog Monitor')} (interval={args.interval}s)...")
    wd = HeartbeatWatchdog(
        check_interval=args.interval,
        storage_path=args.storage,
        alert_log_path=args.alerts_log,
        webhook_url=args.webhook,
    )

    def _on_alert(alert: DeadManSwitchAlert):
        sev_color = Color.red if alert.severity == "CRITICAL" else Color.yellow
        print(f"\n{sev_color('[ALERT - ' + alert.severity + ']')} {alert.reason}")

    wd.add_alert_handler(_on_alert)
    wd.start()

    print(f"{Color.green('✓ Watchdog active.')} Press Ctrl+C to terminate.\n")
    try:
        while True:
            time.sleep(2.0)
            telemetry = wd.get_telemetry()
            sys.stdout.write(
                f"\r{Color.dim('Agents:')} {telemetry['total_monitored_agents']} | "
                f"{Color.green('Active:')} {telemetry['active_agents']} | "
                f"{Color.red('Dead:')} {telemetry['dead_agents']} | "
                f"{Color.dim('Pulses:')} {telemetry['total_pulses_recorded']} | "
                f"{Color.dim('Uptime:')} {telemetry['uptime_seconds']}s"
            )
            sys.stdout.flush()
    except KeyboardInterrupt:
        print(f"\n{Color.yellow('Shutting down watchdog...')}")
        wd.stop()
        print(f"{Color.green('✓ Watchdog stopped cleanly.')}")
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    """Launch Bridge Studio Web UI."""
    host = args.host
    port = args.port
    public_path = Path(args.public_dir) if args.public_dir else None

    handler_factory = lambda *a, **kw: BridgeStudioHTTPHandler(*a, public_dir=public_path, **kw)
    httpd = ThreadedHTTPServer((host, port), handler_factory)

    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    print(f"{Color.bold('Sovereign Bridge Studio Web UI')} listening on {Color.cyan(url)}")
    print(f"  {Color.dim('REST API endpoints:')} /api/health, /api/stats, /api/channels, /api/claims, /api/send")
    print(f"  {Color.dim('MCP HTTP Gateway:')}   /api/mcp")
    print(f"  {Color.dim('Press Ctrl+C to stop.')}\n")

    # Start background watchdog
    wd = get_default_watchdog() if get_default_watchdog else HeartbeatWatchdog()
    if wd and not wd.is_running():
        wd.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{Color.yellow('Stopping Bridge Studio HTTP Server...')}")
        httpd.server_close()
        if wd:
            wd.stop()
        print(f"{Color.green('✓ Server stopped.')}")
    return 0


def handle_mcp(args: argparse.Namespace) -> int:
    """Run Model Context Protocol stdio server."""
    server = MCPServer()
    server.run_stdio()
    return 0


def handle_diagnostics(args: argparse.Namespace) -> int:
    """Perform system health diagnostics."""
    srv = MCPServer()
    res = srv.execute_tool("bridge_diagnostics", {"verbose": True})
    data = json.loads(res["content"][0]["text"])

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"{Color.bold('Sovereign Agent Bridge Doctor / Diagnostics')}\n")
    print(f"  {Color.green('✓')} Python Version:    {data.get('python_version')} ({data.get('architecture')})")
    print(f"  {Color.green('✓')} OS Platform:      {data.get('os_platform')}")
    print(f"  {Color.green('✓')} Hostname:         {data.get('hostname')}")
    print(f"  {Color.green('✓')} CPU Cores:        {data.get('cpu_cores')}")
    print(f"  {Color.green('✓')} MCP Protocol:     {data.get('mcp_protocol')}")
    print(f"  {Color.green('✓')} Mutex Locks:      {data.get('lock_subsystem')}")
    print(f"  {Color.green('✓')} Consensus Engine:  {data.get('consensus_subsystem')}")
    print()
    return 0


def handle_test(args: argparse.Namespace) -> int:
    """Run internal self-verification test suite."""
    print(f"{Color.bold('Running Sovereign Agent Bridge Self-Verification Test Suite...')}\n")
    tests_passed = 0
    total_tests = 6

    # Test 1: Watchdog Pulse & Alerting
    try:
        wd = HeartbeatWatchdog(check_interval=0.5, auto_save=False)
        wd.record_pulse("test-agent", interval=1.0, timeout=2.0)
        p = wd.get_agent("test-agent")
        assert p is not None and p.status == AgentStatus.ACTIVE
        # Trigger liveness sweep with time in future
        alerts = wd.check_liveness(now=time.time() + 3.0)
        assert len(alerts) > 0 and alerts[0].status == AgentStatus.DEAD
        print(f"  {Color.green('✓')} [1/6] Heartbeat Watchdog & Dead-Man Switch Alerting")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [1/6] Watchdog test failed: {e}")

    # Test 2: Mutex Claim Manager
    try:
        cm = FallbackClaimManager()
        c1 = cm.claim("proj-alpha", "agent-1", ttl=10.0)
        assert c1["success"] is True
        c2 = cm.claim("proj-alpha", "agent-2", ttl=10.0)
        assert c2["success"] is False  # Conflict
        cm.release("proj-alpha", "agent-1")
        c3 = cm.claim("proj-alpha", "agent-2", ttl=10.0)
        assert c3["success"] is True
        print(f"  {Color.green('✓')} [2/6] Distributed Claim Manager & Mutex Locking")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [2/6] Claim Manager test failed: {e}")

    # Test 3: Consensus Engine
    try:
        ce = FallbackConsensusEngine()
        cons = ce.run_consensus("Deploy autonomous mesh network")
        assert cons["final_decision"] == "APPROVED_WITH_CONDITIONS"
        assert len(cons["deliberation"]["synthesis"]["ratified_conditions"]) > 0
        print(f"  {Color.green('✓')} [3/6] 3-Way Dialectic Consensus Engine")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [3/6] Consensus Engine test failed: {e}")

    # Test 4: MCP Protocol RPC
    try:
        srv = MCPServer()
        init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8")
        resp = srv.handle_request(init_req)
        assert resp["result"]["serverInfo"]["name"] == "sovereign-agent-bridge"
        tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode("utf-8")
        tools_resp = srv.handle_request(tools_req)
        assert len(tools_resp["result"]["tools"]) == 8
        print(f"  {Color.green('✓')} [4/6] Model Context Protocol (MCP) JSON-RPC 2.0 Handler")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [4/6] MCP Server test failed: {e}")

    # Test 5: Tool Execution Dispatch
    try:
        srv = MCPServer()
        call_res = srv.execute_tool("bridge_send", {"channel": "simplex", "message": "Unit test message"})
        assert call_res["isError"] is False
        bcast_res = srv.execute_tool("bridge_broadcast", {"message": "Unit broadcast message"})
        assert bcast_res["isError"] is False
        print(f"  {Color.green('✓')} [5/6] Multi-Channel Router & Broadcast Dispatchers")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [5/6] Tool Execution test failed: {e}")

    # Test 6: CLI & Diagnostics
    try:
        srv = MCPServer()
        diag = srv.execute_tool("bridge_diagnostics", {"verbose": False})
        assert diag["isError"] is False
        print(f"  {Color.green('✓')} [6/6] System Diagnostics & Environment Inspection")
        tests_passed += 1
    except Exception as e:
        print(f"  {Color.red('✗')} [6/6] Diagnostics test failed: {e}")

    print(f"\n{Color.bold('Test Summary:')} {tests_passed}/{total_tests} Tests Passed.")
    return 0 if tests_passed == total_tests else 1


# ---------------------------------------------------------------------------
# Argument Parser & Entry Point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="sovereign-bridge",
        description="Sovereign Agent Bridge :: Zero-dependency multi-channel messaging, MCP server, consensus engine, and dead-man switch watchdog.",
    )
    parser.add_argument("-v", "--version", action="version", version=f"sovereign-agent-bridge {__version__}")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON data.")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # send
    p_send = subparsers.add_parser("send", help="Send a message to a specific channel.")
    p_send.add_argument("channel", choices=["signal", "simplex", "telegram", "matrix", "webhook", "auto", "local"], help="Channel adapter.")
    p_send.add_argument("message", help="Message text content.")
    p_send.add_argument("-r", "--recipient", help="Recipient ID / number / address.")
    p_send.add_argument("-a", "--attachment", help="File attachment path or URL.")
    p_send.add_argument("-i", "--agent-id", help="Sender agent ID.")

    # broadcast
    p_bcast = subparsers.add_parser("broadcast", help="Broadcast message across connected channels.")
    p_bcast.add_argument("message", help="Broadcast message content.")
    p_bcast.add_argument("-c", "--channels", help="Comma-separated subset of channels.")
    p_bcast.add_argument("-p", "--priority", default="NORMAL", choices=["LOW", "NORMAL", "HIGH", "CRITICAL"], help="Priority level.")
    p_bcast.add_argument("-i", "--agent-id", help="Broadcaster agent ID.")

    # channels
    p_chan = subparsers.add_parser("channels", help="List channels and query live health status.")
    p_chan.add_argument("-t", "--test", action="store_true", help="Perform live latency ping check.")

    # claim
    p_claim = subparsers.add_parser("claim", help="Claim or release a project lock.")
    p_claim.add_argument("project_id", nargs="?", default="default-project", help="Project / lock ID.")
    p_claim.add_argument("-a", "--agent-id", help="Requesting agent ID.")
    p_claim.add_argument("--release", action="store_true", help="Release held lock.")
    p_claim.add_argument("--handoff-to", help="Handoff lock to another agent.")
    p_claim.add_argument("--ttl", type=float, default=300.0, help="Lock Time-To-Live in seconds.")
    p_claim.add_argument("--status", action="store_true", help="Check lock status.")
    p_claim.add_argument("--list", action="store_true", help="List all active locks.")

    # consensus
    p_cons = subparsers.add_parser("consensus", help="Run 3-way dialectic consensus.")
    p_cons.add_argument("proposal", help="Proposal or thesis to evaluate.")
    p_cons.add_argument("--proponent", default="Proponent-Agent", help="Proponent agent role.")
    p_cons.add_argument("--skeptic", default="Skeptic-Agent", help="Skeptic agent role.")
    p_cons.add_argument("--arbitrator", default="Arbitrator-Agent", help="Arbitrator agent role.")
    p_cons.add_argument("--mode", default="dialectic", choices=["dialectic", "majority", "supermajority"], help="Resolution mode.")
    p_cons.add_argument("--rounds", type=int, default=3, help="Deliberation rounds.")

    # pulse
    p_pulse = subparsers.add_parser("pulse", help="Record agent heartbeat pulse.")
    p_pulse.add_argument("agent_id", nargs="?", default="agent-cli", help="Agent identifier.")
    p_pulse.add_argument("--interval", type=float, default=60.0, help="Pulse interval in seconds.")
    p_pulse.add_argument("--timeout", type=float, help="Dead-man switch timeout in seconds.")
    p_pulse.add_argument("--task", help="Current task metadata description.")
    p_pulse.add_argument("--status", action="store_true", help="Query agent pulse status.")
    p_pulse.add_argument("--list", action="store_true", help="List all active pulses.")

    # watchdog
    p_watch = subparsers.add_parser("watchdog", help="Start continuous heartbeat watchdog daemon.")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Scan interval in seconds.")
    p_watch.add_argument("--storage", help="Path to heartbeats.json file.")
    p_watch.add_argument("--alerts-log", help="Path to watchdog_alerts.jsonl.")
    p_watch.add_argument("--webhook", help="Webhook URL for dead-man alerts.")

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch Bridge Studio Web UI (design influenced by Material 3).")
    p_serve.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1).")
    p_serve.add_argument("-p", "--port", type=int, default=8788, help="Port to bind (default: 8788).")
    p_serve.add_argument("--public-dir", help="Path to static web assets directory.")

    # mcp
    subparsers.add_parser("mcp", help="Run Model Context Protocol (MCP) server on stdio.")

    # diagnostics / doctor / platform
    subparsers.add_parser("diagnostics", help="Run system diagnostics.")
    subparsers.add_parser("doctor", help="Alias for diagnostics.")
    subparsers.add_parser("platform", help="Alias for diagnostics.")

    # test
    subparsers.add_parser("test", help="Execute internal self-verification tests.")

    return parser


def main() -> int:
    """CLI Main Entry Point."""
    parser = build_parser()
    args = parser.parse_args()

    # Configure color support
    if args.no_color or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        Color.set_enabled(False)

    if not args.subcommand:
        print_banner(no_banner=args.json)
        parser.print_help()
        return 0

    if args.subcommand not in ("mcp",) and not args.json:
        print_banner()

    if args.subcommand == "send":
        return handle_send(args)
    elif args.subcommand == "broadcast":
        return handle_broadcast(args)
    elif args.subcommand == "channels":
        return handle_channels(args)
    elif args.subcommand == "claim":
        return handle_claim(args)
    elif args.subcommand == "consensus":
        return handle_consensus(args)
    elif args.subcommand == "pulse":
        return handle_pulse(args)
    elif args.subcommand == "watchdog":
        return handle_watchdog(args)
    elif args.subcommand == "serve":
        return handle_serve(args)
    elif args.subcommand == "mcp":
        return handle_mcp(args)
    elif args.subcommand in ("diagnostics", "doctor", "platform"):
        return handle_diagnostics(args)
    elif args.subcommand == "test":
        return handle_test(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
