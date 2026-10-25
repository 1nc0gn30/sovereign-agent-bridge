#!/usr/bin/env python3
"""⚡ ZOTH STUDIO — Sovereign Signal Swarm Bridge & Remote NOC
=============================================================================
Bridges Signal Messenger on your phone (+19482047987) with:
  • Immediate < 100ms ACK delivery notification
  • Periodic / on-demand heartbeats with live temps, RAM, and CPU telemetry
  • Direct arbitrary shell / command execution (/cmd, /sh, $)
  • Hardware metrics: CPU package temps, motherboard temps, NVMe disk, RAM
  • Google Antigravity CLI (agy) full reasoning agent
  • aZoth Local Agent & 21-Agent Swarm Multiplexer
  • Sovereign Agent Bridge & Zoth Studio v2 Stack Integration
=============================================================================
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Base paths & logging
PHONE_NUMBER = os.environ.get("SIGNAL_ACCOUNT", "+19482047987")
SOCKET_PATH = os.environ.get("SIGNAL_SOCKET_PATH", "/tmp/signal-cli.sock")
AGY_BIN = shutil.which("agy") or "/home/zoth/.local/bin/agy"
AZOTH_DIR = Path("/home/zoth/NullAITech/azoth-local-agent")
WORKSPACE_DIR = Path("/home/zoth")
LOG_PATH = Path("/home/zoth/.local/state/zoth-signal-dispatcher.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

MAX_CHUNK_SIZE = 3500

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("zoth_signal_dispatcher")

# Heartbeat configuration
HEARTBEAT_INTERVAL_SECONDS = 300  # 5 minutes
HEARTBEAT_ENABLED = os.environ.get("SIGNAL_HEARTBEAT", "0") == "1"
SIGNAL_HTTP_PORT = int(os.environ.get("SIGNAL_HTTP_PORT", "8768"))
SIGNAL_MESSAGE_HISTORY: List[Dict[str, Any]] = []


def record_signal_message(direction: str, recipient: str, text: str) -> None:
    SIGNAL_MESSAGE_HISTORY.append({
        "id": int(time.time() * 1000),
        "direction": direction,
        "recipient": recipient,
        "text": text,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    if len(SIGNAL_MESSAGE_HISTORY) > 200:
        del SIGNAL_MESSAGE_HISTORY[:-200]


# ---------------------------------------------------------------------------
# Hardware & Telemetry Engine
# ---------------------------------------------------------------------------

def get_cpu_temps() -> Dict[str, str]:
    """Retrieve temperature readings from Linux thermal zones."""
    temps = {}
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(f"{zone}/type", "r") as f:
                z_type = f.read().strip()
            with open(f"{zone}/temp", "r") as f:
                raw_temp = int(f.read().strip())
            celsius = round(raw_temp / 1000.0, 1)
            temps[z_type] = f"{celsius}°C"
        except Exception:
            continue
    return temps


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Test TCP port liveness."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_system_telemetry(detailed: bool = True) -> str:
    """Generate detailed host metrics: Temps, RAM, CPU, Disk, Battery, Stack."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Temperatures
    temps = get_cpu_temps()
    pkg_temp = temps.get("x86_pkg_temp", temps.get("acpitz", "N/A"))
    pch_temp = temps.get("pch_cannonlake", temps.get("SEN1", "N/A"))

    # 2. Memory
    try:
        free_out = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()
        parts = free_out[1].split()
        mem_used = int(parts[2])
        mem_total = int(parts[1])
        mem_avail = int(parts[6]) if len(parts) > 6 else mem_total - mem_used
        mem_str = f"{round(mem_used/1024, 1)}G / {round(mem_total/1024, 1)}G (Avail: {round(mem_avail/1024, 1)}G)"
    except Exception:
        mem_str = "N/A"

    # 3. CPU Load & Uptime
    try:
        with open("/proc/loadavg", "r") as f:
            load_parts = f.read().split()[:3]
        load_str = " ".join(load_parts)
    except Exception:
        load_str = "N/A"

    try:
        uptime_res = subprocess.run(["uptime", "-p"], capture_output=True, text=True)
        uptime_str = uptime_res.stdout.strip().removeprefix("up ")
    except Exception:
        uptime_str = "N/A"

    # 4. NVMe / Root Disk
    try:
        df_out = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()
        disk_parts = df_out[1].split()
        disk_str = f"{disk_parts[2]} used of {disk_parts[1]} ({disk_parts[4]})"
    except Exception:
        disk_str = "N/A"

    # 5. Battery
    bat_str = "AC Power"
    for bat in glob.glob("/sys/class/power_supply/BAT*"):
        try:
            with open(f"{bat}/capacity", "r") as f:
                cap = f.read().strip()
            with open(f"{bat}/status", "r") as f:
                stat = f.read().strip()
            bat_str = f"{cap}% ({stat})"
            break
        except Exception:
            continue

    # 6. Service Stack Health
    services = [
        ("UI Cockpit", 3000),
        ("Bridge REST", 8102),
        ("Memory Daemon", 8094),
        ("Vault Daemon", 8787),
        ("Swarm Daemon", 8790),
        ("Ollama Local", 11434),
    ]
    service_lines = []
    for sname, sport in services:
        status_icon = "🟢 UP" if check_port(sport) else "🔴 DOWN"
        service_lines.append(f"  • {sname.padEnd if hasattr(sname, 'padEnd') else sname:<14} (:{sport}) ➔ {status_icon}")

    if not detailed:
        return (
            f"💓 [Zoth Heartbeat · {now_str}]\n"
            f"• CPU: {pkg_temp} | Load: {load_str}\n"
            f"• RAM: {mem_str}\n"
            f"• Disk: {disk_str}\n"
            f"• Battery: {bat_str}\n"
            f"• Bridge Status: 🟢 ACTIVE"
        )

    return (
        f"💻 ZOTH HOST & STACK TELEMETRY:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Uptime:      {uptime_str}\n"
        f"🌡️ CPU Temp:    {pkg_temp} (PCH: {pch_temp})\n"
        f"📊 CPU Load:    {load_str} (1m, 5m, 15m)\n"
        f"🧠 Memory:      {mem_str}\n"
        f"💾 NVMe Disk:   {disk_str}\n"
        f"🔋 Battery:     {bat_str}\n\n"
        f"🏛️ SOVEREIGN DAEMONS:\n"
        + "\n".join(service_lines)
    )


