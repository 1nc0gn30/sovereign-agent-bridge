"""Comprehensive tests for Cryptographic Message Envelope and Federation Gateway.

Tests zero-dependency RFC 5869 HKDF, SHA-256 CTR cipher, authenticated envelopes,
monotonic sequence protection, cross-bridge federation mesh, MCP tools, CLI, and REST APIs.
"""

from __future__ import annotations

import json
import time
import pytest

from sovereign_agent_bridge.crypto_envelope import (
    EnvelopeSecurityManager,
    MessageEnvelope,
    derive_keys,
    hkdf_expand,
    hkdf_extract,
    sha256_ctr_crypt,
)
from sovereign_agent_bridge.federation_gateway import (
    BridgePeer,
    FederatedMessage,
    FederationGateway,
)
from sovereign_agent_bridge.mcp_server import MCPServer
from sovereign_agent_bridge.ui_server import BridgeUIServer
from sovereign_agent_bridge.cli import build_parser, handle_envelope, handle_federation


# ---------------------------------------------------------------------------
# Crypto Envelope & HKDF Tests
# ---------------------------------------------------------------------------

def test_hkdf_derivation_and_properties():
    """Verify HKDF-SHA256 extract and expand produces correct lengths and determinism."""
    ikm = b"initial-keying-material-super-secret"
    salt = b"salt-12345"
    prk = hkdf_extract(salt, ikm)
    assert len(prk) == 32

    okm_32 = hkdf_expand(prk, b"handshake-context", length=32)
    assert len(okm_32) == 32

    okm_64 = hkdf_expand(prk, b"handshake-context", length=64)
    assert len(okm_64) == 64
    assert okm_64[:32] == okm_32

    enc_key, mac_key = derive_keys("shared-passphrase", salt="salt-12345", info="agent-bridge-v1")
    assert len(enc_key) == 32
    assert len(mac_key) == 32
    assert enc_key != mac_key


def test_sha256_ctr_crypt_symmetry():
    """Verify SHA-256 CTR keystream cipher encrypts and decrypts symmetrically."""
    key = b"A" * 32
    iv = b"B" * 16
    plaintext = b"Autonomous agent execution plan: deploy swarm to nodes [1, 2, 3]."

    ciphertext = sha256_ctr_crypt(data=plaintext, key=key, nonce=iv)
    assert ciphertext != plaintext
    assert len(ciphertext) == len(plaintext)

    decrypted = sha256_ctr_crypt(data=ciphertext, key=key, nonce=iv)
    assert decrypted == plaintext


def test_envelope_seal_and_open_encrypted():
    """Verify envelope manager seals and opens encrypted payloads with HMAC validation."""
    alice = EnvelopeSecurityManager(agent_id="agent-alice")
    bob = EnvelopeSecurityManager(agent_id="agent-bob")
    shared_secret = "test-cluster-shared-secret-key-12345"

    payload = {"task": "SECURITY_AUDIT", "target": "gateway-01", "level": 3}
    envelope = alice.seal_envelope(
        recipient_id="agent-bob",
        payload=payload,
        shared_secret=shared_secret,
        encrypt=True,
    )

    assert envelope.sender_id == "agent-alice"
    assert envelope.recipient_id == "agent-bob"
    assert envelope.is_encrypted is True
    assert envelope.seq_no == 1
    assert envelope.payload_b64 is not None
    assert envelope.nonce_hex is not None
    assert envelope.mac_hex is not None

    opened = bob.open_envelope(envelope, shared_secret=shared_secret)
    assert opened["verified"] is True
    assert opened["sender_id"] == "agent-alice"
    assert opened["seq_no"] == 1
    assert opened["payload"] == payload


def test_envelope_seal_and_open_unencrypted():
    """Verify envelope manager seals and opens authenticated plaintext envelopes."""
    alice = EnvelopeSecurityManager(agent_id="agent-alice")
    bob = EnvelopeSecurityManager(agent_id="agent-bob")
    shared_secret = "test-cluster-shared-secret-key-12345"

    payload = {"announcement": "Network reboot scheduled in 10 minutes"}
    envelope = alice.seal_envelope(
        recipient_id="agent-bob",
        payload=payload,
        shared_secret=shared_secret,
        encrypt=False,
    )

    assert envelope.is_encrypted is False
    assert envelope.payload_b64 is not None
    assert envelope.mac_hex is not None

    opened = bob.open_envelope(envelope.to_json(), shared_secret=shared_secret)
    assert opened["verified"] is True
    assert opened["payload"] == payload


def test_envelope_tamper_detection():
    """Verify envelope rejection when payload or MAC is altered."""
    mgr = EnvelopeSecurityManager(agent_id="agent-test")
    envelope = mgr.seal_envelope("recipient", {"data": 42}, encrypt=True)

    # Tamper with MAC
    raw_dict = envelope.to_dict()
    raw_dict["mac_hex"] = "0" * 64
    tampered_env = MessageEnvelope.from_dict(raw_dict)

    with pytest.raises(ValueError, match="Integrity failure"):
        mgr.open_envelope(tampered_env)


