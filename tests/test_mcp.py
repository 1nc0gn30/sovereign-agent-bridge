"""Tests for Model Context Protocol (MCP) Server in sovereign_agent_bridge.mcp_server."""

from __future__ import annotations

import json
import pytest

from sovereign_agent_bridge.mcp_server import MCPServer


def test_mcp_server_initialize_handshake():
    """Test MCP JSON-RPC 2.0 initialize request."""
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    res = server.handle_request(json.dumps(req).encode("utf-8"))
    assert res["jsonrpc"] == "2.0"
    assert res["id"] == 1
    assert "result" in res
    assert res["result"]["serverInfo"]["name"] == "sovereign-agent-bridge"
    assert res["result"]["serverInfo"]["version"] == "0.1.0"


def test_mcp_server_tools_list():
    """Test MCP tools/list returns all registered bridge tools."""
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    res = server.handle_request(json.dumps(req).encode("utf-8"))
    tools = res.get("result", {}).get("tools", [])
    assert len(tools) >= 8

    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "bridge_send",
        "bridge_broadcast",
        "bridge_list_channels",
        "bridge_claim_project",
        "bridge_consensus",
        "bridge_heartbeat",
        "bridge_stats",
        "bridge_diagnostics",
    }
    assert expected_tools.issubset(tool_names)


def test_mcp_tool_execution_stats():
    """Test executing bridge_stats tool."""
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "bridge_stats", "arguments": {"detailed": True}},
    }

    res = server.handle_request(json.dumps(req).encode("utf-8"))
    assert res["id"] == 3
    content_list = res["result"]["content"]
    assert len(content_list) > 0
    raw_text = content_list[0]["text"]
    parsed = json.loads(raw_text)
    assert parsed["server"] == "sovereign-agent-bridge"
    assert "telemetry" in parsed


def test_mcp_tool_execution_claims():
    """Test executing bridge_claim_project tool via MCP."""
    server = MCPServer()

    # Claim lock
    claim_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "bridge_claim_project",
            "arguments": {
                "action": "claim",
                "project_id": "proj-mcp-test",
                "agent_id": "agent-claude",
                "ttl": 120.0,
            },
        },
    }
    claim_res = server.handle_request(json.dumps(claim_req).encode("utf-8"))
    parsed_claim = json.loads(claim_res["result"]["content"][0]["text"])
    assert parsed_claim["success"] is True

    # Release lock
    rel_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "bridge_claim_project",
            "arguments": {
                "action": "release",
                "project_id": "proj-mcp-test",
                "agent_id": "agent-claude",
            },
        },
    }
    rel_res = server.handle_request(json.dumps(rel_req).encode("utf-8"))
    parsed_rel = json.loads(rel_res["result"]["content"][0]["text"])
    assert parsed_rel["success"] is True


def test_mcp_tool_execution_consensus():
    """Test executing bridge_consensus tool via MCP."""
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "bridge_consensus",
            "arguments": {
                "proposal": "Migrate database engine to distributed SQLite with Raft",
                "proponent": "Architect-Alpha",
                "skeptic": "Security-Skeptic",
                "arbitrator": "Lead-Arbitrator",
                "rounds": 2,
            },
        },
    }

    res = server.handle_request(json.dumps(req).encode("utf-8"))
    parsed = json.loads(res["result"]["content"][0]["text"])
    assert "consensus_id" in parsed or "final_decision" in parsed or "deliberation" in parsed


def test_mcp_unknown_method_error():
    """Test MCP server handling unknown RPC method."""
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 99, "method": "invalid/unknownMethod", "params": {}}

    res = server.handle_request(json.dumps(req).encode("utf-8"))
    assert "error" in res
    assert res["error"]["code"] == -32601  # Method not found
