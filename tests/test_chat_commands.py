"""Typed workspace actions have a small, deterministic grammar."""
import pytest

from network_a2a.chat_commands import (ChatCommand, HELP_TEXT, MODEL_ROLES,
                                       leading_mention, parse_command, resolve_identity)


@pytest.mark.parametrize("text,expected", [
    ("/help", ChatCommand("help")), (" /AGENTS ", ChatCommand("agents")),
    ("/users", ChatCommand("users")), ("/status", ChatCommand("status")),
    ("/set @my-agent as Head", ChatCommand("set", "my-agent", "Head")),
    ("/SET @my-agent AS coder", ChatCommand("set", "my-agent", "Coder")),
    ("/set head auto", ChatCommand("set", role="Automatic")),
    *[(f"/set @agent as {role}", ChatCommand("set", "agent", role)) for role in MODEL_ROLES],
])
def test_supported_commands(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "", "hello /help", "//help", "```\n/set @agent as Head\n```",
    "The model replied: /set @other as Head", "@agent /help",
])
def test_normal_messages_are_not_commands(text):
    assert parse_command(text) is None


@pytest.mark.parametrize("text", [
    "/unknown", "/help extra", "/set", "/set agent as Head", "/set @agent as Owner",
    "/set @agent as Head; quit", "/set @agent as Head\n/help", "/set @../agent as Head",
    "/set @" + "a" * 65 + " as Head", "/" + "x" * 2001,
])
def test_invalid_commands_fail_with_help_instead_of_becoming_prompts(text):
    with pytest.raises(ValueError):
        parse_command(text)


@pytest.mark.parametrize("text,expected", [
    ("@alice hello", ("alice", "hello")),
    ("@coder, fix this\ncode", ("coder", "fix this\ncode")),
    (" @agent-name: @other is referenced", ("agent-name", "@other is referenced")),
    ("@alice", ("alice", "")), ("Ask @alice", None),
    ("alice@example.com", None), ("@alice@example.com", None),
    ("`@alice hello`", None),
])
def test_only_a_leading_identity_addresses_a_recipient(text, expected):
    assert leading_mention(text) == expected


def test_resolution_requires_a_visible_unambiguous_identity():
    assert resolve_identity("ALICE", ["alice", "coder"]) == "alice"
    assert resolve_identity("Alice", ["Alice", "alice"]) == "Alice"
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_identity("ALICE", ["Alice", "alice"])
    with pytest.raises(ValueError, match="not in this workspace"):
        resolve_identity("private", ["alice", "coder"])
    for command in ("/set @agent as Head", "/set head auto", "/help", "/agents", "/users", "/status"):
        assert command in HELP_TEXT