def test_envelope_replay_and_sequence_protection():
    """Verify sequence counter progression and replay rejection."""
    sender = EnvelopeSecurityManager(agent_id="sender")
    receiver = EnvelopeSecurityManager(agent_id="receiver")

    env1 = sender.seal_envelope("receiver", {"step": 1})
    env2 = sender.seal_envelope("receiver", {"step": 2})

    assert env1.seq_no == 1
    assert env2.seq_no == 2

    # Receiver opens env1 then env2
    receiver.open_envelope(env1)
    receiver.open_envelope(env2)

    # Replaying env1 should be rejected (sequence regression)
    with pytest.raises(ValueError, match="Replay or out-of-order sequence detected"):
        receiver.open_envelope(env1)


# ---------------------------------------------------------------------------
# Federation Gateway Tests
# ---------------------------------------------------------------------------

def test_federation_peer_registration_and_topology():
    """Verify registering peers, updating heartbeats, and inspecting mesh topology."""
    gateway = FederationGateway(bridge_id="bridge-node-alpha")
    peer = gateway.register_peer("bridge-node-beta", "http://127.0.0.1:8789", federated_topics=["swarm.*", "alerts.#"])

    assert peer.peer_id == "bridge-node-beta"
    assert peer.endpoint_url == "http://127.0.0.1:8789"
    assert "swarm.*" in peer.federated_topics

    topo = gateway.get_topology()
    assert topo["bridge_id"] == "bridge-node-alpha"
    assert topo["peer_count"] == 1
    assert any(p["peer_id"] == "bridge-node-beta" for p in topo["peers"])

    # Deregister peer
    assert gateway.unregister_peer("bridge-node-beta") is True
    assert gateway.get_topology()["peer_count"] == 0


def test_federation_routing_and_loop_prevention():
    """Verify outbound dispatch, visited_bridges tagging, and loop prevention."""
    gw_a = FederationGateway(bridge_id="bridge-A")
    gw_a.register_peer("bridge-B", "http://127.0.0.1:8789", federated_topics=["metrics.#"])

    # Outbound routing generates FederatedMessage
    res = gw_a.route_outbound("metrics.cpu", {"usage": 88.5}, max_hops=4)
    assert res["total_dispatched"] == 1
    assert "bridge-B" in res["dispatched_peers"]

    # Inbound message with bridge-A already visited should be dropped (loop prevention)
    looped_msg = FederatedMessage(
        message_id="msg-loop-01",
        origin_bridge_id="bridge-X",
        topic="metrics.cpu",
        payload={"usage": 90.0},
        visited_bridges=["bridge-X", "bridge-A"],
        max_hops=3,
    )
    ok, status, _ = gw_a.receive_inbound(looped_msg)
    assert ok is False
    assert "Routing loop detected" in status


def test_federation_ttl_exhaustion():
    """Verify dropping messages whose TTL has expired."""
    gw = FederationGateway(bridge_id="bridge-test")
    dead_msg = FederatedMessage(
        message_id="msg-dead-01",
        origin_bridge_id="bridge-origin",
        topic="test.topic",
        payload={"data": "dead"},
        hop_count=3,
        max_hops=3,
    )
    ok, status, _ = gw.receive_inbound(dead_msg)
    assert ok is False
    assert "TTL/Hop limit exceeded" in status


def test_federation_local_subscriber_dispatch():
    """Verify local agent subscribers receive federated messages matching topic pattern."""
    gw = FederationGateway(bridge_id="bridge-subscriber-hub")
    received = []

    def on_alert(msg: FederatedMessage):
        received.append(msg.payload)

    gw.subscribe_local("alerts.security", on_alert)

    # Inbound message
    incoming = FederatedMessage(
        message_id="msg-sec-01",
        origin_bridge_id="bridge-sensor",
        topic="alerts.security",
        payload={"alert": "PORT_SCAN_DETECTED"},
        hop_count=0,
        max_hops=5,
    )
    ok, status, routed = gw.receive_inbound(incoming)
    assert ok is True
    assert len(received) == 1
    assert received[0]["alert"] == "PORT_SCAN_DETECTED"


# ---------------------------------------------------------------------------
# MCP Server Tool Tests
# ---------------------------------------------------------------------------