# ---------------------------------------------------------------------------
# Shell / Command Execution Engine
# ---------------------------------------------------------------------------

def execute_shell_command(cmd_str: str, cwd: Optional[Path] = None, timeout: int = 60) -> str:
    """Execute arbitrary command on the host securely and return stdout/stderr."""
    work_dir = cwd or WORKSPACE_DIR
    logger.info("Executing host shell command in %s: %s", work_dir, cmd_str)
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


# ---------------------------------------------------------------------------
# Agent Routing Engine (Antigravity & Azoth)
# ---------------------------------------------------------------------------

def execute_agent_prompt(target_agent: str, prompt: str, client: Optional[SignalRpcClient] = None) -> str:
    """Route prompt to Antigravity, Azoth, or configured agent with live progress updates."""
    target = target_agent.lower().strip()
    logger.info("Routing prompt to target '%s': %s", target, prompt[:80])

    stop_progress = threading.Event()
    start_time = time.time()

    def progress_ticker():
        while not stop_progress.wait(60.0):
            elapsed = int(time.time() - start_time)
            elapsed_min = round(elapsed / 60.0, 1)
            if client:
                try:
                    progress_msg = f"⏳ [{target.upper()} Working · {elapsed_min}m elapsed]\nDeep reasoning and tool execution in progress..."
                    client.send_message(PHONE_NUMBER, progress_msg)
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
            # Route through Azoth local agent (backed by AGY engine)
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
            proc = subprocess.run(
                cmd,
                cwd=str(AZOTH_DIR),
                capture_output=True,
                text=True,
                timeout=1800,
            )
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
            proc = subprocess.run(
                cmd,
                cwd=str(AZOTH_DIR),
                capture_output=True,
                text=True,
                timeout=1800,
            )
            output = proc.stdout.strip()
            if not output and proc.stderr:
                output = f"[{target.upper()} Notice]: {proc.stderr.strip()}"
            return output or f"[{target.upper()} completed with no output]"

        else:
            return execute_agent_prompt("azoth", prompt, client=client)

    except subprocess.TimeoutExpired:
        return f"⏱️ Agent reasoning reached the 30-minute safety threshold."
    except Exception as e:
        logger.error("Agent execution error: %s", e, exc_info=True)
        return f"⚠️ Execution error: {e}"
    finally:
        stop_progress.set()


