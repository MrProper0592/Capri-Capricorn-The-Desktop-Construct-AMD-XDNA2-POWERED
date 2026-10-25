"""
chat_window.py — Cyberpunk terminal UI for Capri with AMD XDNA2 NPU telemetry,
slash commands (/npu, /status, /model, /clear, /reset, /help), and voice PTT/TTS.
"""

import threading
from typing import Callable, Any
from PySide6.QtCore    import Qt, QTimer, Signal, QObject, QEvent
from PySide6.QtGui     import QColor, QTextCursor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QLabel, QPushButton,
    QApplication, QSizePolicy, QFrame, QScrollArea,
)

from capri.desktop.wm import apply_pin_on_top
from capri.desktop.session_manager import (
    ChatSession,
    ChatSessionManager,
    format_session_time,
)

C_BG        = "#08080f"
C_BORDER    = "#00e5ff"
C_ACCENT    = "#f5e642"
C_PINK      = "#ff2d78"
C_TEXT      = "#c8f0f5"
C_DIM       = "#3a5a60"
C_GREEN     = "#39ff14"
C_USER      = "#f5e642"
C_CAPRI     = "#00e5ff"
C_MIC_LIVE  = "#ff2d78"

FONT_FAMILY = "Consolas, Courier New, monospace"
FONT_SIZE   = 11

