"""Explicit chat actions and mentions, parsed without calling a model."""
from dataclasses import dataclass
import re


IDENTITY = r"[A-Za-z0-9_-]{1,64}"
COMMAND_NAMES = ("/help", "/agents", "/users", "/status", "/set")
MODEL_ROLES = {
    "Coder": ("Coding and debugging", ["coding", "debugging"]),
    "Debugger": ("Debugging and fixing defects", ["debugging"]),
    "Researcher": ("Research and investigation", ["research"]),
    "Writer": ("Writing and editing prose", ["writing"]),
    "Reasoner": ("Reasoning and problem solving", ["reasoning"]),
    "Chat": ("", []),
}
ROLE_NAMES = ("Head", *MODEL_ROLES)
HELP_TEXT = """### Chat commands

| Command | Action |
| --- | --- |
| `/set @agent as Head` | Save the workspace's Head for chat and orchestration. |
| `/set head auto` | Restore the first imported model as Head, with available-model fallback. |
| `/set @agent as Coder` | Enable coding and debugging delegation. |
| `/set @agent as Debugger` | Enable debugging delegation. |
| `/set @agent as Researcher` | Enable research delegation. |
| `/set @agent as Writer` | Enable writing delegation. |
| `/set @agent as Reasoner` | Enable reasoning delegation. |
| `/set @agent as Chat` | Turn off specialist delegation for that model. |
| `/agents` | List model agents and their availability. |
| `/users` | List device identities in this workspace. |
| `/status` | Show the Head and specialist routing settings. |
| `/help` | Show this help. |

Only the workspace owner can assign roles. Specialist roles apply to models
imported on the owner's device. Roles replace the model's purpose and permitted
tasks; they describe your choice of specialist, not a capability test.

Type `@` to find users and agents, or `/` to find commands. Start a message with
`@name` to open that recipient's conversation and send it there. The current
conversation's history stays in its conversation. Inline mentions remain text.
Use `//` at the start to send a literal leading slash.
"""


@dataclass(frozen=True)
class ChatCommand:
    name: str
    identity: str | None = None
    role: str | None = None


def is_command(text):
    text = text.lstrip()
    return text.startswith("/") and not text.startswith("//")


def parse_command(text):
    if not is_command(text):
        return None
    if len(text) > 2000:
        raise ValueError("This command is too long. Type /help for available commands.")
    parts = text.split()
    name = parts[0].casefold()
    if name in COMMAND_NAMES[:-1] and len(parts) == 1:
        return ChatCommand(name[1:])
    if name == "/set":
        if [part.casefold() for part in parts[1:]] == ["head", "auto"]:
            return ChatCommand("set", role="Automatic")
        if (len(parts) == 4 and re.fullmatch("@" + IDENTITY, parts[1])
                and parts[2].casefold() == "as"):
            role = next((role for role in ROLE_NAMES if role.casefold() == parts[3].casefold()), None)
            if role:
                return ChatCommand("set", parts[1][1:], role)
        raise ValueError("Use /set @agent as Head, Coder, Debugger, Researcher, Writer, Reasoner or Chat; or /set head auto.")
    raise ValueError("Unknown command or arguments. Type /help for available commands.")


def leading_mention(text):
    """Select one explicit recipient; emails and inline references remain text."""
    match = re.match(r"^\s*@(" + IDENTITY + r")(?:[,:]?\s+|$)(.*)$", text, re.S)
    return (match[1], match[2].strip()) if match else None


def resolve_identity(name, identities):
    identities = list(dict.fromkeys(identities))
    if name in identities:
        return name
    matches = [identity for identity in identities if identity.casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"@{name} is ambiguous. Use the exact identity from /agents or /users.")
    raise ValueError(f"@{name} is not in this workspace. Use /agents or /users to find an identity.")
