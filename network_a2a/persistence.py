"""Atomic local history storage, shared by the relay and desktop UI."""
import json
import sqlite3
from collections import defaultdict, deque
from contextlib import closing, contextmanager
from pathlib import Path

from .content import MAX_MESSAGE_BYTES, MAX_TEXT_BYTES, content_summary, text_content


class HistoryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "history.sqlite3"
        with self.connection() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, value TEXT NOT NULL, PRIMARY KEY (namespace, key))")

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as database:
            with database:
                yield database

    def load(self, namespace):
        with self.connection() as database:
            return {key: json.loads(value) for key, value in database.execute(
                "SELECT key, value FROM records WHERE namespace = ?", (namespace,))}

    def save(self, namespace, key, value):
        encoded = json.dumps(value, ensure_ascii=False)
        with self.connection() as database:
            database.execute("INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                             (namespace, key, encoded))

    def delete(self, namespace, key):
        """Drop one record, leaving every other namespace and key alone.

        ``clear`` is the only other way to remove anything and it empties the
        whole table, which is right when a workspace is deleted and far too
        much for removing a single conversation. Anything held in a ``("ui",
        "state")`` blob is not one record per conversation and still has to be
        read, edited and written back by its owner.
        """
        with self.connection() as database:
            database.execute("DELETE FROM records WHERE namespace = ? AND key = ?",
                             (namespace, key))

    def clear(self):
        with self.connection() as database:
            database.execute("DELETE FROM records")
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database.execute("VACUUM")


def conversation_archive(chat):
    """Recover the complete visible chat with original attachments and responder names."""
    content = defaultdict(deque)
    for message in chat.get("history", []):
        content[(message["role"], content_summary(message["content"]))].append(message["content"])
    archive = []
    for index, (role, text) in enumerate(chat.get("messages", [])):
        if role not in ("user", "assistant"):
            continue
        prepared = content[(role, text)]
        message = {"role": role, "content": prepared.popleft() if prepared else text}
        responder = chat.get("responders", {}).get(str(index))
        if role == "assistant" and responder:
            message["responder"] = responder
        archive.append(message)
    return archive


def model_context(messages, limit=100, byte_limit=190000):
    """Keep the saved archive intact while fitting the provider transport."""
    history = [{"role": message["role"], "content": message["content"]}
               for message in messages if message["role"] in ("user", "assistant")][-limit:]
    media = any(isinstance(message["content"], list) for message in history)
    budget = MAX_MESSAGE_BYTES - 4096 if media else byte_limit
    while len(history) > 1 and (len(json.dumps(history).encode()) > budget or
            sum(len(text_content(message["content"]).encode()) for message in history) > MAX_TEXT_BYTES - 4096):
        history.pop(0)
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history
