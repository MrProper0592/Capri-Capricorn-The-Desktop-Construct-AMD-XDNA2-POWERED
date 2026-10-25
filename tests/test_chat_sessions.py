"""
test_chat_sessions.py — Unit and UI smoke tests for Capri multi-chat session persistence
and cyberpunk terminal recent chats drawer.
Run with:
    QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests/test_chat_sessions.py
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Headless Qt setup before importing PySide6 widgets
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from capri.desktop.session_manager import ChatSession, ChatSessionManager
from capri.desktop.chat_window import ChatWindow, ChatSessionRow


# Ensure single QApplication instance across tests
_app = QApplication.instance()
if _app is None:
    _app = QApplication([])


class TestChatSessionManager(unittest.TestCase):
    """Test suite for ChatSessionManager JSON persistence and session operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = Path(self.temp_dir) / "chats.json"
        self.mgr = ChatSessionManager(storage_path=self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_persist_reload(self):
        """Verify creating a session, persisting messages to JSON, and reloading."""
        active = self.mgr.get_active_session()
        self.assertIsNotNone(active)
        self.assertEqual(active.title, "New Chat")

        # Add messages
        self.mgr.add_message("user", "Hello Capri, explain quantum tunneling")
        self.mgr.add_message("assistant", "Quantum tunneling is a quantum mechanical phenomenon...")

        self.assertTrue(self.storage_file.exists())

        # Verify JSON content on disk
        with open(self.storage_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("sessions", data)
        self.assertEqual(len(data["sessions"]), 1)
        saved = data["sessions"][0]
        self.assertEqual(len(saved["messages"]), 2)
        self.assertEqual(saved["messages"][0]["role"], "user")
        self.assertEqual(saved["messages"][1]["role"], "assistant")
        self.assertEqual(saved["title"], "Hello Capri, explain quantum tun...")

        # Reload in a fresh manager instance
        fresh_mgr = ChatSessionManager(storage_path=self.storage_file)
        self.assertEqual(len(fresh_mgr.list_sessions()), 1)
        reloaded = fresh_mgr.get_active_session()
        self.assertEqual(reloaded.id, active.id)
        self.assertEqual(len(reloaded.messages), 2)
        self.assertEqual(reloaded.messages[0]["content"], "Hello Capri, explain quantum tunneling")

    def test_auto_titling(self):
        """Verify first user message derives a readable session title."""
        active = self.mgr.get_active_session()
        self.mgr.add_message("user", "What is the AMD Ryzen AI NPU architecture?")
        self.assertEqual(active.title, "What is the AMD Ryzen AI NPU arc...")

    def test_rename_session(self):
        """Verify renaming a session updates the title in JSON and sets user_renamed flag."""
        active = self.mgr.get_active_session()
        success = self.mgr.rename_session(active.id, "NPU Telemetry Research")
        self.assertTrue(success)
        self.assertEqual(active.title, "NPU Telemetry Research")
        self.assertTrue(active.user_renamed)

        # Ensure auto-title does NOT overwrite custom title
        self.mgr.add_message("user", "Another question that shouldn't override")
        self.assertEqual(active.title, "NPU Telemetry Research")

        # Reload from JSON and check persistence
        fresh_mgr = ChatSessionManager(storage_path=self.storage_file)
        self.assertEqual(fresh_mgr.get_active_session().title, "NPU Telemetry Research")

    def test_delete_session(self):
        """Verify deleting a chat removes it from JSON."""
        s1 = self.mgr.get_active_session()
        s2 = self.mgr.new_session(title="Second Session")

        self.assertEqual(len(self.mgr.list_sessions()), 2)
        self.assertEqual(self.mgr.active_session_id, s2.id)

        # Delete inactive session s1
        res = self.mgr.delete_session(s1.id)
        self.assertTrue(res)
        sessions = self.mgr.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, s2.id)

    def test_delete_active_session_starts_new(self):
        """Verify deleting the active chat starts a clean new chat."""
        s1 = self.mgr.get_active_session()
        self.mgr.add_message("user", "active message")
        deleted_id = s1.id

        self.mgr.delete_session(deleted_id)
        new_active = self.mgr.get_active_session()
        self.assertNotEqual(new_active.id, deleted_id)
        self.assertEqual(len(new_active.messages), 0)
        self.assertEqual(new_active.title, "New Chat")

    def test_clear_active_session(self):
        """Verify clearing active session memory buffer."""
        self.mgr.add_message("user", "test 1")
        self.mgr.add_message("assistant", "test 2")
        self.assertEqual(len(self.mgr.get_active_session().messages), 2)

        self.mgr.clear_active_session()
        self.assertEqual(len(self.mgr.get_active_session().messages), 0)


class MockAIClient:
    def __init__(self):
        self._history = []
        self.backend = "xdna2"
        self.model_name = "gemma3:4b"

    def is_connected(self):
        return True

    def clear_history(self):
        self._history.clear()

    def set_history(self, messages):
        self._history = list(messages)

    def get_history(self):
        return list(self._history)

    def send(self, msg, on_token, on_done, on_error):
        on_token("Echo: " + msg)
        on_done("Echo: " + msg)


class TestChatWindowUI(unittest.TestCase):
    """UI smoke tests for ChatWindow drawer, header toggle, inline rename, and inline delete."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.storage_file = Path(self.temp_dir) / "chats.json"
        self.session_mgr = ChatSessionManager(storage_path=self.storage_file)

        self.ai = MockAIClient()
        self.ctrl = MagicMock()
        self.parent_win = MagicMock()
        self.parent_win.pos.return_value = MagicMock(x=lambda: 500, y=lambda: 200)
        self.parent_win.width.return_value = 200
        self.parent_win.height.return_value = 200

        self.win = ChatWindow(
            ai_client=self.ai,
            character_controller=self.ctrl,
            parent_window=self.parent_win,
            session_manager=self.session_mgr,
        )
        self.win.show()

    def tearDown(self):
        self.win.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_header_title_toggles_drawer(self):
        """Clicking header title toggles the recent chats drawer and changes arrow indicator."""
        # Initially drawer is closed
        self.assertFalse(self.win._drawer.isVisible())
        self.assertTrue(self.win._log.isVisible())
        self.assertIn("▾", self.win._header_title_btn.text())

        # Click to open drawer
        self.win._header_title_btn.click()
        self.assertTrue(self.win._drawer.isVisible())
        self.assertFalse(self.win._log.isVisible())
        self.assertIn("▴", self.win._header_title_btn.text())

        # Click to close drawer
        self.win._header_title_btn.click()
        self.assertFalse(self.win._drawer.isVisible())
        self.assertTrue(self.win._log.isVisible())
        self.assertIn("▾", self.win._header_title_btn.text())

    def test_new_chat_button(self):
        """Clicking [+ NEW CHAT] creates a new session, switches active, and closes drawer."""
        self.win._session_mgr.add_message("user", "Old conversation")
        old_id = self.win._session_mgr.active_session_id

        # Open drawer and click new chat
        self.win._header_title_btn.click()
        self.assertTrue(self.win._drawer.isVisible())

        self.win._new_chat_btn.click()

        # Drawer should close, log visible, and active session should be new
        self.assertFalse(self.win._drawer.isVisible())
        self.assertTrue(self.win._log.isVisible())
        self.assertNotEqual(self.win._session_mgr.active_session_id, old_id)
        self.assertEqual(len(self.win._session_mgr.get_active_session().messages), 0)

    def test_inline_rename(self):
        """Clicking pencil (✎), changing title, and clicking checkmark (✓) updates title in JSON."""
        self.win._header_title_btn.click()
        rows = [
            self.win._drawer_list_layout.itemAt(i).widget()
            for i in range(self.win._drawer_list_layout.count() - 1)
        ]
        self.assertGreaterEqual(len(rows), 1)
        row: ChatSessionRow = rows[0]

        # Click pencil edit button
        row._edit_btn.click()
        self.assertTrue(row._edit_frame.isVisible())
        self.assertFalse(row._view_frame.isVisible())

        # Change title text and click save (✓)
        row._edit_input.setText("Cyberpunk Neural Link")
        row._save_btn.click()

        # Row should return to view mode with updated title
        self.assertTrue(row._view_frame.isVisible())
        self.assertFalse(row._edit_frame.isVisible())
        self.assertIn("Cyberpunk Neural Link", row.session.title)

        # Verify persisted to JSON
        fresh = ChatSessionManager(storage_path=self.storage_file)
        self.assertEqual(fresh.get_active_session().title, "Cyberpunk Neural Link")

    def test_inline_delete_cancel(self):
        """Clicking (✕) shows inline 'delete the chat?', clicking (✕) cancels without deleting."""
        self.win._header_title_btn.click()
        rows = [
            self.win._drawer_list_layout.itemAt(i).widget()
            for i in range(self.win._drawer_list_layout.count() - 1)
        ]
        row: ChatSessionRow = rows[0]
        initial_count = len(self.win._session_mgr.list_sessions())

        # Click delete button (✕)
        row._delete_btn.click()
        self.assertTrue(row._delete_confirm_frame.isVisible())
        self.assertFalse(row._view_frame.isVisible())

        # Click cancel (✕)
        row._cancel_del_btn.click()
        self.assertTrue(row._view_frame.isVisible())
        self.assertFalse(row._delete_confirm_frame.isVisible())
        self.assertEqual(len(self.win._session_mgr.list_sessions()), initial_count)

    def test_inline_delete_confirm(self):
        """Clicking (✕) shows inline confirmation, clicking (✓) removes session from JSON."""
        # Open drawer and create second session so we have two
        self.win._open_drawer()
        s2 = self.win._session_mgr.new_session(title="Chat To Delete")
        self.win._refresh_drawer()

        rows = [
            self.win._drawer_list_layout.itemAt(i).widget()
            for i in range(self.win._drawer_list_layout.count() - 1)
        ]
        # Find row for s2
        row_s2 = next(r for r in rows if r.session.id == s2.id)

        # Click delete button on s2
        row_s2._delete_btn.click()
        self.assertTrue(row_s2._delete_confirm_frame.isVisible())

        # Click confirm (✓)
        row_s2._confirm_del_btn.click()

        # Check session removed from manager and storage file
        remaining_ids = [s.id for s in self.win._session_mgr.list_sessions()]
        self.assertNotIn(s2.id, remaining_ids)

        fresh = ChatSessionManager(storage_path=self.storage_file)
        self.assertNotIn(s2.id, [s.id for s in fresh.list_sessions()])


    def test_switch_session_updates_log_and_ai(self):
        """Switching session loads its messages into log and AI history."""
        # Session 1
        s1 = self.win._session_mgr.get_active_session()
        self.win._session_mgr.add_message("user", "What is AMD XDNA2?")
        self.win._session_mgr.add_message("assistant", "AMD XDNA2 is an NPU architecture.")

        # Create Session 2
        s2 = self.win._session_mgr.new_session(title="Second Topic")
        self.win._session_mgr.add_message("user", "Hello Session 2")
        self.win._session_mgr.add_message("assistant", "Welcome to Session 2")

        # Now switch back to Session 1 via drawer selection
        self.win._on_session_selected(s1.id)
        self.assertEqual(self.win._session_mgr.active_session_id, s1.id)

        # AI history should match Session 1
        ai_hist = self.win._ai.get_history()
        self.assertEqual(len(ai_hist), 2)
        self.assertEqual(ai_hist[0]["content"], "What is AMD XDNA2?")

        # Log should contain Session 1 text
        log_html = self.win._log.toHtml()
        self.assertIn("What is AMD XDNA2?", log_html)
        self.assertIn("AMD XDNA2 is an NPU architecture.", log_html)

    def test_clear_slash_command_flushes_session(self):
        """Executing /clear flushes session messages in manager and on disk."""
        self.win._session_mgr.add_message("user", "Clear me please")
        self.assertEqual(len(self.win._session_mgr.get_active_session().messages), 1)

        self.win._input.setText("/clear")
        self.win._on_send()

        self.assertEqual(len(self.win._session_mgr.get_active_session().messages), 0)
        fresh = ChatSessionManager(storage_path=self.storage_file)
        self.assertEqual(len(fresh.get_active_session().messages), 0)

    def test_escape_key_closes_drawer(self):
        """Pressing Escape key closes the drawer if open."""
        from PySide6.QtGui import QKeyEvent
        self.win._open_drawer()
        self.assertTrue(self.win._drawer.isVisible())

        # Simulate Escape keypress
        esc_event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        self.win.keyPressEvent(esc_event)

        self.assertFalse(self.win._drawer.isVisible())
        self.assertTrue(self.win._log.isVisible())


if __name__ == "__main__":
    unittest.main()