# ---------------------------------------------------------------------------
# Signal JSON-RPC Client & Message Sender
# ---------------------------------------------------------------------------

class SignalRpcClient:
    """JSON-RPC client over UNIX domain socket for signal-cli."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._lock = threading.Lock()

    def send_message(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[str]] = None,
        voice_note: bool = False,
    ) -> bool:
        """Send message via signal-cli JSON-RPC socket with chunking and voice notes."""
        if attachments:
            params: Dict[str, Any] = {
                "recipient": [recipient],
                "message": message or "",
                "attachment": attachments,
            }
            if voice_note:
                params["voiceNote"] = True
            payload = {
                "jsonrpc": "2.0",
                "method": "send",
                "params": params,
                "id": f"msg-att-{int(time.time()*1000)}",
            }
            try:
                with self._lock:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                        sock.settimeout(15.0)
                        sock.connect(self.socket_path)
                        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                        resp_data = sock.recv(4096)
                        resp = json.loads(resp_data.decode("utf-8"))
                        if "error" in resp:
                            logger.error("Signal RPC send error: %s", resp["error"])
                            return False
                        return True
            except Exception as e:
                logger.error("Failed to send attachment over Signal socket: %s", e)
                return False

        chunks = self._chunk_message(message)
        success = True
        if message:
            record_signal_message("outbound", recipient, message)

        for i, chunk in enumerate(chunks):
            chunk_text = chunk
            if len(chunks) > 1:
                chunk_text = f"[{i+1}/{len(chunks)}]\n{chunk}"

            payload = {
                "jsonrpc": "2.0",
                "method": "send",
                "params": {
                    "recipient": [recipient],
                    "message": chunk_text,
                },
                "id": f"msg-{int(time.time()*1000)}-{i}",
            }

            try:
                with self._lock:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                        sock.settimeout(15.0)
                        sock.connect(self.socket_path)
                        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                        resp_data = sock.recv(4096)
                        resp = json.loads(resp_data.decode("utf-8"))
                        if "error" in resp:
                            logger.error("Signal RPC send error: %s", resp["error"])
                            success = False
            except Exception as e:
                logger.error("Failed to send message over Signal socket: %s", e)
                success = False

        return success

    @staticmethod
    def _chunk_message(text: str) -> List[str]:
        if len(text) <= MAX_CHUNK_SIZE:
            return [text]
        chunks = []
        lines = text.splitlines(keepends=True)
        current = ""
        for line in lines:
            if len(current) + len(line) > MAX_CHUNK_SIZE:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        return chunks


# ---------------------------------------------------------------------------
# Voice & Live Call Engine Integration (:8114 / :9394)
# ---------------------------------------------------------------------------

VOICE_CALL_PORT = int(os.environ.get("VOICE_CALL_PORT", "8114"))
VOICE_MODE_ENABLED = False
ACTIVE_VOICE = "azoth"

AVAILABLE_VOICES = {
    "azoth": "Azoth (Strategic, Resonant Baritone)",
    "ghostbyte": "Ghostbyte (Cybernetic, Decisive Imperator)",
    "athena": "Athena (Sharp, Articulate Classical)",
    "mercury": "Mercury (Fast, Agile Inquisitive)",
    "kitsune": "Kitsune (Warm, Natural Conversational)",
    "chronos": "Chronos (Deep, Measured Stoic)",
}


def create_live_call_link(channel: str = "signal", voice: str = "azoth") -> Tuple[Optional[str], Optional[str]]:
    """Request 1-click live call token from agent-voice-call (:8114)."""
    try:
        url = f"http://127.0.0.1:{VOICE_CALL_PORT}/api/call/create"
        payload = json.dumps({"channel": channel, "voice": voice}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("call_url"), data.get("session_id")
    except Exception as e:
        logger.warning("Failed to create live call link: %s", e)
        return None, None


def generate_voice_audio_file(text: str, voice_name: str = "azoth") -> Optional[str]:
    """Synthesize speech file using agent_voice_call.tts_bridge."""
    try:
        if "/home/zoth/NullAITech/agent-voice-call/src" not in sys.path:
            sys.path.insert(0, "/home/zoth/NullAITech/agent-voice-call/src")
        from agent_voice_call.tts_bridge import generate_voice_file
        return generate_voice_file(text, voice_name=voice_name, out_format="ogg")
    except Exception as e:
        logger.warning("TTS speech generation failed: %s", e)
        return None


def send_voice_note_reply(client: SignalRpcClient, text: str, voice_name: str = "azoth") -> bool:
    """Generate and dispatch native Signal voice note."""
    audio_path = generate_voice_audio_file(text, voice_name=voice_name)
    if audio_path and os.path.exists(audio_path):
        return client.send_message(PHONE_NUMBER, "", attachments=[audio_path], voice_note=True)
def extract_media_attachments(text: str) -> List[str]:
    """Extract local image and media paths from markdown or plain text."""
    if not text:
        return []
    candidates = []
    for m in re.finditer(r'!\[.*?\]\((?:file://)?([^)\s]+)\)', text):
        candidates.append(m.group(1))
    for m in re.finditer(r'\[.*?\]\((?:file://)?([^)\s]+\.(?:png|jpg|jpeg|webp|gif|svg|mp4|ogg|mp3|wav|pdf))\)', text, re.IGNORECASE):
        candidates.append(m.group(1))
    for m in re.finditer(r'(\/(?:home|tmp|var)[^\s\)\'"]+\.(?:png|jpg|jpeg|webp|gif|svg|mp4|ogg|mp3|wav|pdf))', text, re.IGNORECASE):
        candidates.append(m.group(1))

    valid = []
    for c in candidates:
        cleaned = c.strip().rstrip(".,;:)'\"")
        if os.path.isfile(cleaned) and cleaned not in valid:
            valid.append(cleaned)
    return valid


# ---------------------------------------------------------------------------
# Command Parsing & Dispatcher Loop
# ---------------------------------------------------------------------------

def handle_incoming_text(text: str, client: SignalRpcClient) -> None:
    """Process incoming Signal message with immediate ACK (< 100ms) and dispatch."""
    global VOICE_MODE_ENABLED, ACTIVE_VOICE, HEARTBEAT_ENABLED
    clean = text.strip()
    if not clean:
        return
    record_signal_message("inbound", PHONE_NUMBER, clean)

    # Check for direct shell command prefix ($ or /cmd or /sh or @cmd)
    is_shell_cmd = False
    cmd_to_run = ""

    if clean.startswith("$ "):
        is_shell_cmd = True
        cmd_to_run = clean[2:].strip()
    elif clean.startswith("/cmd ") or clean.startswith("/sh ") or clean.startswith("/run "):
        is_shell_cmd = True
        cmd_to_run = clean.split(maxsplit=1)[1].strip()
    elif clean.startswith("@cmd ") or clean.startswith("@sh ") or clean.startswith("@exec "):
        is_shell_cmd = True
        cmd_to_run = clean.split(maxsplit=1)[1].strip()

    # 1. IMMEDIATE ACK (< 100ms)
    preview = clean[:70] + ("..." if len(clean) > 70 else "")
    lower = clean.lower()
    if is_shell_cmd:
        ack_text = f"⚡ [ACK] Running shell command:\n$ {cmd_to_run[:60]}"
    elif lower in ("/sys", "sys", "temps", "ram", "cpu", "/status", "status", "/doctor"):
        ack_text = "⚡ [ACK] Gathering system & hardware telemetry..."
    elif lower == "/call" or lower.startswith("/call "):
        ack_text = "⚡ [ACK] Spawning 1-click live voice call station room..."
    elif lower.startswith("/voice ") or lower.startswith("/speak "):
        ack_text = "⚡ [ACK] Synthesizing sovereign audio voice note..."
    elif lower in ("/voicemode", "/voicemode on", "/voicemode off", "/voice on", "/voice off"):
        ack_text = "⚡ [ACK] Updating voice synthesis mode..."
    elif lower in ("/voices", "voices"):
        ack_text = "⚡ [ACK] Retrieving available voice personas..."
    elif clean.startswith("@"):
        agent_tag = clean.split()[0]
        ack_text = f"⚡ [ACK] Directive received for {agent_tag.upper()}:\n\"{preview}\""
    else:
        ack_text = f"⚡ [ACK] Directive received by Azoth:\n\"{preview}\""

    # Fire immediate ACK
    client.send_message(PHONE_NUMBER, ack_text)

    # 2. EXECUTE REQUEST
    # Case A: Shell Command Execution
    if is_shell_cmd:
        reply = execute_shell_command(cmd_to_run)
        client.send_message(PHONE_NUMBER, reply)
        return

    # Case B: System Telemetry & Metrics
    if lower in ("/sys", "!sys", "sys", "/metrics", "metrics", "temps", "ram", "cpu", "temp"):
        reply = get_system_telemetry(detailed=True)
        client.send_message(PHONE_NUMBER, reply)
        return

    # Case C: Status & Diagnostics
    if lower in ("/status", "!status", "status", "ping", "/health"):
        reply = get_system_telemetry(detailed=False)
        client.send_message(PHONE_NUMBER, reply)
        return

    if lower in ("/doctor", "!doctor", "doctor"):
        reply = get_system_telemetry(detailed=True)
        client.send_message(PHONE_NUMBER, reply)
        return

    if lower in ("/heartbeat on", "heartbeat on"):
        HEARTBEAT_ENABLED = True
        client.send_message(PHONE_NUMBER, "💓 [Signal Heartbeat ENABLED] 5-minute status pulses active.")
        return

    if lower in ("/heartbeat off", "heartbeat off"):
        HEARTBEAT_ENABLED = False
        client.send_message(PHONE_NUMBER, "🔇 [Signal Heartbeat DISABLED] Only on-demand & event alerts will be sent.")
        return

    if lower.startswith("/simplex ") or lower.startswith("/matrix "):
        ch = "simplex" if lower.startswith("/simplex ") else "matrix"
        msg_body = clean.split(maxsplit=1)[1].strip() if " " in clean else ""
        if msg_body:
            try:
                req_data = json.dumps({"channel": ch, "message": msg_body, "agent_id": f"signal:{PHONE_NUMBER}"}).encode("utf-8")
                req = urllib.request.Request("http://127.0.0.1:8102/api/send", data=req_data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5.0) as r:
                    res_json = json.loads(r.read().decode("utf-8"))
                client.send_message(PHONE_NUMBER, f"🌐 [Signal ➔ {ch.upper()} Relayed]:\n{msg_body}\nStatus: {res_json.get('status', 'DELIVERED')}")
            except Exception as e:
                client.send_message(PHONE_NUMBER, f"⚠️ {ch.upper()} relay error: {e}")
        return

    # Case Voice 1: Live Voice Call Link
    if lower == "/call" or lower.startswith("/call "):
        voice_arg = clean[5:].strip().lower() if len(clean) > 5 else ACTIVE_VOICE
        if voice_arg not in AVAILABLE_VOICES:
            voice_arg = ACTIVE_VOICE
        call_url, session_id = create_live_call_link(channel="signal", voice=voice_arg)
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
            client.send_message(PHONE_NUMBER, invitation)
        else:
            client.send_message(PHONE_NUMBER, "⚠️ Voice call service (:8114) unreachable. Checking agent-voice-call...")
        return

    # Case Voice 2: Spoken Voice Note
    if lower.startswith("/voice ") or lower.startswith("/speak ") or lower.startswith("/say "):
        parts = clean.split(maxsplit=2)
        target_voice = ACTIVE_VOICE
        speech_text = ""
        if len(parts) >= 2:
            if parts[1].lower() in AVAILABLE_VOICES and len(parts) >= 3:
                target_voice = parts[1].lower()
                speech_text = parts[2].strip()
            else:
                speech_text = clean.split(maxsplit=1)[1].strip()

        if speech_text:
            sent = send_voice_note_reply(client, speech_text, voice_name=target_voice)
            if not sent:
                client.send_message(PHONE_NUMBER, f"⚠️ Voice synthesis unavailable. Text: {speech_text}")
        return

    # Case Voice 3: Voice Mode Toggle
    if lower in ("/voicemode", "/voicemode on", "/voice on", "/voice-mode on"):
        VOICE_MODE_ENABLED = True
        client.send_message(PHONE_NUMBER, "🎙️ [VOICE MODE ACTIVE] The agent will now speak back all responses as voice notes.")
        return

    if lower in ("/voicemode off", "/voice off", "/voice-mode off"):
        VOICE_MODE_ENABLED = False
        client.send_message(PHONE_NUMBER, "🔇 [VOICE MODE DISABLED] Responses reverted to text-only mode.")
        return

    # Case Voice 4: Voices Directory
    if lower in ("/voices", "!voices", "voices"):
        v_lines = [f"• {k.upper():<10} ➔ {v}" for k, v in AVAILABLE_VOICES.items()]
        reply = (
            "🎙️ AVAILABLE ZOTH VOICE PERSONAS:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(v_lines) + "\n\n"
            "Commands:\n"
            "• /call [persona] — Start live call with persona (e.g. /call athena)\n"
            "• /voice [persona] <text> — Send spoken voice note\n"
            "• /voicemode on|off — Toggle continuous voice replies"
        )
        client.send_message(PHONE_NUMBER, reply)
        return

    # Case D: Help Menu
    if lower in ("/help", "!help", "help", "/?"):
        reply = (
            "⚡ ZOTH SOVEREIGN SIGNAL BRIDGE COMMANDS:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎙️ LIVE VOICE & CALLING COMMANDS:\n"
            "• /call [persona] — 1-Click live full-duplex phone call\n"
            "• /voice <text> — Synthesize & send audio voice note\n"
            "• /voices — List 6 available voice personas\n"
            "• /voicemode on|off — Toggle automatic voice responses\n\n"
            "💻 SYSTEM & HARDWARE COMMANDS:\n"
            "• /sys or temps — CPU temps, RAM, disk, battery\n"
            "• /status — Compact liveness heartbeat\n"
            "• $ <command> — Run any host bash command (e.g. $ uptime)\n"
            "• /cmd <command> — Run arbitrary shell command\n\n"
            "🤖 AI AGENTS & COMPANIONS:\n"
            "• @agy <prompt> — Antigravity deep reasoning CLI\n"
            "• @azoth <prompt> — Azoth Autonomous Local Agent\n"
            "• @hermes <prompt> — Hermes Agent CLI\n"
            "• @codex <prompt> — OpenAI Codex CLI\n"
            "• @claude <prompt> — Claude Code CLI\n"
            "• Plain text — Automatically routed to Azoth / Antigravity"
        )
        client.send_message(PHONE_NUMBER, reply)
        return

    # Case E: Agent Prompting
    match = re.match(r"^@([a-zA-Z0-9_\-]+)\s*(.*)", clean, re.DOTALL)
    if match:
        target = match.group(1).lower()
        prompt = match.group(2).strip()
    else:
        target = "azoth"
        prompt = clean

    reply = execute_agent_prompt(target, prompt, client=client)
    attachments = extract_media_attachments(reply)
    if attachments:
        logger.info("Auto-extracted %d media attachment(s) in agent reply: %s", len(attachments), attachments)
        if len(reply) > 1000:
            client.send_message(PHONE_NUMBER, "", attachments=attachments)
            client.send_message(PHONE_NUMBER, reply)
        else:
            client.send_message(PHONE_NUMBER, reply, attachments=attachments)
    else:
        client.send_message(PHONE_NUMBER, reply)
    if VOICE_MODE_ENABLED and reply:
        send_voice_note_reply(client, reply[:300], voice_name=ACTIVE_VOICE)


def handle_incoming_voice_note(attachment: Dict[str, Any], caption: str, client: SignalRpcClient) -> None:
    """Handle incoming spoken voice note from Neal's phone."""
    att_id = attachment.get("id", "audio")
    mime = attachment.get("contentType", "audio/ogg")
    logger.info("Received audio voice note from Signal: ID=%s, mime=%s", att_id, mime)

    # 1. Immediate ACK
    client.send_message(PHONE_NUMBER, "🎙️ [ACK] Voice transmission received from Neal. Spawning live voice call link...")

    # 2. Generate live call invitation link
    call_url, session_id = create_live_call_link(channel="signal", voice=ACTIVE_VOICE)
    if call_url:
        invite_msg = (
            f"📞 [ZOTH LIVE CALL READY]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎙️ Voice Persona: {ACTIVE_VOICE.title()}\n"
            f"🔗 Tap link to talk live:\n{call_url}"
        )
        client.send_message(PHONE_NUMBER, invite_msg)

    # 3. Synthesize spoken confirmation back as native voice note
    send_voice_note_reply(
        client,
        f"Voice note received, Neal. Azoth standing by. Tap the link to speak live.",
        voice_name=ACTIVE_VOICE,
    )