def test_mcp_envelope_and_federation_tools():
    """Verify MCP tools for envelope seal/open and federation topology/routing."""
    server = MCPServer()

    # 1. bridge_seal_envelope
    seal_res = server.execute_tool(
        "bridge_seal_envelope",
        {
            "recipient_id": "agent-delta",
            "payload": {"directive": "MIGRATE_CONTAINER", "container_id": "c-99"},
            "encrypt": True,
        },
    )
    assert seal_res["isError"] is False
    seal_data = json.loads(seal_res["content"][0]["text"])
    assert "envelope_id" in seal_data
    assert seal_data["is_encrypted"] is True

    # 2. bridge_open_envelope
    open_res = server.execute_tool(
        "bridge_open_envelope",
        {"envelope": seal_data},
    )
    assert open_res["isError"] is False
    open_data = json.loads(open_res["content"][0]["text"])
    assert open_data["verified"] is True
    assert open_data["payload"]["directive"] == "MIGRATE_CONTAINER"

    # 3. bridge_federation_topology
    topo_res = server.execute_tool("bridge_federation_topology", {})
    assert topo_res["isError"] is False
    topo_data = json.loads(topo_res["content"][0]["text"])
    assert "bridge_id" in topo_data
    assert "peer_count" in topo_data

    # 4. bridge_federate_message
    fed_res = server.execute_tool(
        "bridge_federate_message",
        {"topic": "cluster.heartbeat", "payload": {"status": "HEALTHY"}},
    )
    assert fed_res["isError"] is False
    fed_data = json.loads(fed_res["content"][0]["text"])
    assert "message_id" in fed_data


# ---------------------------------------------------------------------------
# CLI Command Tests
# ---------------------------------------------------------------------------

def test_cli_envelope_and_federation(capsys):
    """Verify CLI handling for envelope seal/open and federation commands."""
    parser = build_parser()

    # 1. Envelope seal
    args_seal = parser.parse_args(["envelope", "seal", "-r", "agent-bob", "-p", "CLI Test Payload", "--json"])
    assert handle_envelope(args_seal) == 0
    captured = capsys.readouterr()
    seal_out = json.loads(captured.out)
    assert "envelope_id" in seal_out

    # 2. Envelope open
    args_open = parser.parse_args(["envelope", "open", "-e", json.dumps(seal_out), "--json"])
    assert handle_envelope(args_open) == 0
    captured_open = capsys.readouterr()
    open_out = json.loads(captured_open.out)
    assert open_out["verified"] is True
    assert open_out["payload"] == "CLI Test Payload"

    # 3. Federation topology
    args_fed = parser.parse_args(["federation", "--json"])
    assert handle_federation(args_fed) == 0
    captured_fed = capsys.readouterr()
    fed_out = json.loads(captured_fed.out)
    assert "bridge_id" in fed_out

    # 4. Federation broadcast
    args_bcast = parser.parse_args(["federation", "-b", "Test Broadcast Data", "-t", "swarm.news", "--json"])
    assert handle_federation(args_bcast) == 0
    captured_bcast = capsys.readouterr()
    bcast_out = json.loads(captured_bcast.out)
    assert "message_id" in bcast_out


# ---------------------------------------------------------------------------
# UI Server REST API Tests
# ---------------------------------------------------------------------------

def test_ui_server_envelope_and_federation_api():
    """Verify REST API endpoints for envelope and federation mesh."""
    import urllib.request

    ui = BridgeUIServer(host="127.0.0.1", port=0)
    ui.start(blocking=False)
    time.sleep(0.1)

    try:
        base = f"http://127.0.0.1:{ui.port}"

        # GET /api/envelopes
        with urllib.request.urlopen(f"{base}/api/envelopes") as resp:
            assert resp.status == 200
            env_info = json.loads(resp.read().decode())
            assert "ciphers" in env_info

        # GET /api/federation
        with urllib.request.urlopen(f"{base}/api/federation") as resp:
            assert resp.status == 200
            fed_info = json.loads(resp.read().decode())
            assert "bridge_id" in fed_info

        # POST /api/envelopes/seal
        seal_req = urllib.request.Request(
            f"{base}/api/envelopes/seal",
            data=json.dumps({"recipient_id": "test-rx", "payload": "Secret Instructions"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(seal_req) as resp:
            assert resp.status == 200
            sealed = json.loads(resp.read().decode())
            assert sealed["success"] is True
            env = sealed["envelope"]

        # POST /api/envelopes/open
        open_req = urllib.request.Request(
            f"{base}/api/envelopes/open",
            data=json.dumps({"envelope": env}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(open_req) as resp:
            assert resp.status == 200
            opened = json.loads(resp.read().decode())
            assert opened["success"] is True
            assert opened["opened"]["payload"] == "Secret Instructions"

        # POST /api/federation/peers
        peer_req = urllib.request.Request(
            f"{base}/api/federation/peers",
            data=json.dumps({
                "peer_id": "bridge-remote-9",
                "endpoint_url": "http://127.0.0.1:9999",
                "topics": ["tasks.#"],
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(peer_req) as resp:
            assert resp.status == 200
            peer_res = json.loads(resp.read().decode())
            assert peer_res["success"] is True
            assert peer_res["peer"]["peer_id"] == "bridge-remote-9"

        # POST /api/federation/route
        route_req = urllib.request.Request(
            f"{base}/api/federation/route",
            data=json.dumps({"topic": "tasks.build", "payload": {"repo": "agent-bridge"}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(route_req) as resp:
            assert resp.status == 200
            route_res = json.loads(resp.read().decode())
            assert route_res["success"] is True
            assert "message_id" in route_res["result"]
    finally:
        ui.stop()