WINDOW_STYLE = f"""
QWidget#ChatPanel {{
    background: {C_BG};
    border: 2px solid {C_BORDER};
}}
QScrollBar:vertical {{
    background: {C_BG};
    width: 6px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {C_DIM};
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""
LOG_STYLE = f"""
QTextEdit {{
    background: {C_BG};
    color: {C_TEXT};
    border: none;
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE}px;
    padding: 8px;
    selection-background-color: {C_DIM};
}}
"""
INPUT_STYLE = f"""
QLineEdit {{
    background: {C_BG};
    color: {C_ACCENT};
    border: none;
    border-top: 1px solid {C_DIM};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE}px;
    padding: 8px 8px 8px 4px;
}}
QLineEdit:focus {{
    border-top: 1px solid {C_BORDER};
}}
"""
HEADER_STYLE = f"""
QWidget#Header {{
    background: {C_BG};
    border-bottom: 1px solid {C_DIM};
}}
QLabel {{
    color: {C_ACCENT};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE}px;
    font-weight: bold;
    background: transparent;
}}
"""


ROW_STYLE = f"""
QFrame#SessionRow {{
    background: #0d0d1a;
    border: 1px solid #141426;
    border-radius: 2px;
}}
QFrame#SessionRow:hover {{
    border: 1px solid rgba(0, 229, 255, 0.4);
    background: #111124;
}}
QFrame#SessionRow[active="true"] {{
    background: rgba(0, 229, 255, 0.08);
    border: 1px solid {C_BORDER};
    border-left: 3px solid {C_BORDER};
}}
"""


class ChatSessionRow(QFrame):
    """
    Cyberpunk row displaying a chat session in the recent chats drawer.
    Supports inline renaming (✎) and inline delete confirmation (✕ -> delete the chat? ✓ / ✕).
    """

    def __init__(
        self,
        session: ChatSession,
        is_active: bool,
        on_select: Callable[[str], None],
        on_rename: Callable[[str, str], None],
        on_delete: Callable[[str], None],
    ):
        super().__init__()
        self.session = session
        self.is_active = is_active
        self.on_select = on_select
        self.on_rename = on_rename
        self.on_delete = on_delete

        self.setObjectName("SessionRow")
        self.setProperty("active", "true" if is_active else "false")
        self.setFixedHeight(32)

        self._setup_ui()

    def _setup_ui(self):
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(6, 2, 6, 2)
        main_lay.setSpacing(0)

        # ── 1. View Mode ──
        self._view_frame = QWidget()
        v_lay = QHBoxLayout(self._view_frame)
        v_lay.setContentsMargins(0, 0, 0, 0)
        v_lay.setSpacing(6)

        # Rename button (✎) on the left of chat name
        self._edit_btn = QPushButton("✎")
        self._edit_btn.setFixedSize(18, 18)
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.setToolTip("Rename chat")
        self._edit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_DIM};
                border: none;
                font-size: 11px;
                font-family: {FONT_FAMILY};
                padding: 0;
            }}
            QPushButton:hover {{
                color: {C_BORDER};
            }}
        """)
        self._edit_btn.clicked.connect(self._show_edit_mode)

        # Chat Title Button
        display_title = self._truncate(self.session.title, 24)
        prefix = "▸ " if self.is_active else ""
        self._title_btn = QPushButton(f"{prefix}{display_title}")
        self._title_btn.setCursor(Qt.PointingHandCursor)
        self._title_btn.setToolTip(self.session.title)
        self._title_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        title_color = C_ACCENT if self.is_active else C_TEXT
        font_weight = "bold" if self.is_active else "normal"
        self._title_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                text-align: left;
                color: {title_color};
                font-family: {FONT_FAMILY};
                font-size: 11px;
                font-weight: {font_weight};
                padding: 0;
            }}
            QPushButton:hover {{
                color: #ffffff;
            }}
        """)
        self._title_btn.clicked.connect(self._on_title_clicked)

        # Timestamp label
        time_str = format_session_time(self.session.updated_at)
        self._time_lbl = QLabel(time_str)
        self._time_lbl.setStyleSheet(f"color: {C_DIM}; font-family: {FONT_FAMILY}; font-size: 9px;")

        # Delete button (✕) on the right
        self._delete_btn = QPushButton("✕")
        self._delete_btn.setFixedSize(18, 18)
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setToolTip("Delete chat")
        self._delete_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_DIM};
                border: none;
                font-size: 11px;
                font-family: {FONT_FAMILY};
                padding: 0;
            }}
            QPushButton:hover {{
                color: {C_PINK};
            }}
        """)
        self._delete_btn.clicked.connect(self._show_delete_confirm_mode)

        v_lay.addWidget(self._edit_btn)
        v_lay.addWidget(self._title_btn)
        v_lay.addWidget(self._time_lbl)
        v_lay.addWidget(self._delete_btn)

        # ── 2. Inline Edit Mode ──
        self._edit_frame = QWidget()
        e_lay = QHBoxLayout(self._edit_frame)
        e_lay.setContentsMargins(0, 0, 0, 0)
        e_lay.setSpacing(4)

        self._edit_input = QLineEdit(self.session.title)
        self._edit_input.setFixedHeight(22)
        self._edit_input.setStyleSheet(f"""
            QLineEdit {{
                background: #08080f;
                color: {C_ACCENT};
                border: 1px solid {C_BORDER};
                font-family: {FONT_FAMILY};
                font-size: 10px;
                padding: 1px 4px;
            }}
        """)
        self._edit_input.returnPressed.connect(self._save_edit)
        self._edit_input.installEventFilter(self)

        self._save_btn = QPushButton("✓")
        self._save_btn.setFixedSize(20, 20)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setToolTip("Save (Enter)")
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(57, 255, 20, 0.15);
                color: {C_GREEN};
                border: 1px solid {C_GREEN};
                font-size: 11px;
                font-weight: bold;
                border-radius: 2px;
            }}
            QPushButton:hover {{
                background: {C_GREEN};
                color: #000000;
            }}
        """)
        self._save_btn.clicked.connect(self._save_edit)

        self._cancel_edit_btn = QPushButton("✕")
        self._cancel_edit_btn.setFixedSize(20, 20)
        self._cancel_edit_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_edit_btn.setToolTip("Cancel (Esc)")
        self._cancel_edit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_DIM};
                border: 1px solid {C_DIM};
                font-size: 11px;
                border-radius: 2px;
            }}
            QPushButton:hover {{
                color: {C_PINK};
                border-color: {C_PINK};
            }}
        """)
        self._cancel_edit_btn.clicked.connect(self._show_view_mode)

        e_lay.addWidget(self._edit_input)
        e_lay.addWidget(self._save_btn)
        e_lay.addWidget(self._cancel_edit_btn)

        # ── 3. Inline Delete Confirmation Mode ──
        self._delete_confirm_frame = QWidget()
        d_lay = QHBoxLayout(self._delete_confirm_frame)
        d_lay.setContentsMargins(0, 0, 0, 0)
        d_lay.setSpacing(6)

        confirm_lbl = QLabel("delete the chat?")
        confirm_lbl.setStyleSheet(f"color: {C_PINK}; font-family: {FONT_FAMILY}; font-size: 10px; font-weight: bold;")

        self._confirm_del_btn = QPushButton("✓")
        self._confirm_del_btn.setFixedSize(20, 20)
        self._confirm_del_btn.setCursor(Qt.PointingHandCursor)
        self._confirm_del_btn.setToolTip("Confirm Delete")
        self._confirm_del_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 45, 120, 0.20);
                color: {C_PINK};
                border: 1px solid {C_PINK};
                font-size: 11px;
                font-weight: bold;
                border-radius: 2px;
            }}
            QPushButton:hover {{
                background: {C_PINK};
                color: #000000;
            }}
        """)
        self._confirm_del_btn.clicked.connect(self._confirm_delete)

        self._cancel_del_btn = QPushButton("✕")
        self._cancel_del_btn.setFixedSize(20, 20)
        self._cancel_del_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_del_btn.setToolTip("Cancel")
        self._cancel_del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_DIM};
                border: 1px solid {C_DIM};
                font-size: 11px;
                border-radius: 2px;
            }}
            QPushButton:hover {{
                color: {C_TEXT};
                border-color: {C_TEXT};
            }}
        """)
        self._cancel_del_btn.clicked.connect(self._show_view_mode)

        d_lay.addWidget(confirm_lbl)
        d_lay.addStretch()
        d_lay.addWidget(self._confirm_del_btn)
        d_lay.addWidget(self._cancel_del_btn)

        main_lay.addWidget(self._view_frame)
        main_lay.addWidget(self._edit_frame)
        main_lay.addWidget(self._delete_confirm_frame)

        self._show_view_mode()

    def _show_view_mode(self):
        self._view_frame.setVisible(True)
        self._edit_frame.setVisible(False)
        self._delete_confirm_frame.setVisible(False)

    def _show_edit_mode(self):
        self._view_frame.setVisible(False)
        self._delete_confirm_frame.setVisible(False)
        self._edit_frame.setVisible(True)
        self._edit_input.setText(self.session.title)
        self._edit_input.setFocus()
        self._edit_input.selectAll()

    def _show_delete_confirm_mode(self):
        self._view_frame.setVisible(False)
        self._edit_frame.setVisible(False)
        self._delete_confirm_frame.setVisible(True)

    def _save_edit(self):
        text = self._edit_input.text().strip()
        if text:
            self.session.title = text
            self._title_btn.setText(("▸ " if self.is_active else "") + self._truncate(text, 24))
            self._title_btn.setToolTip(text)
            self.on_rename(self.session.id, text)
        self._show_view_mode()

    def _confirm_delete(self):
        self.on_delete(self.session.id)

    def _on_title_clicked(self):
        self.on_select(self.session.id)

    def eventFilter(self, obj, event):
        if obj is self._edit_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._show_view_mode()
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        clean = text.strip()
        if len(clean) > max_len:
            return clean[: max_len - 3] + "..."
        return clean


