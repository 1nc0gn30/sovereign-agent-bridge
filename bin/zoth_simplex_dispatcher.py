#!/usr/bin/env python3
"""Zoth Sovereign SimpleX Chat Dispatcher & Telemetry Bridge.

Provides a persistent bridge between SimpleX Chat (privacy-first, metadata-free
end-to-end encryption) and the local Zoth / Antigravity / Azoth AI agent stack.

Key Capabilities:
- Instant message acknowledgement (< 100ms) upon inbound message arrival
- Auto-accepts incoming SimpleX contact requests and greets new contacts
- Real-time hardware telemetry: CPU package temps, RAM usage, load, service health
- Direct host bash execution via `$ <cmd>` or `/cmd <cmd>`
- AI agent routing (@agy, @azoth, @hermes, @codex, @claude, or default)
- 30-minute reasoning allowance with 60-second live progress tickers
- Periodic 5-minute telemetry heartbeats to connected contacts
- Generates SimpleX invite links and QR codes for zero-friction mobile pairing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import websockets

# Base Directories
HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = HOME / ".gemini" / "antigravity-cli" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SIMPLEX_DIR = HOME / ".simplex"
SIMPLEX_DIR.mkdir(parents=True, exist_ok=True)

WORKSPACE_DIR = HOME / "NullAITech"
AZOTH_DIR = WORKSPACE_DIR / "azoth-local-agent"
AGY_BIN = shutil.which("agy") or str(HOME / ".local" / "bin" / "agy")
HERMES_BIN = shutil.which("hermes") or str(HOME / ".local" / "bin" / "hermes")
SIMPLEX_BIN = shutil.which("simplex-chat") or "/usr/local/bin/simplex-chat"
QRENCODE_BIN = shutil.which("qrencode") or "/usr/bin/qrencode"

SIMPLEX_PORT = int(os.getenv("SIMPLEX_PORT", "5225"))
SIMPLEX_HTTP_PORT = int(os.getenv("SIMPLEX_HTTP_PORT", "8767"))
SIMPLEX_WS_URL = f"ws://127.0.0.1:{SIMPLEX_PORT}"
SIMPLEX_DB_PREFIX = str(SIMPLEX_DIR / "zoth_agent")
HEARTBEAT_ENABLED = os.getenv("SIMPLEX_HEARTBEAT", "0") == "1"

LOG_FILE = LOG_DIR / "simplex_bridge.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (SimpleX) %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("zoth_simplex_dispatcher")

# Voice Calling & Kokoro Engine Integration (:8114 / :9394)
VOICE_CALL_PORT = int(os.getenv("VOICE_CALL_PORT", "8114"))
DEFAULT_VOICE = "azoth"
AVAILABLE_VOICES = {
    "azoth": "Azoth (Strategic, Resonant Baritone)",
    "ghostbyte": "Ghostbyte (Cybernetic, Decisive Imperator)",
    "athena": "Athena (Sharp, Articulate Classical)",
    "mercury": "Mercury (Fast, Agile Inquisitive)",
    "kitsune": "Kitsune (Warm, Natural Conversational)",
    "chronos": "Chronos (Deep, Measured Stoic)",
}


def create_live_call_link(channel: str = "simplex", voice: str = "azoth") -> Tuple[Optional[str], Optional[str]]:
    """Request 1-click live call token from agent-voice-call (:8114)."""
    try:
        url = f"http://127.0.0.1:{VOICE_CALL_PORT}/api/call/create"
        payload = json.dumps({"channel": channel, "voice": voice}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("call_url"), data.get("session_id")
    except Exception as e:
        logger.warning("Failed to create live call link for SimpleX: %s", e)
        return None, None


# ---------------------------------------------------------------------------
# Hardware & System Telemetry Helpers
# ---------------------------------------------------------------------------

def get_cpu_temp() -> Optional[float]:
    """Retrieve CPU package temperature from Linux thermal zones."""
    try:
        thermal_base = Path("/sys/class/thermal")
        for zone in sorted(thermal_base.glob("thermal_zone*")):
            type_file = zone / "type"
            temp_file = zone / "temp"
            if type_file.exists() and temp_file.exists():
                z_type = type_file.read_text().strip()
                if "x86_pkg_temp" in z_type or "cpu" in z_type.lower() or "core" in z_type.lower():
                    raw = float(temp_file.read_text().strip())
                    return round(raw / 1000.0, 1)

        zone0 = thermal_base / "thermal_zone0" / "temp"
        if zone0.exists():
            return round(float(zone0.read_text().strip()) / 1000.0, 1)
    except Exception as e:
        logger.debug("Failed reading thermal sensors: %s", e)
    return None


def get_ram_info() -> Dict[str, Any]:
    """Retrieve system RAM usage from /proc/meminfo."""
    try:
        meminfo: Dict[str, float] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    meminfo[k] = float(v)

        total_kb = meminfo.get("MemTotal", 0.0)
        avail_kb = meminfo.get("MemAvailable", 0.0)
        used_kb = total_kb - avail_kb

        total_gb = round(total_kb / (1024.0 * 1024.0), 2)
        used_gb = round(used_kb / (1024.0 * 1024.0), 2)
        pct = round((used_kb / total_kb) * 100.0, 1) if total_kb > 0 else 0.0

        return {"total_gb": total_gb, "used_gb": used_gb, "percent": pct}
    except Exception as e:
        logger.debug("Failed reading RAM info: %s", e)
        return {"total_gb": 0.0, "used_gb": 0.0, "percent": 0.0}


def get_load_avg() -> str:
    """Return 1m, 5m, 15m system load average."""
    try:
        loads = os.getloadavg()
        return f"{loads[0]:.2f}, {loads[1]:.2f}, {loads[2]:.2f}"
    except Exception:
        return "N/A"


def get_services_status() -> Dict[str, str]:
    """Check health of key system and AI daemons."""
    services = {
        "zoth-signal-bridge": "unknown",
        "zoth-simplex-bridge": "active",
        "zoth-studio (port 3000)": "offline",
        "azoth-cockpit (port 8790)": "offline",
    }

    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "zoth-signal-bridge.service"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        services["zoth-signal-bridge"] = res.stdout.strip()
    except Exception:
        pass

    import socket

    for name, port in [("zoth-studio (port 3000)", 3000), ("azoth-cockpit (port 8790)", 8790)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            services[name] = "active" if s.connect_ex(("127.0.0.1", port)) == 0 else "offline"

    return services


def format_system_telemetry(header: str = "Hardware & Telemetry Pulse") -> str:
    """Build a clean telemetry report string."""
    temp = get_cpu_temp()
    temp_str = f"{temp}°C" if temp is not None else "Sensor Unavailable"
    ram = get_ram_info()
    loads = get_load_avg()
    svcs = get_services_status()

    svc_lines = "\n".join([f"  • {k}: {v}" for k, v in svcs.items()])

    return (
        f"📊 [{header}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌡️ CPU Temp:    {temp_str}\n"
        f"🧠 System RAM:  {ram['used_gb']} / {ram['total_gb']} GB ({ram['percent']}%)\n"
        f"⚡ Load (1/5/15): {loads}\n"
        f"⚙️ Services:\n"
        f"{svc_lines}\n"
        f"🕒 UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )


# ---------------------------------------------------------------------------
# Shell Command & Agent Execution Engine
# ---------------------------------------------------------------------------

def execute_bash_command(cmd_str: str, timeout: int = 600) -> str:
    """Execute arbitrary bash command on host with detailed execution metadata."""
    work_dir = WORKSPACE_DIR if WORKSPACE_DIR.exists() else HOME
    start_t = time.time()

    try:
        proc = subprocess.run(
            cmd_str,
            shell=True,
            executable="/bin/bash",
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = round(time.time() - start_t, 2)
        out = proc.stdout.strip()
        err = proc.stderr.strip()

        combined = out
        if err:
            combined = f"{combined}\n[STDERR]: {err}" if combined else f"[STDERR]: {err}"

        if not combined:
            combined = "(No output produced)"

        return (
            f"💻 [EXEC: bash · {elapsed}s · exit {proc.returncode}]\n"
            f"$ {cmd_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{combined}"
        )
    except subprocess.TimeoutExpired:
        return f"⏱️ Command timed out after {timeout} seconds:\n$ {cmd_str}"
    except Exception as e:
        return f"⚠️ Execution error: {e}"


def execute_agent_prompt(
    target_agent: str,
    prompt: str,
    on_ticker: Optional[Any] = None,
) -> str:
    """Route prompt to Antigravity, Azoth, or configured engine with live tickers."""
    target = target_agent.lower().strip()
    logger.info("Routing prompt to agent '%s': %s", target, prompt[:80])

    stop_progress = threading.Event()
    start_time = time.time()

    def progress_ticker():
        while not stop_progress.wait(60.0):
            elapsed = int(time.time() - start_time)
            elapsed_min = round(elapsed / 60.0, 1)
            if on_ticker:
                try:
                    on_ticker(
                        f"⏳ [{target.upper()} Working · {elapsed_min}m elapsed]\n"
                        f"Deep reasoning and tool execution in progress..."
                    )
                except Exception:
                    pass

    ticker_thread = threading.Thread(target=progress_ticker, daemon=True)
    ticker_thread.start()

    try:
        if target in ("agy", "antigravity"):
            cmd = [AGY_BIN, "-p", prompt, "--dangerously-skip-permissions"]
            proc = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True, timeout=1800)
            output = proc.stdout.strip()
            if not output and proc.stderr:
                output = f"[AGY Notice]: {proc.stderr.strip()}"
            return output or "[Antigravity completed with no output]"

        elif target in ("azoth", "default"):
            cmd = [
                "/opt/zothos-ai-env/bin/python3",
                str(AZOTH_DIR / "agent.py"),
                "-q",
                prompt,
                "--engine",
                "agy",
                "--timeout",
                "1800",
            ]
            proc = subprocess.run(cmd, cwd=str(AZOTH_DIR), capture_output=True, text=True, timeout=1800)
            output = proc.stdout.strip()
            if not output and proc.stderr:
                output = f"[Azoth Notice]: {proc.stderr.strip()}"
            return output or "[Azoth completed with no output]"

        elif target in ("hermes", "codex", "claude", "gemini", "ollama"):
            cmd = [
                "/opt/zothos-ai-env/bin/python3",
                str(AZOTH_DIR / "agent.py"),
                "-q",
                prompt,
                "--engine",
                target,
                "--timeout",
                "1800",
            ]
            proc = subprocess.run(cmd, cwd=str(AZOTH_DIR), capture_output=True, text=True, timeout=1800)
            output = proc.stdout.strip()
            if not output and proc.stderr:
                output = f"[{target.upper()} Notice]: {proc.stderr.strip()}"
            return output or f"[{target.upper()} completed with no output]"

        else:
            return execute_agent_prompt("azoth", prompt, on_ticker=on_ticker)

    except subprocess.TimeoutExpired:
        return "⏱️ Agent reasoning reached the 30-minute safety threshold."
    except Exception as e:
        logger.error("Agent execution error: %s", e, exc_info=True)
        return f"⚠️ Execution error: {e}"
    finally:
        stop_progress.set()


# ---------------------------------------------------------------------------
# SimpleX Chat Daemon & Client Manager
# ---------------------------------------------------------------------------

class SimpleXBridge:
    """Manages the SimpleX Chat daemon process and WebSocket client connection."""

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.ws = None
        self.contact_id_to_name: Dict[str, str] = {}
        self.connected_contacts: Set[str] = set()
        self.outbound_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.message_history: List[Dict[str, Any]] = []
        self.running = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._load_known_contacts()
        self._start_http_api()

    def _record_message(self, direction: str, contact: str, text: str):
        entry = {
            "id": int(time.time() * 1000),
            "direction": direction,
            "contact": contact,
            "text": text,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.message_history.append(entry)
        if len(self.message_history) > 200:
            self.message_history = self.message_history[-200:]

    def _start_http_api(self):
        """Start lightweight local HTTP REST bridge on 127.0.0.1:8767 for ZothOS & Sovereign Bridge."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        bridge_ref = self

        class SimpleXHTTPHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send_json(self, code: int, payload: Dict[str, Any]):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self._send_json(200, {"ok": True})

            def do_GET(self):
                invite_file = SIMPLEX_DIR / "invite_url.txt"
                invite_url = invite_file.read_text().strip() if invite_file.exists() else ""
                if self.path in ("/health", "/api/health", "/api/simplex/status"):
                    self._send_json(200, {
                        "status": "ok",
                        "service": "zoth-simplex-bridge",
                        "ws_url": SIMPLEX_WS_URL,
                        "ws_connected": bridge_ref.ws is not None,
                        "contacts": sorted(list(bridge_ref.connected_contacts)),
                        "invite_url": invite_url,
                        "qr_path": str(SIMPLEX_DIR / "simplex_invite_qr.png"),
                        "heartbeat_enabled": HEARTBEAT_ENABLED,
                    })
                elif self.path.startswith("/api/simplex/invite"):
                    self._send_json(200, {
                        "status": "ok",
                        "invite_url": invite_url,
                        "qr_path": str(SIMPLEX_DIR / "simplex_invite_qr.png"),
                    })
                elif self.path.startswith("/api/simplex/messages"):
                    self._send_json(200, {
                        "status": "ok",
                        "contacts": sorted(list(bridge_ref.connected_contacts)),
                        "messages": bridge_ref.message_history[-50:],
                    })
                else:
                    self._send_json(404, {"error": "Not found"})

            def do_POST(self):
                if self.path in ("/api/simplex/send", "/api/send"):
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                        body = json.loads(raw)
                        text = str(body.get("text") or body.get("message") or "").strip()
                        recipient = str(body.get("recipient") or "").strip().lstrip("@")
                        if not recipient:
                            contacts = sorted(list(bridge_ref.connected_contacts))
                            recipient = contacts[0] if contacts else "neal"
                        if not text:
                            self._send_json(400, {"error": "Message text is required"})
                            return
                        bridge_ref.send_msg_sync(recipient, text)
                        self._send_json(200, {
                            "status": "DELIVERED",
                            "channel": "simplex",
                            "recipient": f"@{recipient}",
                            "length": len(text),
                        })
                    except Exception as e:
                        self._send_json(500, {"error": str(e)})
                else:
                    self._send_json(404, {"error": "Not found"})

        def _serve():
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", SIMPLEX_HTTP_PORT), SimpleXHTTPHandler)
                logger.info("SimpleX HTTP Bridge listening on http://127.0.0.1:%d", SIMPLEX_HTTP_PORT)
                httpd.serve_forever()
            except Exception as e:
                logger.warning("SimpleX HTTP Bridge server could not bind port %d: %s", SIMPLEX_HTTP_PORT, e)

        threading.Thread(target=_serve, daemon=True).start()

    def _clean_contacts(self):
        """Remove numeric contact IDs that duplicate named contacts."""
        mapped_names = set(self.contact_id_to_name.values())
        cleaned = set()
        for c in self.connected_contacts:
            c_str = str(c).strip().lstrip("@")
            if not c_str:
                continue
            if c_str.isdigit() and (c_str in self.contact_id_to_name or mapped_names):
                if c_str in self.contact_id_to_name:
                    cleaned.add(self.contact_id_to_name[c_str])
                continue
            cleaned.add(c_str)
        self.connected_contacts = cleaned

    def _load_known_contacts(self):
        map_file = SIMPLEX_DIR / "contact_map.json"
        if map_file.exists():
            try:
                self.contact_id_to_name = json.loads(map_file.read_text())
            except Exception:
                pass
        contacts_file = SIMPLEX_DIR / "known_contacts.json"
        if contacts_file.exists():
            try:
                data = json.loads(contacts_file.read_text())
                self.connected_contacts = set(data)
            except Exception:
                pass
        self._clean_contacts()
        self._save_known_contacts()

    def _save_known_contacts(self):
        try:
            self._clean_contacts()
            contacts_file = SIMPLEX_DIR / "known_contacts.json"
            contacts_file.write_text(json.dumps(sorted(list(self.connected_contacts)), indent=2))
            map_file = SIMPLEX_DIR / "contact_map.json"
            map_file.write_text(json.dumps(self.contact_id_to_name, indent=2))
        except Exception:
            pass

    def ensure_daemon(self):
        """Ensure simplex-chat daemon is running on SIMPLEX_PORT."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", SIMPLEX_PORT)) == 0:
                logger.info("SimpleX daemon already listening on port %d", SIMPLEX_PORT)
                return

        logger.info("Spawning SimpleX daemon on port %d...", SIMPLEX_PORT)
        cmd = [
            SIMPLEX_BIN,
            "-d",
            SIMPLEX_DB_PREFIX,
            "-p",
            str(SIMPLEX_PORT),
            "--user-display-name",
            "Zoth Studio AI",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2.0)

    async def generate_invite(self) -> Dict[str, str]:
        """Request a one-time connection invite link via SimpleX daemon."""
        fut = asyncio.get_running_loop().create_future()
        corr_id = f"invite-{int(time.time()*1000)}"

        # Send /connect command over WebSocket
        msg = {"corrId": corr_id, "cmd": "/connect"}
        await self.ws.send(json.dumps(msg))

        return {
            "corrId": corr_id,
            "status": "requested",
        }

    async def send_msg(self, recipient: str, text: str):
        """Queue message for delivery to SimpleX contact."""
        await self.outbound_queue.put((recipient, text))

    def send_msg_sync(self, recipient: str, text: str):
        """Thread-safe sync enqueue from HTTP or background threads."""
        try:
            loop = self.loop or asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.outbound_queue.put_nowait, (recipient, text))
        except Exception as e:
            logger.debug("send_msg_sync error: %s", e)

    async def start(self):
        """Main lifecycle loop: starts daemon and maintains WebSocket connection."""
        self.running = True
        self.loop = asyncio.get_running_loop()
        self.ensure_daemon()

        while self.running:
            try:
                logger.info("Connecting to SimpleX WebSocket at %s...", SIMPLEX_WS_URL)
                async with websockets.connect(SIMPLEX_WS_URL, max_size=10_000_000) as ws:
                    self.ws = ws
                    logger.info("Connected to SimpleX Chat daemon!")

                    # Request active connection invite and contacts on startup
                    try:
                        await ws.send(json.dumps({"corrId": "init-connect", "cmd": "/connect"}))
                        await ws.send(json.dumps({"corrId": "init-contacts", "cmd": "/contacts"}))
                    except Exception as e:
                        logger.debug("Failed requesting init data: %s", e)

                    # Start background worker tasks
                    inbound_task = asyncio.create_task(self._inbound_listener())
                    outbound_task = asyncio.create_task(self._outbound_worker())
                    heartbeat_task = asyncio.create_task(self._heartbeat_worker())

                    done, pending = await asyncio.wait(
                        [inbound_task, outbound_task, heartbeat_task],
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()

            except Exception as e:
                logger.warning("SimpleX WebSocket disconnected: %s. Reconnecting in 3s...", e)
                await asyncio.sleep(3.0)

    async def _outbound_worker(self):
        """Consume queued outgoing messages and transmit via WebSocket."""
        while self.running:
            recipient, text = await self.outbound_queue.get()
            try:
                # If numeric id was passed, resolve to name
                target = str(recipient).strip().lstrip("@")
                if target.isdigit() and target in self.contact_id_to_name:
                    target = self.contact_id_to_name[target]
                target = f"@{target}"
                cmd = f"{target} {text}"
                corr_id = f"snd-{int(time.time()*1000)}"
                payload = json.dumps({"corrId": corr_id, "cmd": cmd})
                if self.ws:
                    await self.ws.send(payload)
                    self._record_message("outbound", target, text)
                    logger.info("Sent message to SimpleX contact %s (%d chars)", target, len(text))
            except Exception as e:
                logger.error("Failed sending SimpleX message: %s", e)
            finally:
                self.outbound_queue.task_done()

    async def _heartbeat_worker(self):
        """Emit periodic 5-minute telemetry pulses to active contacts when enabled."""
        await asyncio.sleep(15.0)  # Wait for startup settlement
        while self.running:
            try:
                if HEARTBEAT_ENABLED and self.connected_contacts:
                    self._clean_contacts()
                    pulse_msg = format_system_telemetry(header="SimpleX Heartbeat · 5m pulse")
                    for contact in sorted(list(self.connected_contacts)):
                        await self.send_msg(contact, pulse_msg)
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

            await asyncio.sleep(300.0)

    async def _inbound_listener(self):
        """Receive and route events from SimpleX daemon."""
        while self.running and self.ws:
            raw = await self.ws.recv()
            try:
                data = json.loads(raw)
            except Exception:
                continue

            resp = data.get("resp") if isinstance(data.get("resp"), dict) else data
            resp_type = resp.get("type") or data.get("type", "")

            # 1. Capture Connection Invites
            if resp_type == "invitation":
                conn_link = resp.get("connLinkInvitation", {})
                short_link = conn_link.get("connShortLink", "")
                full_link = conn_link.get("connFullLink", "")
                link_to_use = short_link or full_link
                if link_to_use:
                    invite_file = SIMPLEX_DIR / "invite_url.txt"
                    invite_file.write_text(link_to_use)
                    logger.info("SimpleX Pairing Invite Created: %s", link_to_use)

                    # Generate QR Code image
                    qr_file = SIMPLEX_DIR / "simplex_invite_qr.png"
                    if QRENCODE_BIN and shutil.which(QRENCODE_BIN):
                        try:
                            subprocess.run(
                                [QRENCODE_BIN, "-o", str(qr_file), "-s", "8", link_to_use],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            logger.info("Saved SimpleX Pairing QR code to %s", qr_file)
                        except Exception:
                            pass

            # Contacts List sync + Auto Ratchet Sync check
            elif resp_type == "contactsList":
                contacts = resp.get("contacts", []) or []
                for c in contacts:
                    cid = str(c.get("contactId", ""))
                    name = c.get("localDisplayName") or c.get("profile", {}).get("displayName") or ""
                    if name:
                        self.contact_id_to_name[cid] = name
                        self.connected_contacts.add(name)
                    conn_stats = (c.get("activeConn") or {}).get("connectionStats_", {}) or {}
                    ratchet_state = conn_stats.get("ratchetSyncState", "")
                    if name and ratchet_state in ("required", "allowed"):
                        logger.info("Contact @%s ratchetSyncState=%s -> issuing /sync @%s", name, ratchet_state, name)
                        try:
                            await self.ws.send(json.dumps({"corrId": f"sync-{cid}", "cmd": f"/sync @{name}"}))
                        except Exception:
                            pass
                self._save_known_contacts()
                logger.info("Loaded SimpleX contacts: %s", list(self.connected_contacts))

            elif resp_type == "chatCmdError":
                err = resp.get("chatError", {})
                err_json = json.dumps(err)
                logger.warning("SimpleX daemon command error: %s", err_json)
                if "send prohibited" in err_json or "ratchetSync" in err_json:
                    for contact in sorted(list(self.connected_contacts)):
                        try:
                            logger.info("Auto-healing ratchet sync for @%s", contact)
                            await self.ws.send(json.dumps({"corrId": f"autosync-{int(time.time())}", "cmd": f"/sync @{contact}"}))
                        except Exception:
                            pass

            # 2. Auto-Accept Contact Requests
            elif resp_type == "contactRequest":
                contact_req = resp.get("contactRequest", {}) or {}
                req_id = contact_req.get("contactRequestId")
                if req_id is not None:
                    logger.info("Auto-accepting SimpleX contact request ID %s", req_id)
                    await self.ws.send(json.dumps({"corrId": f"acc-{req_id}", "cmd": f"/accept {req_id}"}))

            # 3. Handle Contact Connected
            elif resp_type in ("contactConnected", "ContactConnected"):
                contact = resp.get("contact", {})
                contact_id = str(contact.get("contactId", ""))
                alias = (
                    contact.get("localDisplayName")
                    or contact.get("localAlias")
                    or contact.get("profile", {}).get("displayName")
                    or f"contact_{contact_id}"
                )
                if contact_id and alias:
                    self.contact_id_to_name[contact_id] = alias
                    self.connected_contacts.add(alias)
                    self._save_known_contacts()
                    logger.info("New SimpleX contact established: %s (%s)", alias, contact_id)

                    welcome_msg = (
                        f"🚀 [Zoth SimpleX Bridge · Connected]\n"
                        f"Welcome, {alias}! End-to-end encrypted connection active.\n\n"
                        f"{format_system_telemetry('Initial System Vitals')}\n\n"
                        f"Available Commands:\n"
                        f"  • /sys, temps, ram, cpu — Hardware telemetry\n"
                        f"  • /doctor — System health check\n"
                        f"  • $ <cmd> — Execute host bash command\n"
                        f"  • @agy, @azoth, @hermes — Route prompts to agents\n"
                        f"  • /invite — Generate fresh connection invite"
                    )
                    await self.send_msg(alias, welcome_msg)

            # 4. Handle Incoming Messages (newChatItems / newChatItem)
            elif resp_type in ("newChatItems", "newChatItem"):
                items = resp.get("chatItems", []) if resp_type == "newChatItems" else [resp]
                if not isinstance(items, list):
                    items = [items]

                for item in items:
                    await self._process_chat_item(item)

    async def _process_chat_item(self, item: dict):
        """Extract sender, text, and trigger execution."""
        chat_info = item.get("chatInfo", {}) or {}
        chat_item = item.get("chatItem", {}) or {}

        # Filter out outgoing echoes
        chat_dir = chat_item.get("chatDir", {})
        dir_type = chat_dir.get("type", "") if isinstance(chat_dir, dict) else ""
        if dir_type in ("directSnd", "groupSnd"):
            return

        content = chat_item.get("content", {}) or {}
        if content.get("type") != "rcvMsgContent":
            return

        msg_content = content.get("msgContent", {}) or {}
        text = msg_content.get("text", "").strip()
        if not text:
            return

        # Determine sender contact identifier
        contact = chat_info.get("contact", {}) or {}
        contact_id = str(contact.get("contactId", ""))
        sender_name = (
            contact.get("localDisplayName")
            or contact.get("profile", {}).get("displayName")
            or self.contact_id_to_name.get(contact_id)
            or f"contact_{contact_id}"
        )

        if not contact_id:
            chat_id = chat_info.get("chatId")
            if chat_id is not None:
                contact_id = str(chat_id)

        target_name = sender_name
        if contact_id and target_name:
            self.contact_id_to_name[contact_id] = target_name
            self.connected_contacts.add(target_name)
            self._save_known_contacts()

        logger.info("Received SimpleX message from %s (%s): %s", target_name, contact_id, text[:60])
        self._record_message("inbound", target_name, text)

        # ⚡ 1. INSTANT ACK (< 100ms)
        ack_text = f"⚡ [Zoth SimpleX Bridge · ACK] Processing request: \"{text[:60]}{'...' if len(text)>60 else ''}\""
        await self.send_msg(target_name, ack_text)

        # ⚡ 2. Async command / agent dispatch
        asyncio.create_task(self._dispatch_command(target_name, text))

    async def _dispatch_command(self, contact_id: str, prompt: str):
        """Parse command and execute without blocking event loop."""
        global HEARTBEAT_ENABLED
        p_lower = prompt.lower().strip()

        # Telemetry Commands
        if p_lower in ("/sys", "sys", "temps", "temp", "ram", "cpu", "status", "/status"):
            report = format_system_telemetry("Host Telemetry")
            await self.send_msg(contact_id, report)
            return

        if p_lower in ("/heartbeat on", "heartbeat on"):
            HEARTBEAT_ENABLED = True
            await self.send_msg(contact_id, "💓 [SimpleX Heartbeat ENABLED] 5-minute telemetry pulses active.")
            return

        if p_lower in ("/heartbeat off", "heartbeat off"):
            HEARTBEAT_ENABLED = False
            await self.send_msg(contact_id, "🔇 [SimpleX Heartbeat DISABLED] Only on-demand & event alerts will be sent.")
            return

        if p_lower in ("/sync", "sync"):
            if self.ws:
                await self.ws.send(json.dumps({"corrId": f"sync-{int(time.time())}", "cmd": f"/sync @{contact_id.lstrip('@')}"}))
            await self.send_msg(contact_id, f"🔄 [SimpleX Double-Ratchet Sync] Initiated for @{contact_id.lstrip('@')}.")
            return

        if p_lower.startswith("/matrix ") or p_lower.startswith("matrix "):
            matrix_msg = prompt.split(maxsplit=1)[1].strip() if " " in prompt else ""
            if matrix_msg:
                try:
                    req_data = json.dumps({"channel": "matrix", "message": matrix_msg, "agent_id": f"simplex:{contact_id}"}).encode("utf-8")
                    req = urllib.request.Request("http://127.0.0.1:8102/api/send", data=req_data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=5.0) as r:
                        res_json = json.loads(r.read().decode("utf-8"))
                    await self.send_msg(contact_id, f"🌐 [SimpleX ➔ Matrix Bridge Relayed]:\n{matrix_msg}\nStatus: {res_json.get('status', 'DELIVERED')}")
                except Exception as e:
                    await self.send_msg(contact_id, f"⚠️ Matrix relay error: {e}")
            return

        if p_lower in ("/doctor", "doctor", "/health"):
            report = format_system_telemetry("Diagnostic Health Report")
            await self.send_msg(contact_id, report)
            return

        if p_lower in ("/invite", "invite", "/connect", "connect"):
            invite_file = SIMPLEX_DIR / "invite_url.txt"
            url = invite_file.read_text().strip() if invite_file.exists() else ""
            reply = (
                f"🔗 [SimpleX One-Time Connection Link]:\n{url}\n\n"
                f"Scan or tap in SimpleX Chat app to establish connection."
            )
            await self.send_msg(contact_id, reply)
            return

        # Live Voice Call Link (/call or /call [voice])
        if p_lower == "/call" or p_lower.startswith("/call "):
            voice_arg = prompt[5:].strip().lower() if len(prompt) > 5 else DEFAULT_VOICE
            if voice_arg not in AVAILABLE_VOICES:
                voice_arg = DEFAULT_VOICE
            loop = asyncio.get_running_loop()
            call_url, session_id = await loop.run_in_executor(None, create_live_call_link, "simplex", voice_arg)
            if call_url:
                voice_label = AVAILABLE_VOICES.get(voice_arg, voice_arg.title())
                invitation = (
                    f"📞 [ZOTH LIVE VOICE CALL INVITATION]\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎙️ Voice Persona: {voice_label}\n"
                    f"⏱️ Session:       {session_id}\n\n"
                    f"🔗 1-Click Secure Call Room:\n"
                    f"{call_url}\n\n"
                    f"Tap link to speak live with your agent from your phone.\n"
                    f"• Zero app install required (WebRTC/Audio API)\n"
                    f"• Real-time two-way voice conversation\n"
                    f"• Auto-transcript & memory debrief upon hangup"
                )
                await self.send_msg(contact_id, invitation)
            else:
                await self.send_msg(contact_id, "⚠️ Voice call service (:8114) unreachable. Checking agent-voice-call...")
            return

        # Voice Personas Directory (/voices)
        if p_lower in ("/voices", "voices"):
            v_lines = [f"• {k.upper():<10} ➔ {v}" for k, v in AVAILABLE_VOICES.items()]
            reply = (
                "🎙️ AVAILABLE ZOTH VOICE PERSONAS:\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(v_lines) + "\n\n"
                "Commands:\n"
                "• /call [persona] — Start live call (e.g. /call ghostbyte)"
            )
            await self.send_msg(contact_id, reply)
            return

        if p_lower in ("/help", "help", "/start", "start"):
            help_text = (
                f"⚡ [Zoth SimpleX Bridge Reference]\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎙️ Live Voice Calling:\n"
                f"  /call [persona]    ➔ 1-Click live full-duplex phone call\n"
                f"  /voices            ➔ List available voice personas\n\n"
                f"📊 Hardware & Vitals:\n"
                f"  /sys, temps, ram, cpu, /doctor\n\n"
                f"💻 Direct Shell Execution:\n"
                f"  $ <bash command>   (e.g. $ uptime, $ free -h)\n"
                f"  /cmd <command>\n\n"
                f"🤖 Agent Routing:\n"
                f"  @agy <prompt>      ➔ Google Antigravity\n"
                f"  @azoth <prompt>    ➔ aZoth Local Agent\n"
                f"  @hermes <prompt>   ➔ Hermes Agent\n"
                f"  @codex <prompt>    ➔ OpenAI Codex\n"
                f"  @claude <prompt>   ➔ Claude\n"
                f"  <plain text>       ➔ aZoth / AGY pair programmer\n\n"
                f"🔗 SimpleX Pairing:\n"
                f"  /invite            ➔ Generate pairing link"
            )
            await self.send_msg(contact_id, help_text)
            return

        # Direct Shell Command Execution ($ <cmd> or /cmd <cmd>)
        if prompt.startswith("$ ") or prompt.startswith("/cmd "):
            cmd_body = prompt[2:].strip() if prompt.startswith("$ ") else prompt[5:].strip()
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, execute_bash_command, cmd_body, 600)
            await self.send_msg(contact_id, res)
            return

        # Agent Routing
        target_agent = "azoth"
        actual_prompt = prompt

        agent_patterns = [
            (r"^@agy\b", "agy"),
            (r"^@antigravity\b", "agy"),
            (r"^@azoth\b", "azoth"),
            (r"^@hermes\b", "hermes"),
            (r"^@codex\b", "codex"),
            (r"^@claude\b", "claude"),
            (r"^@gemini\b", "gemini"),
            (r"^@ollama\b", "ollama"),
        ]

        for pat, agent_name in agent_patterns:
            if re.search(pat, prompt, re.IGNORECASE):
                target_agent = agent_name
                actual_prompt = re.sub(pat, "", prompt, flags=re.IGNORECASE).strip()
                break

        def ticker_callback(ticker_msg: str):
            self.send_msg_sync(contact_id, ticker_msg)

        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(
            None,
            execute_agent_prompt,
            target_agent,
            actual_prompt,
            ticker_callback,
        )

        formatted_reply = f"🤖 [{target_agent.upper()}]:\n\n{reply}"
        await self.send_msg(contact_id, formatted_reply)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    logger.info("Initializing Zoth Sovereign SimpleX Chat Dispatcher...")
    bridge = SimpleXBridge()
    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        logger.info("Shutting down SimpleX Dispatcher.")
    except Exception as e:
        logger.critical("Fatal error in SimpleX Dispatcher: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