# ---------------------------------------------------------------------------
# Background Heartbeat Daemon
# ---------------------------------------------------------------------------

def run_heartbeat_loop(client: SignalRpcClient) -> None:
    """Send periodic 5-minute status heartbeat to Signal."""
    logger.info("Heartbeat thread initialized (interval: %ds).", HEARTBEAT_INTERVAL_SECONDS)
    
    # Send initial connection heartbeat 3 seconds after boot
    time.sleep(3.0)
    try:
        init_ping = (
            "💓 [Zoth Sovereign Bridge Online]\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Host: zoth-pill (Parrot OS / Debian)\n"
            f"• Account: {PHONE_NUMBER}\n"
            f"• Status: 🟢 ACTIVE & LISTENING\n"
            f"• Direct Shell: Enabled ($ <cmd> or /cmd <cmd>)\n"
            f"• Telemetry: Enabled (/sys or temps)\n"
            f"• AI Pair: Antigravity (agy) & aZoth ready."
        )
        client.send_message(PHONE_NUMBER, init_ping)
        logger.info("Sent startup heartbeat to Signal.")
    except Exception as e:
        logger.warning("Failed to send startup heartbeat: %s", e)

    while True:
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)
        if not HEARTBEAT_ENABLED:
            continue
        try:
            pulse_msg = get_system_telemetry(detailed=False)
            client.send_message(PHONE_NUMBER, pulse_msg)
            logger.info("Periodic 5-minute heartbeat sent to Signal.")
        except Exception as e:
            logger.warning("Periodic heartbeat failed: %s", e)