class _SignalBridge(QObject):
    append_token    = Signal(str)
    append_error    = Signal(str)
    set_status      = Signal(str, str)
    set_badge       = Signal(str, str)
    generation_done = Signal(str)
    set_mic_state   = Signal(bool)


class ChatWindow(QWidget):

    def __init__(self, ai_client, character_controller, parent_window,
                 stt=None, tts=None, ptt=None, session_manager=None, storage_path=None):
        super().__init__()
        self._ai       = ai_client
        self._ctrl     = character_controller
        self._parent   = parent_window
        self._stt      = stt
        self._tts      = tts
        self._ptt      = ptt
        self._thinking = False
        self._bridge   = _SignalBridge()

        self._session_mgr = session_manager or ChatSessionManager(storage_path=storage_path)

        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._check_connection()

        # Load active session history into terminal log and AI client
        active_sess = self._session_mgr.get_active_session()
        self._load_session_to_log(active_sess)

        if self._ptt:
            self._ptt._on_press   = self._ptt_pressed
            self._ptt._on_release = self._ptt_released
            self._ptt.start()

    def _setup_window(self):
        self.setWindowTitle("Capri // XDNA2 Terminal")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("ChatPanel")
        self.setStyleSheet(WINDOW_STYLE)
        self.setFixedSize(440, 520)
        self._reposition()

    def _reposition(self):
        if self._parent:
            p = self._parent.pos()
            screen = QApplication.primaryScreen().geometry()
            x = p.x() - self.width() - 10
            if x < 0:
                x = p.x() + self._parent.width() + 10
            y = p.y() - self.height() + self._parent.height()
            y = max(0, min(y, screen.height() - self.height()))
            self.move(x, y)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_header())
        self._drawer = self._make_drawer()
        root.addWidget(self._drawer)
        self._drawer.setVisible(False)
        root.addWidget(self._make_log())
        root.addWidget(self._make_divider())
        root.addWidget(self._make_input_bar())

    def _make_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        header.setStyleSheet(HEADER_STYLE)
        header.setFixedHeight(34)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(4)

        # Clickable header dropdown button for recent chats
        self._header_title_btn = QPushButton("CAPRI // XDNA2 TERMINAL ▾")
        self._header_title_btn.setObjectName("HeaderTitleBtn")
        self._header_title_btn.setCursor(Qt.PointingHandCursor)
        self._header_title_btn.setToolTip("Toggle Recent Chats")
        self._header_title_btn.setStyleSheet(f"""
            QPushButton#HeaderTitleBtn {{
                color: {C_ACCENT};
                font-family: {FONT_FAMILY};
                font-size: 11px;
                font-weight: bold;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 1px 4px;
                text-align: left;
            }}
            QPushButton#HeaderTitleBtn:hover {{
                color: #ffffff;
                background: rgba(0, 229, 255, 0.12);
                border: 1px solid {C_BORDER};
            }}
            QPushButton#HeaderTitleBtn:pressed {{
                background: rgba(0, 229, 255, 0.25);
            }}
        """)
        self._header_title_btn.clicked.connect(self._toggle_drawer)

        self._npu_badge = QLabel("XDNA2 [aie2p]")
        self._npu_badge.setStyleSheet(
            f"color: {C_BORDER}; border: 1px solid {C_BORDER}; "
            f"font-size: 9px; font-family: {FONT_FAMILY}; padding: 1px 4px;"
        )

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {C_DIM}; font-size: 14px;")

        self._status_label = QLabel("CONNECTING")
        self._status_label.setStyleSheet(f"color: {C_DIM}; font-size: 10px;")

        self._mic_label = QLabel("● MIC")
        self._mic_label.setStyleSheet(
            f"color: {C_MIC_LIVE}; font-size: 10px; font-family: {FONT_FAMILY};"
        )
        self._mic_label.setVisible(False)

        self._stop_btn = QPushButton("■ STOP")
        self._stop_btn.setFixedHeight(18)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C_PINK};
                border: 1px solid {C_PINK}; font-size: 9px;
                font-family: {FONT_FAMILY}; padding: 0 4px;
            }}
            QPushButton:hover {{ background: {C_PINK}; color: #000; }}
        """)
        self._stop_btn.setVisible(False)
        self._stop_btn.clicked.connect(self._stop_speech)

        self._pin_btn = QPushButton("📌")
        self._pin_btn.setFixedSize(20, 20)
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setToolTip("Toggle Always on Top")
        self._pin_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C_DIM};
                border: none; font-size: 11px; font-family: {FONT_FAMILY};
            }}
            QPushButton:checked {{ color: {C_ACCENT}; }}
            QPushButton:hover {{ color: {C_BORDER}; }}
        """)
        self._pin_btn.toggled.connect(self._on_pin_btn_toggled)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C_DIM};
                border: none; font-size: 12px; font-family: {FONT_FAMILY};
            }}
            QPushButton:hover {{ color: {C_PINK}; }}
        """)
        close_btn.clicked.connect(self.hide)

        lay.addWidget(self._header_title_btn)
        lay.addSpacing(4)
        lay.addWidget(self._npu_badge)
        lay.addStretch()
        lay.addWidget(self._mic_label)
        lay.addSpacing(4)
        lay.addWidget(self._stop_btn)
        lay.addSpacing(4)
        lay.addWidget(self._status_dot)
        lay.addWidget(self._status_label)
        lay.addSpacing(4)
        lay.addWidget(self._pin_btn)
        lay.addSpacing(2)
        lay.addWidget(close_btn)
        return header

    def _make_drawer(self) -> QWidget:
        drawer = QWidget()
        drawer.setObjectName("RecentChatsDrawer")
        drawer.setStyleSheet(f"""
            QWidget#RecentChatsDrawer {{
                background: #0d0d1a;
                border-bottom: 1px solid {C_DIM};
            }}
            {ROW_STYLE}
        """)

        d_lay = QVBoxLayout(drawer)
        d_lay.setContentsMargins(0, 0, 0, 0)
        d_lay.setSpacing(0)

        # Top bar
        top_bar = QWidget()
        top_bar.setFixedHeight(30)
        top_lay = QHBoxLayout(top_bar)
        top_lay.setContentsMargins(10, 4, 10, 4)

        title_lbl = QLabel("// RECENT SESSIONS")
        title_lbl.setStyleSheet(f"color: {C_BORDER}; font-family: {FONT_FAMILY}; font-size: 10px; font-weight: bold;")

        self._new_chat_btn = QPushButton("+ NEW CHAT")
        self._new_chat_btn.setFixedHeight(20)
        self._new_chat_btn.setCursor(Qt.PointingHandCursor)
        self._new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C_BORDER};
                border: 1px solid {C_BORDER};
                font-family: {FONT_FAMILY};
                font-size: 10px;
                font-weight: bold;
                padding: 0 8px;
                border-radius: 2px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.20);
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background: rgba(0, 229, 255, 0.35);
            }}
        """)
        self._new_chat_btn.clicked.connect(self._on_new_chat_clicked)

        top_lay.addWidget(title_lbl)
        top_lay.addStretch()
        top_lay.addWidget(self._new_chat_btn)

        # Divider line
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background: {C_DIM}; max-height: 1px; border: none;")

        # Scroll Area for session rows
        scroll = QScrollArea()
        scroll.setObjectName("DrawerScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {C_BG};
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C_DIM};
                min-height: 20px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {C_BORDER};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        self._drawer_list_layout = QVBoxLayout(scroll_content)
        self._drawer_list_layout.setContentsMargins(6, 6, 6, 6)
        self._drawer_list_layout.setSpacing(4)
        self._drawer_list_layout.addStretch()

        scroll.setWidget(scroll_content)

        d_lay.addWidget(top_bar)
        d_lay.addWidget(sep)
        d_lay.addWidget(scroll)

        return drawer

    def _toggle_drawer(self):
        if not self._drawer.isHidden():
            self._close_drawer()
        else:
            self._open_drawer()

    def _open_drawer(self):
        self._refresh_drawer()
        self._log.setVisible(False)
        self._drawer.setVisible(True)
        self._header_title_btn.setText("CAPRI // XDNA2 TERMINAL ▴")

    def _close_drawer(self):
        self._drawer.setVisible(False)
        self._log.setVisible(True)
        self._header_title_btn.setText("CAPRI // XDNA2 TERMINAL ▾")

    def _refresh_drawer(self):
        # Remove old rows (keep the stretch at the end)
        while self._drawer_list_layout.count() > 1:
            item = self._drawer_list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        active_id = self._session_mgr.active_session_id
        sessions = self._session_mgr.list_sessions()
        for s in sessions:
            row = ChatSessionRow(
                session=s,
                is_active=(s.id == active_id),
                on_select=self._on_session_selected,
                on_rename=self._on_session_renamed,
                on_delete=self._on_session_deleted,
            )
            insert_pos = max(0, self._drawer_list_layout.count() - 1)
            self._drawer_list_layout.insertWidget(insert_pos, row)
            row.show()

    def _on_session_selected(self, session_id: str):
        session = self._session_mgr.set_active_session(session_id)
        if session:
            self._load_session_to_log(session)
        self._close_drawer()
        self._input.setFocus()

    def _on_session_renamed(self, session_id: str, new_title: str):
        self._session_mgr.rename_session(session_id, new_title)

    def _on_session_deleted(self, session_id: str):
        was_active = (session_id == self._session_mgr.active_session_id)
        self._session_mgr.delete_session(session_id)
        if was_active:
            if hasattr(self._ai, "clear_history") and callable(self._ai.clear_history):
                self._ai.clear_history()
            self._load_session_to_log(self._session_mgr.get_active_session())
        self._refresh_drawer()

    def _on_new_chat_clicked(self):
        session = self._session_mgr.new_session()
        if hasattr(self._ai, "clear_history") and callable(self._ai.clear_history):
            self._ai.clear_history()
        self._load_session_to_log(session)
        self._close_drawer()
        self._input.setFocus()

    def _load_session_to_log(self, session: ChatSession):
        self._log.clear()
        hint = " | hold L-CTRL to speak" if self._ptt else ""
        if not session.messages:
            self._append_system(
                f"AMD XDNA2 SESSION READY — type a message or /help{hint}."
            )
        else:
            title_text = session.title or "ACTIVE SESSION"
            self._append_system(f"SESSION: {title_text}{hint}")
            for msg in session.messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "user":
                    self._append_user(content)
                elif role == "assistant":
                    self._append_capri(content)
                elif role == "system":
                    self._append_system(content)
        if hasattr(self._ai, "set_history") and callable(self._ai.set_history):
            self._ai.set_history(session.messages)

    def _on_pin_btn_toggled(self, checked: bool) -> None:
        if self._parent and hasattr(self._parent, "_toggle_pin_on_top"):
            self._parent._toggle_pin_on_top(checked)
        else:
            apply_pin_on_top(self, checked, delayed=False)

    def set_pinned(self, pinned: bool) -> None:
        """Update pin button state without firing toggled signals."""
        if hasattr(self, "_pin_btn") and self._pin_btn:
            self._pin_btn.blockSignals(True)
            self._pin_btn.setChecked(pinned)
            self._pin_btn.blockSignals(False)

    def _make_log(self) -> QTextEdit:
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(LOG_STYLE)
        self._log.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return self._log

    def _make_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {C_DIM}; background: {C_DIM}; max-height: 1px;")
        return line

    def _make_input_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background: {C_BG};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)
        prompt = QLabel(">_")
        prompt.setStyleSheet(f"color: {C_BORDER}; font-family: {FONT_FAMILY}; font-size: 13px;")
        self._input = QLineEdit()
        self._input.setStyleSheet(INPUT_STYLE)
        self._input.setPlaceholderText("talk to capri... (/npu, /model, /clear, /help)")
        self._input.returnPressed.connect(self._on_send)
        lay.addWidget(prompt)
        lay.addWidget(self._input)
        return bar

    def _connect_signals(self):
        self._bridge.append_token.connect(self._on_token)
        self._bridge.append_error.connect(self._on_error)
        self._bridge.set_status.connect(self._set_status)
        self._bridge.set_badge.connect(self._set_badge)
        self._bridge.generation_done.connect(self._on_generation_done)
        self._bridge.set_mic_state.connect(self._on_mic_state_changed)

    # ── NPU status & badge helpers ────────────────────────────────────

    def _compute_badge_text(self, connected: bool = True) -> str:
        if not connected:
            return "OFFLINE"
        backend = (
            getattr(self._ai, "active_backend", None)
            or getattr(self._ai, "backend", None)
            or "XDNA2"
        )
        backend_up = str(backend).upper()
        model = (
            getattr(self._ai, "model", None)
            or getattr(self._ai, "model_name", None)
            or ""
        )
        if "XDNA" in backend_up or "FLM" in backend_up or "AUTO" in backend_up:
            return f"XDNA2 [aie2p]" if not model else f"XDNA2 [{model}]"
        if model:
            return f"{backend_up} [{model}]"
        return f"{backend_up}"

    def _compute_ready_status(self) -> str:
        metrics = getattr(self._ai, "last_metrics", None)
        tok_s = None
        backend = None
        if isinstance(metrics, dict):
            tok_s = metrics.get("tok_per_sec") or metrics.get("tokens_per_sec")
            backend = metrics.get("backend")
        elif metrics is not None:
            tok_s = getattr(metrics, "tok_per_sec", None) or getattr(metrics, "tokens_per_sec", None)
            backend = getattr(metrics, "backend", None)

        if not backend:
            backend = (
                getattr(self._ai, "active_backend", None)
                or getattr(self._ai, "backend", None)
                or "XDNA2"
            )
        label = "XDNA2" if str(backend).lower() in ("xdna2", "flm", "auto") else str(backend).upper()
        if tok_s and float(tok_s) > 0:
            return f"{label} • {float(tok_s):.1f} tok/s"
        return f"{label} READY"

    def _set_badge(self, text: str, colour: str):
        self._npu_badge.setText(text)
        self._npu_badge.setStyleSheet(
            f"color: {colour}; border: 1px solid {colour}; "
            f"font-size: 9px; font-family: {FONT_FAMILY}; padding: 1px 4px;"
        )

    # ── PTT ───────────────────────────────────────────────────────────

    def _ptt_pressed(self):
        if self._thinking or not self._stt:
            return
        if not self._stt.is_ready():
            self._bridge.append_error.emit("// STT still loading — wait a moment.")
            return
        if self._tts:
            self._tts.stop()
        self._stt.start_recording()
        self._bridge.set_mic_state.emit(True)

    def _ptt_released(self):
        if not self._stt:
            return
        self._bridge.set_mic_state.emit(False)
        text = self._stt.stop_and_transcribe()
        if text:
            self._bridge.append_token.emit("\x00__VOICE__:" + text)
        else:
            self._bridge.append_error.emit("// Nothing heard — try again.")

    # ── Terminal Slash Commands ───────────────────────────────────────

    def show_npu_status(self):
        """Public helper so CapriWindow context menu or /npu command can display telemetry."""
        if hasattr(self._ai, "get_status_report") and callable(self._ai.get_status_report):
            try:
                report = self._ai.get_status_report()
            except Exception as e:
                report = f"TELEMETRY ERROR: {e}"
        else:
            ok = self._ai.is_connected() if hasattr(self._ai, "is_connected") else False
            backend = getattr(self._ai, "backend", "xdna2")
            model = getattr(self._ai, "model", getattr(self._ai, "model_name", "gemma3:4b"))
            report = (
                f"AMD XDNA2 NPU TELEMETRY\n"
                f"  Backend : {backend}\n"
                f"  Model   : {model}\n"
                f"  Status  : {'ONLINE' if ok else 'OFFLINE'}"
            )
        self._append_telemetry(str(report))
        self._check_connection()

    def clear_memory(self):
        """Public helper to flush conversation history."""
        if hasattr(self._ai, "clear_history") and callable(self._ai.clear_history):
            self._ai.clear_history()
        if hasattr(self, "_session_mgr") and self._session_mgr:
            self._session_mgr.clear_active_session()
        self._log.clear()
        self._append_system("MEMORY BUFFER FLUSHED")

    def _handle_slash_command(self, text: str) -> bool:
        """Returns True if `text` was handled as a terminal slash command."""
        if not text.startswith("/"):
            return False

        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/npu", "/status"):
            self._append_user(text)
            self.show_npu_status()
            return True

        if cmd in ("/clear", "/reset"):
            self._append_user(text)
            self.clear_memory()
            return True

        if cmd == "/model":
            self._append_user(text)
            if not arg:
                cur_model = getattr(self._ai, "model", getattr(self._ai, "model_name", "unknown"))
                self._append_system(f"ACTIVE MODEL: {cur_model} — usage: /model <tag>")
                return True
            if hasattr(self._ai, "set_model") and callable(self._ai.set_model):
                res = self._ai.set_model(arg)
                msg = res if isinstance(res, str) and res else f"ACTIVE MODEL SWITCHED TO: {arg}"
                self._append_system(msg)
                self._set_badge(self._compute_badge_text(True), C_BORDER)
            else:
                if hasattr(self._ai, "model"):
                    self._ai.model = arg
                self._append_system(f"ACTIVE MODEL SET TO: {arg}")
                self._set_badge(self._compute_badge_text(True), C_BORDER)
            return True

        if cmd == "/help":
            self._append_user(text)
            help_text = (
                "AVAILABLE XDNA2 TERMINAL COMMANDS:\n"
                "  /npu, /status   — Display live AMD XDNA2 NPU hardware & runtime telemetry\n"
                "  /model <tag>    — Switch active inference model (e.g. /model gemma3:4b)\n"
                "  /clear, /reset  — Flush conversation memory buffer\n"
                "  /help           — Show this command reference"
            )
            self._append_telemetry(help_text)
            return True

        self._append_user(text)
        self._append_system(f"UNKNOWN COMMAND '{cmd}' — type /help for available commands.")
        return True

    # ── send ──────────────────────────────────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        if not text or self._thinking:
            return
        self._input.clear()
        if self._handle_slash_command(text):
            return
        self._dispatch_message(text)

    def _dispatch_message(self, text: str):
        self._append_user(text)
        if hasattr(self, "_session_mgr") and self._session_mgr:
            self._session_mgr.add_message("user", text)
        self._start_thinking()
        self._ai.send(
            text,
            on_token=lambda chunk: self._bridge.append_token.emit(chunk),
            on_done=lambda full: self._bridge.generation_done.emit(full),
            on_error=lambda msg: (
                self._bridge.append_error.emit(msg),
                self._bridge.generation_done.emit(""),
            ),
        )

    def _start_thinking(self):
        self._thinking = True
        self._input.setEnabled(False)
        self._bridge.set_status.emit("NPU INFER", C_PINK)
        from capri.character.controller import CapriState
        self._ctrl.set_state(CapriState.TALKING)
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<span style="color:{C_CAPRI}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;"><br>CAPRI &gt;&nbsp;</span>'
        )
        self._log.moveCursor(QTextCursor.End)
        self._capri_line_open = True

    def _on_token(self, chunk: str):
        if chunk.startswith("\x00__VOICE__:"):
            self._dispatch_message(chunk[len("\x00__VOICE__:"):])
            return
        self._log.moveCursor(QTextCursor.End)
        self._log.insertPlainText(chunk)
        self._log.moveCursor(QTextCursor.End)

    def _on_error(self, message: str):
        escaped = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<br><span style="color:{C_PINK}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;">{escaped}</span><br>'
        )
        self._log.moveCursor(QTextCursor.End)
        self._set_status("OFFLINE", C_PINK)
        self._set_badge("OFFLINE", C_PINK)

    def _on_generation_done(self, full_reply: str):
        self._thinking = False
        self._input.setEnabled(True)
        self._input.setFocus()
        self._log.insertHtml("<br>")
        self._log.moveCursor(QTextCursor.End)
        if full_reply:
            if hasattr(self, "_session_mgr") and self._session_mgr:
                self._session_mgr.add_message("assistant", full_reply)
            self._set_status(self._compute_ready_status(), C_GREEN)
            self._set_badge(self._compute_badge_text(True), C_BORDER)
        from capri.character.controller import CapriState
        self._ctrl.set_state(CapriState.IDLE)
        if self._tts and full_reply.strip():
            self._stop_btn.setVisible(True)
            self._tts.speak(
                full_reply,
                on_done=lambda: self._bridge.set_mic_state.emit(False),
            )

    def _stop_speech(self):
        if self._tts:
            self._tts.stop()
        self._stop_btn.setVisible(False)

    def _on_mic_state_changed(self, is_live: bool):
        self._mic_label.setVisible(is_live)
        if not is_live and self._tts and not self._tts.is_speaking():
            self._stop_btn.setVisible(False)

    # ── log helpers ───────────────────────────────────────────────────

    def _append_user(self, text: str):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<span style="color:{C_USER}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;"><br>&gt; {escaped}<br></span>'
        )
        self._log.moveCursor(QTextCursor.End)

    def _append_capri(self, text: str):
        escaped = (text.replace("&", "&amp;").replace("<", "&lt;")
                       .replace(">", "&gt;").replace("\n", "<br>"))
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<span style="color:{C_CAPRI}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;"><br>CAPRI &gt;&nbsp;</span>'
            f'<span style="color:{C_TEXT}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px; white-space: pre-wrap;">{escaped}<br></span>'
        )
        self._log.moveCursor(QTextCursor.End)

    def _append_system(self, text: str):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<span style="color:{C_DIM}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;"><br>// {escaped}<br></span>'
        )
        self._log.moveCursor(QTextCursor.End)

    def _append_telemetry(self, text: str):
        escaped = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace(" ", "&nbsp;")
                .replace("\n", "<br>")
        )
        self._log.moveCursor(QTextCursor.End)
        self._log.insertHtml(
            f'<span style="color:{C_BORDER}; font-family:{FONT_FAMILY}; '
            f'font-size:{FONT_SIZE}px;"><br>{escaped}<br></span>'
        )
        self._log.moveCursor(QTextCursor.End)

    def _set_status(self, text: str, colour: str):
        self._status_dot.setStyleSheet(f"color: {colour}; font-size: 14px;")
        self._status_label.setStyleSheet(
            f"color: {colour}; font-size: 10px; font-family: {FONT_FAMILY};"
        )
        self._status_label.setText(text)

    def _check_connection(self):
        def _ping():
            ok = self._ai.is_connected()
            status_txt = self._compute_ready_status() if ok else "OFFLINE"
            badge_txt = self._compute_badge_text(ok)
            self._bridge.set_status.emit(
                status_txt,
                C_GREEN if ok else C_PINK,
            )
            self._bridge.set_badge.emit(
                badge_txt,
                C_BORDER if ok else C_PINK,
            )
        threading.Thread(target=_ping, daemon=True).start()

    def showEvent(self, event):
        self._reposition()
        self._input.setFocus()
        super().showEvent(event)
        parent_pinned = getattr(self._parent, "_pinned_on_top", True) if self._parent else True
        if parent_pinned:
            apply_pin_on_top(self, True, delayed=True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if hasattr(self, "_drawer") and self._drawer.isVisible():
                self._close_drawer()
                return
            if self._tts:
                self._stop_speech()
        super().keyPressEvent(event)
