"""
session_manager.py — Re-export of Capri's ChatSession and ChatSessionManager for the AI layer.
"""

from capri.desktop.session_manager import (
    ChatSession,
    ChatSessionManager,
    format_session_time,
)

__all__ = ["ChatSession", "ChatSessionManager", "format_session_time"]