# ---------------------------------------------------------------------------
# Main Daemon Lifecycle
# ---------------------------------------------------------------------------

def run_daemon() -> None:
    """Main supervisor loop for signal-cli daemon and event listener."""
    logger.info("Starting Zoth Sovereign Signal Dispatcher for %s...", PHONE_NUMBER)

    # Clean existing socket if orphaned
    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            pass

    # Start signal-cli daemon process
    daemon_cmd = [
        "signal-cli",
        "-u",
        PHONE_NUMBER,
        "daemon",
        "--socket",
        SOCKET_PATH,
        "--receive-mode",
        "on-start",
    ]
    logger.info("Launching signal-cli daemon: %s", " ".join(daemon_cmd))
    daemon_proc = subprocess.Popen(
        daemon_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # Wait for socket to become available
    for _ in range(30):
        if os.path.exists(SOCKET_PATH):
            break
        time.sleep(0.5)

    if not os.path.exists(SOCKET_PATH):
        logger.error("Signal socket was not created. Aborting.")
        daemon_proc.terminate()
        sys.exit(1)

    rpc_client = SignalRpcClient(SOCKET_PATH)
    processed_timestamps = set()

    # Start local HTTP API on 127.0.0.1:8768 for ZothOS & Sovereign Agent Bridge
    def start_signal_http_api():
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class SignalHTTPHandler(BaseHTTPRequestHandler):
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
                if self.path in ("/health", "/api/health", "/api/signal/status"):
                    self._send_json(200, {
                        "status": "ok",
                        "service": "zoth-signal-bridge",
                        "account": PHONE_NUMBER,
                        "socket_path": SOCKET_PATH,
                        "socket_active": os.path.exists(SOCKET_PATH),
                        "heartbeat_enabled": HEARTBEAT_ENABLED,
                        "voice_mode": VOICE_MODE_ENABLED,
                    })
                elif self.path.startswith("/api/signal/messages"):
                    self._send_json(200, {
                        "status": "ok",
                        "account": PHONE_NUMBER,
                        "messages": SIGNAL_MESSAGE_HISTORY[-50:],
                    })
                else:
                    self._send_json(404, {"error": "Not found"})

            def do_POST(self):
                if self.path in ("/api/signal/send", "/api/send"):
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                        body = json.loads(raw)
                        text = str(body.get("text") or body.get("message") or "").strip()
                        recipient = str(body.get("recipient") or PHONE_NUMBER).strip()
                        if not text:
                            self._send_json(400, {"error": "Message text is required"})
                            return
                        ok = rpc_client.send_message(recipient, text)
                        self._send_json(200 if ok else 502, {
                            "status": "DELIVERED" if ok else "FAILED",
                            "channel": "signal",
                            "recipient": recipient,
                            "length": len(text),
                        })
                    except Exception as e:
                        self._send_json(500, {"error": str(e)})
                else:
                    self._send_json(404, {"error": "Not found"})

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", SIGNAL_HTTP_PORT), SignalHTTPHandler)
            logger.info("Signal HTTP Bridge listening on http://127.0.0.1:%d", SIGNAL_HTTP_PORT)
            httpd.serve_forever()
        except Exception as e:
            logger.warning("Signal HTTP Bridge could not bind port %d: %s", SIGNAL_HTTP_PORT, e)

    threading.Thread(target=start_signal_http_api, daemon=True).start()

    # Start periodic heartbeat thread
    threading.Thread(target=run_heartbeat_loop, args=(rpc_client,), daemon=True).start()

    # Log signal-cli stderr in background
    def log_stderr():
        for line in daemon_proc.stderr:
            logger.debug("[signal-cli-err] %s", line.strip())

    threading.Thread(target=log_stderr, daemon=True).start()

    logger.info("Signal-cli daemon ready on %s. Connecting event stream...", SOCKET_PATH)

    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream_sock:
                stream_sock.connect(SOCKET_PATH)
                logger.info("Connected to signal-cli event stream.")
                sock_file = stream_sock.makefile("r", encoding="utf-8")

                for line in sock_file:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Process notification
                    if data.get("method") == "receive":
                        envelope = data.get("params", {}).get("envelope", {})
                        ts = envelope.get("timestamp")
                        if ts and ts in processed_timestamps:
                            continue
                        if ts:
                            processed_timestamps.add(ts)
                            if len(processed_timestamps) > 1000:
                                processed_timestamps.clear()

                        # 1. Direct message
                        data_msg = envelope.get("dataMessage")
                        if data_msg:
                            attachments = data_msg.get("attachments", []) or []
                            audio_att = [a for a in attachments if str(a.get("contentType", "")).startswith("audio/")]
                            if audio_att:
                                threading.Thread(
                                    target=handle_incoming_voice_note,
                                    args=(audio_att[0], data_msg.get("message", ""), rpc_client),
                                    daemon=True,
                                ).start()
                            elif data_msg.get("message"):
                                msg_text = data_msg.get("message")
                                threading.Thread(
                                    target=handle_incoming_text,
                                    args=(msg_text, rpc_client),
                                    daemon=True,
                                ).start()

                        # 2. Sync message (Note to Self / user texting self)
                        sync_msg = envelope.get("syncMessage")
                        if sync_msg:
                            sent_msg = sync_msg.get("sentMessage", {}) or {}
                            attachments = sent_msg.get("attachments", []) or []
                            audio_att = [a for a in attachments if str(a.get("contentType", "")).startswith("audio/")]
                            if audio_att:
                                threading.Thread(
                                    target=handle_incoming_voice_note,
                                    args=(audio_att[0], sent_msg.get("message", ""), rpc_client),
                                    daemon=True,
                                ).start()
                            elif sent_msg.get("message"):
                                msg_text = sent_msg.get("message")
                                threading.Thread(
                                    target=handle_incoming_text,
                                    args=(msg_text, rpc_client),
                                    daemon=True,
                                ).start()

        except Exception as e:
            logger.warning("Event stream disconnected: %s. Reconnecting in 3s...", e)
            time.sleep(3.0)


if __name__ == "__main__":
    try:
        run_daemon()
    except KeyboardInterrupt:
        logger.info("Shutting down dispatcher...")
        sys.exit(0)
