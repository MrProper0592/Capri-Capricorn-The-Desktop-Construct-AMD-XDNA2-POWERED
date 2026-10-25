"""
session_manager.py — Persistent multi-chat session management for Capri.
Maintains chat sessions with unique IDs, titles, timestamps, and message histories
saved to JSON (~/.config/capri/chats.json or custom path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
import uuid


def _now_iso() -> str:
    """Return the current local time in ISO 8601 format."""
    return datetime.now().astimezone().isoformat()


def format_session_time(iso_str: str) -> str:
    """Format an ISO timestamp for compact UI display in session rows."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        now = datetime.now().astimezone()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        if dt.year == now.year:
            return dt.strftime("%b %d %H:%M")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(iso_str)[:10]


@dataclass
class ChatSession:
    """Represents a single conversation session with metadata and message history."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    user_renamed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_renamed": self.user_renamed,
            "messages": [dict(m) for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatSession:
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            title=str(data.get("title") or "New Chat"),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            messages=list(data.get("messages") or []),
            user_renamed=bool(data.get("user_renamed", False)),
        )


class ChatSessionManager:
    """
    Manages persistent multi-session chat history backed by a JSON file.
    Supports creating, loading, switching, renaming, deleting, and auto-titling sessions.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else self.default_storage_path()
        self._sessions: list[ChatSession] = []
        self._active_session_id: str | None = None
        self.load()

    @staticmethod
    def default_storage_path() -> Path:
        """Resolve default chats storage path from environment or user config dir."""
        env_file = os.environ.get("CAPRI_CHATS_FILE")
        if env_file:
            return Path(env_file)
        env_config = os.environ.get("CAPRI_CONFIG_DIR")
        if env_config:
            return Path(env_config) / "chats.json"
        return Path.home() / ".config" / "capri" / "chats.json"

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    def load(self) -> None:
        """Load sessions from JSON storage. Creates a default session if file is empty or missing."""
        if not self.storage_path.exists():
            self._sessions = []
            self.new_session(title="New Chat", save=False)
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._sessions = []
            self.new_session(title="New Chat", save=False)
            return

        self._sessions = []
        active_id = None
        if isinstance(data, dict):
            active_id = data.get("active_session_id")
            raw_sessions = data.get("sessions") or []
        elif isinstance(data, list):
            raw_sessions = data
        else:
            raw_sessions = []

        for item in raw_sessions:
            if isinstance(item, dict):
                self._sessions.append(ChatSession.from_dict(item))

        if not self._sessions:
            self.new_session(title="New Chat", save=False)
        else:
            if active_id and any(s.id == active_id for s in self._sessions):
                self._active_session_id = active_id
            else:
                self._active_session_id = self._sessions[0].id

    def save(self) -> None:
        """Persist active sessions and metadata to JSON atomically."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "active_session_id": self._active_session_id,
                "sessions": [s.to_dict() for s in self._sessions],
            }
            tmp_path = self.storage_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            tmp_path.replace(self.storage_path)
        except Exception:
            # Fallback direct write if atomic file replacement encounters issues
            try:
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def get_active_session(self) -> ChatSession:
        """Return the current active session, creating one if none exists."""
        for s in self._sessions:
            if s.id == self._active_session_id:
                return s
        if self._sessions:
            self._active_session_id = self._sessions[0].id
            return self._sessions[0]
        return self.new_session(title="New Chat", save=True)

    def set_active_session(self, session_id: str) -> ChatSession | None:
        """Switch active session by ID."""
        for s in self._sessions:
            if s.id == session_id:
                self._active_session_id = session_id
                self.save()
                return s
        return None

    def new_session(self, title: str | None = None, save: bool = True) -> ChatSession:
        """Create a new chat session and make it active."""
        now = _now_iso()
        session = ChatSession(
            id=uuid.uuid4().hex[:12],
            title=(title or "New Chat").strip(),
            created_at=now,
            updated_at=now,
            messages=[],
            user_renamed=False,
        )
        self._sessions.insert(0, session)
        self._active_session_id = session.id
        if save:
            self.save()
        return session

    def add_message(self, role: str, content: str) -> None:
        """
        Append a message to the active session. If it is the first user message
        and the title has not been manually renamed, derive an automatic title snippet.
        """
        session = self.get_active_session()
        now = _now_iso()
        session.messages.append({"role": role, "content": content})
        session.updated_at = now

        # Auto-title on first user message if title has default placeholder
        if role == "user" and not session.user_renamed:
            if session.title in ("New Chat", "New Session", "") or session.title.startswith("Session "):
                clean_first_line = content.strip().splitlines()[0] if content.strip() else ""
                clean_snippet = clean_first_line[:32].strip()
                if clean_snippet:
                    if len(clean_first_line) > 32:
                        clean_snippet += "..."
                    session.title = clean_snippet

        self.save()

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """Rename a session by ID and flag it as user-renamed."""
        clean = new_title.strip()
        if not clean:
            return False
        for s in self._sessions:
            if s.id == session_id:
                s.title = clean
                s.user_renamed = True
                s.updated_at = _now_iso()
                self.save()
                return True
        return False

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session by ID. If the deleted session was the currently active one,
        starts a clean new chat.
        """
        target_idx = None
        for i, s in enumerate(self._sessions):
            if s.id == session_id:
                target_idx = i
                break
        if target_idx is None:
            return False

        was_active = (self._active_session_id == session_id)
        self._sessions.pop(target_idx)

        if was_active:
            # User requirement: if it was the currently open chat, starts a clean new chat
            self.new_session(title="New Chat", save=False)

        self.save()
        return True

    def clear_active_session(self) -> None:
        """Flush message history for the active session."""
        session = self.get_active_session()
        session.messages.clear()
        session.updated_at = _now_iso()
        self.save()

    def list_sessions(self) -> list[ChatSession]:
        """Return all sessions sorted by updated_at descending (most recent first)."""
        return sorted(self._sessions, key=lambda s: s.updated_at, reverse=True)
