"""Tests for Command Line Interface (CLI) in sovereign_agent_bridge.cli."""

from __future__ import annotations

import io
import sys
import unittest.mock as mock
import pytest

from sovereign_agent_bridge.cli import (
    build_parser,
    handle_channels,
    handle_claim,
    handle_consensus,
    handle_diagnostics,
    handle_pulse,
    main,
)


def test_cli_build_parser_options():
    """Verify CLI parser options, subcommands, and flags."""
    parser = build_parser()
    assert parser.prog == "sovereign-bridge"

    # Test send arguments
    args_send = parser.parse_args(["send", "signal", "Test content", "-r", "+15551234567"])
    assert args_send.subcommand == "send"
    assert args_send.channel == "signal"
    assert args_send.message == "Test content"
    assert args_send.recipient == "+15551234567"

    # Test broadcast arguments
    args_bcast = parser.parse_args(["broadcast", "Global alert", "-p", "CRITICAL"])
    assert args_bcast.subcommand == "broadcast"
    assert args_bcast.message == "Global alert"
    assert args_bcast.priority == "CRITICAL"

    # Test consensus arguments
    args_cons = parser.parse_args(["consensus", "Adopt P2P gossip protocol", "--rounds", "3"])
    assert args_cons.subcommand == "consensus"
    assert args_cons.proposal == "Adopt P2P gossip protocol"
    assert args_cons.rounds == 3


def test_cli_handle_channels(capsys):
    """Test 'channels' subcommand execution."""
    parser = build_parser()
    args = parser.parse_args(["channels"])
    exit_code = handle_channels(args)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "CONFIGURED SOVEREIGN CHANNELS" in captured.out or "signal" in captured.out.lower()


def test_cli_handle_diagnostics(capsys):
    """Test 'diagnostics' subcommand execution."""
    parser = build_parser()
    args = parser.parse_args(["diagnostics"])
    exit_code = handle_diagnostics(args)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Diagnostics" in captured.out or "Python Version" in captured.out or "Platform" in captured.out


def test_cli_handle_claim_and_pulse(capsys):
    """Test 'claim' and 'pulse' subcommand handlers."""
    parser = build_parser()

    # Pulse
    args_pulse = parser.parse_args(["pulse", "agent-cli-test", "--task", "running tests"])
    code_pulse = handle_pulse(args_pulse)
    assert code_pulse == 0
    captured_pulse = capsys.readouterr()
    assert "agent-cli-test" in captured_pulse.out

    # Claim
    args_claim = parser.parse_args(["claim", "project-cli-test", "-a", "agent-cli-test", "--ttl", "120"])
    code_claim = handle_claim(args_claim)
    assert code_claim == 0
    captured_claim = capsys.readouterr()
    assert "project-cli-test" in captured_claim.out


def test_cli_main_entrypoint(capsys):
    """Test CLI main() invocation with arguments via sys.argv."""
    with mock.patch.object(sys, "argv", ["sovereign-bridge", "diagnostics"]):
        code_diag = main()
        assert code_diag == 0

    with mock.patch.object(sys, "argv", ["sovereign-bridge"]):
        code_empty = main()
        assert code_empty == 0
