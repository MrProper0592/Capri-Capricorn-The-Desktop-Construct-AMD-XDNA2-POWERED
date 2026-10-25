"""
wm.py — Window Manager & Compositor Integration for Capri.
Handles Wayland (KDE Plasma 6 / KWin) and X11 always-on-top / keep-above management.

Under Wayland, client surfaces cannot unilaterally modify stacking order via standard
xdg_shell flags without compositor support. On KDE Plasma 6 Wayland, KWin controls
window layers (NormalLayer vs AboveLayer) via its scripting interface.
This module dynamically controls window keepAbove via KWin DBus without recreating
or unmapping Wayland surfaces, preventing flickering and maintaining frameless transparency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtDBus import QDBusConnection, QDBusInterface
    HAS_QT_DBUS = True
except ImportError:
    HAS_QT_DBUS = False


def is_wayland() -> bool:
    """Return True if running under a Wayland compositor."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return True
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    app = QGuiApplication.instance()
    if app and hasattr(app, "platformName") and app.platformName() == "wayland":
        return True
    return False


def is_kde_plasma() -> bool:
    """Return True if running on KDE Plasma."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return "KDE" in desktop.upper()


def set_kwin_keep_above(keep_above: bool, title_filter: Optional[str] = None) -> bool:
    """
    Set keepAbove property in KWin for windows belonging to this process.
    If title_filter is specified, only windows whose captions contain title_filter
    are updated; otherwise all windows belonging to this PID are updated.
    """
    pid = os.getpid()
    val_str = "true" if keep_above else "false"
    title_cond = f' && wins[i].caption.indexOf("{title_filter}") !== -1' if title_filter else ""

    js_code = f"""
    var wins = workspace.windowList();
    for (var i = 0; i < wins.length; i++) {{
        if (wins[i].pid === {pid}{title_cond}) {{
            wins[i].keepAbove = {val_str};
        }}
    }}
    """

    # 1. Try PySide6.QtDBus
    if HAS_QT_DBUS:
        try:
            bus = QDBusConnection.sessionBus()
            if bus.isConnected():
                scripting = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
                if scripting.isValid():
                    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                        f.write(js_code)
                        tmp_path = f.name
                    plugin_name = f"capri_pin_{pid}_{int(time.time() * 1000)}"
                    try:
                        reply = scripting.call("loadScript", tmp_path, plugin_name)
                        if reply.type() != reply.MessageType.ErrorMessage and reply.arguments():
                            sid = reply.arguments()[0]
                            sobj = QDBusInterface("org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script", bus)
                            sobj.call("run")
                            sobj.call("stop")
                            scripting.call("unloadScript", plugin_name)
                            return True
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
        except Exception:
            pass

    # 2. Fallback to CLI tools (qdbus6 / qdbus)
    qdbus_bin = shutil.which("qdbus6") or shutil.which("qdbus")
    if qdbus_bin:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js_code)
            tmp_path = f.name
        plugin_name = f"capri_pin_cli_{pid}_{int(time.time() * 1000)}"
        try:
            r = subprocess.run(
                [qdbus_bin, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", tmp_path, plugin_name],
                capture_output=True,
                text=True,
                check=True,
            )
            sid = r.stdout.strip()
            subprocess.run([qdbus_bin, "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.run"], check=True)
            subprocess.run([qdbus_bin, "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.stop"], check=True)
            subprocess.run([qdbus_bin, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", plugin_name], check=True)
            return True
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return False


def apply_pin_on_top(widget: QWidget, keep_above: bool, delayed: bool = True) -> None:
    """
    Ensure the widget has keep-above applied in a way that respects the current compositor.
    On Wayland + KDE, commands KWin via DBus to avoid destroying the surface.
    On X11 / fallback, uses standard Qt WindowStaysOnTopHint.
    """
    if is_wayland():
        title = widget.windowTitle() if widget.windowTitle() else None
        set_kwin_keep_above(keep_above, title)

        # When newly shown, the surface takes ~30-60ms to map in KWin
        if delayed and keep_above:
            QTimer.singleShot(60, lambda: set_kwin_keep_above(keep_above, title))
            QTimer.singleShot(180, lambda: set_kwin_keep_above(keep_above, title))
    else:
        from PySide6.QtCore import Qt
        widget.setWindowFlag(Qt.WindowStaysOnTopHint, keep_above)
        if widget.isVisible():
            widget.show()
