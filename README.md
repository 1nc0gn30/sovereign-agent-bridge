# Sovereign Agent Bridge

Zero-dependency multi-agent communication bridge, 3-way dialectic consensus engine, distributed mutex lock coordinator, dead-man heartbeat watchdog, and Model Context Protocol (MCP) server built on the Python Standard Library.

- **Repository**: `https://github.com/1nc0gn30/sovereign-agent-bridge` (`https://github.com/NullAITech/sovereign-agent-bridge`)
- **Local Workstation Port**: `http://127.0.0.1:8102`
- **Runtime**: Python 3.9+ (zero external runtime dependencies) + Node.js launcher (`bin/sovereign-bridge.js`)
- **License**: MIT (100% open-access, zero paywalls)

---

## Architecture & Core Capabilities

1. **Multi-Channel Transport Layer**
   - **Signal (`signal`)**: Direct and group messaging via local `signal-cli` REST or JSON-RPC socket.
   - **SimpleX (`simplex`)**: Metadata-minimized relay transport via `simplex-chat` SMP nodes.
   - **Telegram (`telegram`)**: Bidirectional bot notifications and command routing.
   - **Matrix (`matrix`)**: Homeserver room dispatch (`matrix.org` or self-hosted Synapse/Conduit).
   - **Signed HTTP Webhooks (`webhook`)**: Outbound and inbound JSON webhooks verified with HMAC-SHA256.
2. **3-Way Dialectic Consensus Engine**
   - Structured deliberation loop: **Proponent (Thesis)** -> **Skeptic (Antithesis)** -> **Proponent (Rebuttal)** -> **Arbitrator (Synthesis & Verdict)**.
   - Produces deterministic confidence scores and verdicts (`APPROVED`, `AMENDED`, `REJECTED`, `ESCALATE`).
3. **Distributed Claims & Dead-Man Watchdog**
   - Mutual-exclusion task claims with configurable TTL leases, heartbeat renewals, and atomic agent-to-agent handoffs.
   - State-machine liveness monitor (`ACTIVE` -> `WARNING` -> `DEAD` -> `RECOVERED`) with automatic alert fan-out.
4. **Authenticated Cryptographic Envelopes & Cross-Bridge Federation**
   - RFC 5869 HKDF key separation (`enc_key`, `mac_key`), SHA-256 CTR stream encryption, HMAC-SHA256 authentication, and monotonic sequence replay protection.
   - Peer bridge topic routing (`metrics.#`, `alerts.*`) with visited-bridge vector loop prevention and hop-count TTL limits.
5. **Cross-Agent Mesh Introspection & Gatekeeper (`:8102`)**
   - Live session radar and shared SQLite (`~/.config/zothos/agent-bridge-bus.db`) + Markdown (`~/.config/zothos/AGENT_BRIDGE_SYNC.md`) handoff bus across local coding agents (Muse, Antigravity, Hermes, Claude Code, Codex, Aider, OpenCode, Ollama).
   - Lease-based execution policy gatekeeper (`GET/POST /api/agents/authorize`, `/api/agents/release`).

---

## Installation & Quickstart

```bash
git clone https://github.com/1nc0gn30/sovereign-agent-bridge.git
cd sovereign-agent-bridge
pip install -e .
```

Run directly without editable install:

```bash
PYTHONPATH=src python3 -m sovereign_agent_bridge.cli --help
```

Launch the Bridge Studio web workstation on `http://127.0.0.1:8102`:

```bash
node bin/sovereign-bridge.js web
# or directly via Python CLI:
sovereign-bridge serve --host 127.0.0.1 --port 8102
```

---

## CLI Reference

```bash
# System diagnostics and channel health check
sovereign-bridge diagnostics --json
sovereign-bridge channels --test

# Send or broadcast messages
sovereign-bridge send local "Build pipeline green" -r agent-ops
sovereign-bridge broadcast "Deployment checkpoint reached" -p HIGH

# Acquire, list, renew, or release distributed project claims
sovereign-bridge claim repo-migration -a agent-architect --ttl 600
sovereign-bridge claim --list
sovereign-bridge claim repo-migration -a agent-architect --release

# Execute a 3-way dialectic consensus deliberation
sovereign-bridge consensus "Enforce loopback Origin checks on local HTTP daemons" --rounds 3

# Record agent heartbeat pulses
sovereign-bridge pulse agent-worker-01 --interval 30 --task "Running test suite"

# Cryptographic envelope seal & open
sovereign-bridge envelope seal -r agent-bob -p '{"directive":"SYNC"}'
sovereign-bridge envelope open -e '{"envelope_id":"..."}'

# Inspect live local AI agent mesh & post handoff
sovereign-bridge agents --json
sovereign-bridge handoff "Completed verification pass" --to all-agents --subject "CI Gate"
sovereign-bridge policy --list
```

---

## HTTP API Endpoints (`127.0.0.1:8102`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/stats` | Uptime, message counters, active claims, and consensus run metrics |
| `GET` | `/api/events` | Server-Sent Events (SSE) real-time telemetry stream |
| `GET` | `/api/agents/live` | Active local agent sessions, Gatekeeper policies, and handoff bus snapshot |
| `GET` | `/api/agents/messages` | Query recent cross-agent handoff messages from SQLite bus |
| `POST` | `/api/agents/message` | Publish a message to the cross-agent handoff bus |
| `POST` | `/api/agents/authorize` | Request an execution lease from the AI Tool Gatekeeper |
| `POST` | `/api/agents/release` | Release an active Gatekeeper execution lease |

---

## MCP Server Configuration

Register the JSON-RPC 2.0 stdio server in Claude Desktop, Cursor, Hermes, or Antigravity:

```json
{
  "mcpServers": {
    "sovereign-agent-bridge": {
      "command": "sovereign-bridge",
      "args": ["mcp"]
    }
  }
}
```

Exposed MCP tools: `bridge_send`, `bridge_broadcast`, `bridge_list_channels`, `bridge_claim_project`, `bridge_consensus`, `bridge_watchdog_pulse`, `bridge_seal_envelope`, `bridge_open_envelope`, `bridge_federation_topology`, `bridge_federate_message`, `bridge_stats`, `bridge_diagnostics`.

---

## Verification & Testing

```bash
python3 -m pytest -q
```

---

## Attribution & Support

Engineered by **1nc0gn30** ([@1nc0gn30](https://github.com/1nc0gn30) · [1nc0gn30.si](https://1nc0gn30.si)) · [NullAI](https://nullai.tech) · Cataloged in [Zoth Studio](https://zoth.nullai.tech).

- Optional support: [Buy Us a Coffee (Stripe)](https://buy.stripe.com/3cIfZg27PdKo2D64Ocfw40X)
