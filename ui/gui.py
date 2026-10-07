"""
J.A.R.V.I.S. Desktop HUD
========================
An Iron-Man / Mark-L inspired native desktop interface for the Ollama-powered
voice assistant. Replaces the old browser (web) UI with a PyQt6 HUD.

Run it with:
    ollama_assistant_env\\Scripts\\python.exe main.py
"""

import json
import math
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime

# Windows console: print emoji/unicode without crashing
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests
from decouple import config

from PyQt6.QtCore import (
    QAbstractAnimation, QObject, QPointF, QPropertyAnimation, QRect, QRectF,
    Qt, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap,
    QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget,
)

from core.assistant import (
    PersonalizedAssistant, is_farewell, is_stop_phrase, load_history,
)
from core.ollama import BIG_MODEL, DEFAULT_MODEL, split_deep_marker
from functions.online_ops import (
    find_my_ip, get_city_from_ip, get_latest_news, get_random_joke,
    get_weather_report, have_internet, play_on_youtube, search_on_wikipedia,
)
from functions.os_ops import (
    looks_like_command, open_calculator, open_camera, open_cmd,
    open_notepad, take_screenshot,
)
from services.ollama_manager import ensure_ollama, is_online
from utils.autostart import autostart_enabled

try:
    import psutil
except Exception:
    psutil = None

# Models offered in the HUD's model picker (keep in sync with what you
# pulled via `ollama pull`). Deduped so a model only shows up once.
MODELS = list(dict.fromkeys([DEFAULT_MODEL, BIG_MODEL]))
OLLAMA_URL = "http://localhost:11434"

# Settings stay at the project root (covered by .gitignore) so they
# survive code moves.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "gui_settings.json")


def _load_settings():
    """Read the GUI settings file. Returns {} on any error."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_speaker(on):
    """Persist the SPEAKER toggle so it survives app restarts."""
    try:
        data = _load_settings()
        data["speaker_on"] = bool(on)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[gui] could not save settings: {e}")


def _save_model(name):
    """Persist the chosen model so it survives app restarts."""
    try:
        data = _load_settings()
        data["model"] = str(name)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[gui] could not save settings: {e}")


def _save_autolisten(on):
    """Persist the AUTO-MIC toggle so it survives app restarts."""
    try:
        data = _load_settings()
        data["auto_listen"] = bool(on)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[gui] could not save settings: {e}")


def _save_wake(on):
    """Persist the WAKE-WORD toggle so it survives app restarts."""
    try:
        data = _load_settings()
        data["wake_word"] = bool(on)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[gui] could not save settings: {e}")


# ---------------------------------------------------------------------------
# Mark-L inspired cyberpunk palette
# ---------------------------------------------------------------------------
class C:
    BG = "#00060a"
    PANEL = "#010d14"
    PANEL2 = "#010f18"
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    PRI = "#00d4ff"
    PRI_DIM = "#007a99"
    PRI_GHO = "#001f2e"
    ACC = "#ff6b00"
    ACC2 = "#ffcc00"
    GREEN = "#00ff88"
    RED = "#ff3355"
    MUTED_C = "#ff3366"
    TEXT = "#8ffcff"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    WHITE = "#d8f8ff"
    BAR_BG = "#011520"


def qcol(h, a=255):
    c = QColor(h)
    c.setAlpha(a)
    return c


_PANEL_SS = f"""
QFrame {{
    background: {C.PANEL};
    border: 1px solid {C.BORDER};
    border-radius: 6px;
}}
"""

_BTN_SS = f"""
QPushButton {{
    background: transparent; color: {C.TEXT_MED};
    border: 1px solid {C.BORDER_B}; border-radius: 3px;
}}
QPushButton:hover {{
    background: {C.PRI_GHO}; color: {C.PRI}; border-color: {C.PRI};
}}
QPushButton:pressed {{ background: {C.PRI_DIM}; color: {C.BG}; }}
QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
"""

_BTN_ON_SS = f"""
QPushButton {{
    background: {C.PRI_GHO}; color: {C.PRI};
    border: 1px solid {C.PRI}; border-radius: 3px;
}}
QPushButton:hover {{ background: {C.PRI_DIM}; color: {C.BG}; }}
"""

_INPUT_SS = f"""
QLineEdit {{
    background: {C.PANEL}; color: {C.TEXT};
    border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 6px 10px;
}}
QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
"""

_COMBO_SS = f"""
QComboBox {{
    background: {C.PANEL}; color: {C.TEXT_MED};
    border: 1px solid {C.BORDER_B}; border-radius: 3px;
    padding: 3px 6px;
}}
QComboBox:hover {{ border-color: {C.PRI}; color: {C.PRI}; }}
QComboBox:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {C.PANEL2}; color: {C.TEXT};
    border: 1px solid {C.BORDER_B};
    selection-background-color: {C.PRI_GHO};
    selection-color: {C.PRI};
    outline: none;
}}
"""


# ---------------------------------------------------------------------------
# HUD canvas — the animated core (Mark-L style)
# ---------------------------------------------------------------------------
class HudCanvas(QWidget):
    def __init__(self, assistant_name="J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.muted = False
        self.speaking = False
        self.state = "INITIALISING"
        self._name = assistant_name
        self._tick = 0
        self._scale = 1.0
        self._tgt_scale = 1.0
        self._halo = 55.0
        self._tgt_halo = 55.0
        self._last_t = time.time()
        self._scan = 0.0
        self._scan2 = 180.0
        self._rings = [0.0, 120.0, 240.0]
        self._pulses = [0.0, 50.0, 100.0]
        self._blink = True
        self._blink_tick = 0
        self._particles = []
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    # -- public API --------------------------------------------------------
    def set_state(self, s):
        self.state = s

    def set_speaking(self, b):
        self.speaking = b

    def set_muted(self, b):
        self.muted = b

    # -- animation ---------------------------------------------------------
    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo = random.uniform(48, 68)
            self._last_t = now
        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo += (self._tgt_halo - self._halo) * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360
        self._scan = (self._scan + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0] + p[2], p[1] + p[3], p[2] * 0.97, p[3] * 0.97, p[4] - 0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31
        # halo glow
        for i in range(10):
            r = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base = self._rings[idx]
            a_val = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r))
            p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn * math.cos(rad), cy - inn * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [
            (hl, ht, 1, 1), (hr, ht, -1, 1),
            (hl, hb, 1, -1), (hr, hb, -1, -1),
        ]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # core orb
        orb_r = int(fw * 0.27 * self._scale)
        oc = (200, 0, 50) if self.muted else (0, 60, 110)
        for i in range(8, 0, -1):
            r2 = int(orb_r * i / 8)
            frc = i / 8
            a = max(0, min(255, int(self._halo * 1.1 * frc)))
            p.setBrush(QBrush(QColor(
                int(oc[0] * frc), int(oc[1] * frc), int(oc[2] * frc), a)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
        p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
        p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        p.drawText(QRectF(cx - 90, cy - 14, 180, 28),
                   Qt.AlignmentFlag.AlignCenter, self._name)

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status text
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "SPEAKING", qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym} THINKING", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym} PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym} LISTENING", qcol(C.GREEN)
        elif self.state == "STANDBY":
            sym = "◉" if self._blink else "◎"
            txt, col = f"{sym} STANDBY", qcol(C.PRI_DIM)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym} {self.state}", qcol(C.PRI)
        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)


# ---------------------------------------------------------------------------
# Metric bar (CPU / RAM / NET)
# ---------------------------------------------------------------------------
class MetricBar(QWidget):
    def __init__(self, label, color=C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text = "--"
        self.setFixedHeight(34)
        self.setMinimumWidth(70)

    def set_value(self, pct, text):
        self._value = max(0.0, min(100.0, pct))
        self._text = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_w, bar_h, bar_x, bar_y = W - 10, 4, 5, H - 8
        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)
        fill = int(bar_w * self._value / 100.0)
        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)
        if fill > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(6, 3, 50, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 6, 12),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._text)


# ---------------------------------------------------------------------------
# Log widget — typewriter effect, colored by sender (Mark-L style)
# ---------------------------------------------------------------------------
class LogWidget(QTextEdit):
    line_sig = pyqtSignal(str, str)   # (text, tag) — thread-safe enqueue
    stream_sig = pyqtSignal(str)      # streamed reply chars
    finish_sig = pyqtSignal()         # close the current streamed line

    _TAG_COLORS = {"you": C.WHITE, "ai": C.PRI, "err": C.RED, "sys": C.ACC2}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
QTextEdit {{
    background: {C.PANEL};
    color: {C.TEXT};
    border: 1px solid {C.BORDER};
    border-radius: 4px;
    padding: 6px;
    selection-background-color: {C.PRI_GHO};
}}
QScrollBar:vertical {{
    background: {C.BG}; width: 8px; border: none;
}}
QScrollBar::handle:vertical {{
    background: {C.BORDER_B}; border-radius: 4px; min-height: 20px;
}}
""")
        self._queue = []                  # pending (text, tag) full lines
        self._stream = ""                 # chars waiting for the stream line
        self._typing = None               # (text, tag, pos) being typed
        self._close_after_stream = False
        self._in_stream_line = False      # inside an open streamed reply line
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.line_sig.connect(self._on_line)
        self.stream_sig.connect(self._on_stream)
        self.finish_sig.connect(self._on_finish)

    # -- public API (thread-safe: all go through signals) ------------------
    def enqueue_line(self, text, tag="sys"):
        self.line_sig.emit(text, tag)

    def append_stream(self, text):
        self.stream_sig.emit(text)

    def finish_stream(self):
        self.finish_sig.emit()

    def clear_log(self):
        self._queue.clear()
        self._stream = ""
        self._typing = None
        self._close_after_stream = False
        self._in_stream_line = False
        self._timer.stop()
        self.clear()

    # -- slots -------------------------------------------------------------
    def _on_line(self, text, tag):
        if not text:
            return
        self._queue.append((text, tag))
        self._kick()

    def _on_stream(self, text):
        if text:
            self._stream += text
            self._in_stream_line = True
        self._kick()

    def _on_finish(self):
        if self._in_stream_line:
            self._close_after_stream = True
        self._kick()

    def _kick(self):
        if not self._timer.isActive():
            self._timer.start(6)

    def _step(self):
        if self._typing is not None:
            text, tag, pos = self._typing
            self._insert(text[pos], self._TAG_COLORS.get(tag, C.TEXT))
            pos += 1
            if pos >= len(text):
                self._typing = None
                self._insert("\n", C.TEXT)
            else:
                self._typing = (text, tag, pos)
            return
        if self._stream:
            self._insert(self._stream[0], C.PRI)
            self._stream = self._stream[1:]
            if not self._stream and self._close_after_stream:
                self._close_after_stream = False
                self._in_stream_line = False
                self._insert("\n\n", C.TEXT)
            return
        if self._close_after_stream:
            # stream already drained — close the line now
            self._close_after_stream = False
            self._in_stream_line = False
            self._insert("\n\n", C.TEXT)
            return
        if self._queue:
            text, tag = self._queue.pop(0)
            self._typing = (text, tag, 0)
            return
        self._timer.stop()

    def _insert(self, text, color):
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QBrush(QColor(color)))
        fmt.setFont(QFont("Courier New", 9))
        cur.insertText(text, fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()


# ---------------------------------------------------------------------------
# TTS routed through a dedicated speech thread (keeps the Qt loop free)
# ---------------------------------------------------------------------------
class GuiSpeech:
    """Drop-in replacement for the assistant's Speech object.

    Speaks asynchronously: text is queued and a daemon thread (which owns the
    pyttsx3 engine) actually performs the speech, so the Qt event loop and the
    token stream are never blocked by runAndWait().
    """

    def __init__(self):
        self.tts_available = False
        self.is_speaking = False
        self.muted = False        # when True, text is captured but not spoken
        self.interrupted = False  # barge-in: drop speech until the next command
        self.capture = False      # when True, on_speak fires (log forwarding)
        self.on_speak = None      # optional callback(text)
        self.on_error = None      # optional callback(message) from the speech thread
        self._errors = []         # buffered errors, drained by the GUI thread
        self._queue = queue.Queue()
        self._speech = None       # SapiSpeech instance owned by the speech thread
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _note_error(self, msg):
        self._errors.append(msg)
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass

    def drain_errors(self):
        errs = list(self._errors)
        self._errors.clear()
        return errs

    def _run(self):
        from core.speech import SapiSpeech
        speech = SapiSpeech(rate=1)
        self._speech = speech
        if speech.ok:
            self.tts_available = True
            print("[gui] TTS ready")
        else:
            print(f"[gui] TTS unavailable: {speech.error}")
            self._note_error(f"Speech engine failed to start: {speech.error}")
        while True:
            text = self._queue.get()
            if text is None:
                break
            if not speech.ok:
                continue
            try:
                self.is_speaking = True
                speech.speak(text)
            except Exception as e:
                print(f"[gui] TTS error: {e}")
                self._note_error(f"Could not play speech: {e}")
            finally:
                self.is_speaking = False

    def speak(self, text):
        if not text or not text.strip():
            return
        if self.capture and self.on_speak:
            try:
                self.on_speak(text)
            except Exception:
                pass
        if self.interrupted or self.muted or not self.tts_available:
            return
        self._queue.put(text)

    def stop(self):
        """Barge-in: cut off current speech and drop everything queued.

        Also sets ``interrupted`` so chunks still streaming from the current
        reply are silenced until the next command starts (the GUI resets it
        at the start of each new request).
        """
        self.interrupted = True
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        speech = self._speech
        if speech is not None and speech.ok:
            try:
                speech.stop()
            except Exception as e:
                print(f"[gui] TTS stop error: {e}")

    def is_busy(self):
        return self.is_speaking


# ---------------------------------------------------------------------------
# Assistant worker — runs requests in background threads, streams to the UI
# ---------------------------------------------------------------------------
class AssistantWorker(QObject):
    status = pyqtSignal(str)          # LISTENING / THINKING / PROCESSING / READY
    token = pyqtSignal(str)           # streamed reply token
    line = pyqtSignal(str, str)       # (text, tag) complete log line
    done = pyqtSignal()               # finished streaming a reply
    error = pyqtSignal(str)
    listening_finished = pyqtSignal(object)  # query text or None
    wake_detected = pyqtSignal()      # 'hey jarvis' heard
    wake_state = pyqtSignal(bool)     # wake-word loop started / stopped
    processed = pyqtSignal()          # a request finished processing
    toast = pyqtSignal(str)           # async announcements (timers/reminders)

    def __init__(self, assistant):
        super().__init__()
        self.assistant = assistant
        self.tts = assistant.tts
        self.followup = ""
        self.listening = False   # guards against overlapping mic sessions
        self.wake_active = False # wake-word loop running
        self.wake = None         # WakeWordListener instance
        self._wake_capture = False  # True while capturing a command after a wake
        self._mic_convo = False   # conversation mode: auto-listen after replies
        self._convo_after = False # this capture starts/keeps conversation mode
        self._interrupt_on = False  # barge-in listener running
        self._interrupt_thread = None
        self._busy_lock = threading.Lock()

    # -- helpers -----------------------------------------------------------
    def say(self, text, tag="sys"):
        self.line.emit(text, tag)
        # a fresh announcement always speaks (barge-in only silences the
        # reply it was meant to cut off)
        self.tts.interrupted = False
        self.tts.speak(text)

    def reply_line(self, text):
        self.assistant.history.append(f"AI: {text}")
        self.line.emit(text, "ai")
        self.tts.speak(text)

    def announce(self, text):
        """Speak an async announcement (timer/reminder) and show it in the
        transmission log."""
        self.tts.interrupted = False
        self.toast.emit(text)
        self.tts.speak(text)

    # -- barge-in -------------------------------------------------------------
    def start_interrupt_listener(self):
        """Listen for 'stop'-style phrases while J.A.R.V.I.S. is speaking,
        so the user can cut it off mid-sentence."""
        if self._interrupt_thread is not None and \
                self._interrupt_thread.is_alive():
            return
        self._interrupt_on = True
        self._interrupt_thread = threading.Thread(
            target=self._interrupt_loop, daemon=True)
        self._interrupt_thread.start()

    def stop_interrupt_listener(self):
        self._interrupt_on = False

    def _interrupt_loop(self):
        import speech_recognition as sr
        from core.stt import new_recognizer, transcribe
        recognizer = new_recognizer()
        while self._interrupt_on:
            if not self.assistant.tts.is_busy():
                time.sleep(0.3)
                continue
            # only grab the mic when nothing else is capturing
            if self.listening or self._wake_capture:
                time.sleep(0.3)
                continue
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.3)
                    # same quiet-speaker floor as the command capture path
                    recognizer.energy_threshold = max(
                        recognizer.energy_threshold, 60)
                    audio = recognizer.listen(source, timeout=1,
                                              phrase_time_limit=1.5)
            except Exception:
                time.sleep(0.6)
                continue
            text = transcribe(audio, recognizer, language="en-in")
            if text and is_stop_phrase(text):
                print("[gui] barge-in heard — silencing")
                self.tts.stop()
                self.line.emit("As you wish — stopping.", "sys")
                time.sleep(1.5)

    # -- main entry: a text message ----------------------------------------
    def process(self, message):
        msg = (message or "").strip()
        if not msg:
            return
        with self._busy_lock:
            if self.assistant.is_processing:
                self.line.emit("I'm still busy with the previous request.", "err")
                return
            self.assistant.is_processing = True
        # a new request is a fresh start: allow speech again after a barge-in
        self.tts.interrupted = False
        try:
            self.status.emit("THINKING")
            msg, deep = split_deep_marker(msg)
            self.line.emit(f"You: {msg}", "you")
            lowered = msg.lower()

            # answer to a pending wikipedia / youtube follow-up
            if self.followup:
                kind = self.followup
                self.followup = ""
                self.assistant.history.append(f"User: {msg}")
                if not have_internet():
                    # safety net: connectivity can drop between the button
                    # click and the user's answer
                    self._offline_reply(
                        "look things up on Wikipedia"
                        if kind == "wikipedia"
                        else "play YouTube videos")
                    return
                if kind == "wikipedia":
                    try:
                        results = search_on_wikipedia(msg)
                        short = results[:200] + "..." if len(results) > 200 else results
                        self.reply_line(f"Wikipedia says: {short}")
                    except Exception:
                        self.reply_line("Wikipedia's not playing nice right now.")
                else:
                    play_on_youtube(msg)
                    self.reply_line(f"Playing {msg} on YouTube — enjoy!")
                return

            # farewell: end the session without closing the app
            if is_farewell(msg):
                text = f"See ya, {self.assistant.username}! Ping me anytime!"
                self.assistant.history.append(f"User: {msg}")
                self.assistant.history.append(f"AI: {text}")
                threading.Thread(
                    target=self.assistant.summarize_and_save_history,
                    daemon=True,
                ).start()
                # a goodbye ends the whole voice session: conversation mode
                # AND the wake-word listener — no more listening afterwards
                self._mic_convo = False
                self.stop_wake_loop()
                self.line.emit(
                    "Listening stopped — click WAKE or MIC to talk again.",
                    "sys")
                self.reply_line(text)
                return

            # requests for a follow-up term — but not when the message is itself
            # a command (e.g. "open youtube in brave", "play despacito on
            # youtube", "search for X in chrome" get handled directly).
            if not looks_like_command(lowered):
                if "wikipedia" in lowered:
                    self.followup = "wikipedia"
                    self.say("What should I look up on Wikipedia, friend?")
                    return
                if "youtube" in lowered:
                    self.followup = "youtube"
                    self.say("What do you want to jam to on YouTube?")
                    return

            # local / online commands
            if self._run_command(lowered):
                return

            # normal AI chat — stream tokens (ULTRATHINK uses the big model)
            self._stream_chat(msg, model=BIG_MODEL if deep else None)
        except Exception as e:
            print(f"[gui worker] {e}")
            self.error.emit("Yikes, something went wrong on my end.")
        finally:
            self.assistant.is_processing = False
            self.status.emit("READY")
            self.processed.emit()

    def _run_command(self, lowered):
        """Run handle_command, forwarding spoken announcements to the log."""
        # getattr: only GuiSpeech carries these hooks — a plain Speech / a
        # test double must not crash the worker just because it lacks them
        old_capture = getattr(self.tts, "capture", False)
        old_hook = getattr(self.tts, "on_speak", None)
        self.tts.capture = True
        self.tts.on_speak = lambda t: self.line.emit(t, "sys")
        try:
            return self.assistant.handle_command(lowered)
        finally:
            self.tts.capture = old_capture
            self.tts.on_speak = old_hook

    def _stream_chat(self, msg, model=None):
        """Stream a reply, optionally on a specific (bigger) model."""
        # Just after boot the server may still be starting - wake it up and
        # wait (up to 30s) instead of failing the first message.
        if not is_online():
            self.say("Ollama is waking up — one sec!", "sys")
            ensure_ollama(timeout=30,
                          on_status=lambda m: self.line.emit(m, "sys"))
        old_model = self.assistant.ollama.model
        swapped = model is not None and old_model != model
        if swapped:
            self.assistant.ollama.model = model
            self.say("Ultra Think mode on — big brain engaged!")
        try:
            gen = self.assistant.generate_reply(msg, speak=True, paced=False)
            # The first next() builds the prompt (may announce "googling..." via
            # tts.speak); forward those announcements, then stream cleanly.
            self.tts.capture = True
            self.tts.on_speak = lambda t: self.line.emit(t, "sys")
            parts = []
            first = True
            for token in gen:
                if first:
                    self.tts.capture = False
                    self.tts.on_speak = None
                    first = False
                parts.append(token)
                self.token.emit(token)
            reply = "".join(parts).strip()
            self.assistant.history.append(f"User: {msg}")
            self.assistant.history.append(f"AI: {reply}")
            self.done.emit()
        finally:
            # Always resync to the model currently selected in the picker, so
            # a mid-flight switch (or an ULTRATHINK question sent while the
            # big model is already selected) never leaves the two out of sync.
            self.assistant.ollama.model = self.assistant.model

    # -- microphone ----------------------------------------------------------
    def start_listening(self, convo=False):
        """Capture one spoken command.

        convo=True keeps conversation mode going after this capture: once the
        reply has been spoken, the mic opens again for the next command.
        """
        self._convo_after = convo
        self._start_capture(from_wake=False)

    def start_wake_followup_capture(self):
        """Conversation mode: capture the next command without needing the
        wake word again. Runs once the previous reply has been spoken."""
        self._convo_after = True
        self._start_capture(from_wake=False)

    def _start_capture(self, from_wake=False):
        """Run one mic capture on a background thread.

        from_wake=True means the wake word just fired: conversation mode
        begins. Either way the wake-word loop stays paused (via
        _wake_capture) for the whole capture so two mic streams never fight
        — harmless when no wake loop is running (manual MIC / auto-mic).
        """
        with self._busy_lock:
            if self.assistant.is_processing or self.listening:
                return
            self.listening = True
        # the barge-in listener must never hold the mic while we capture —
        # stop it now and re-arm it once this capture is done
        self.stop_interrupt_listener()
        self._wake_capture = True
        if from_wake:
            self._mic_convo = True
        self.status.emit("LISTENING")
        threading.Thread(target=self._capture_thread, daemon=True).start()

    def _capture_thread(self):
        # give the wake-word engine a moment to release its mic stream
        # (it re-checks _wake_paused every ~80 ms) before we open the mic
        time.sleep(0.2)
        # ...and make sure the barge-in listener has fully released the mic
        # too — otherwise two PyAudio streams can fight and this capture
        # silently misses the command. Bounded (normally ~0.3s while the
        # listener idles; up to ~3s if it was mid-listen), so it can't hang.
        if self._interrupt_thread is not None:
            try:
                self._interrupt_thread.join(timeout=3)
            except Exception:
                pass
        self.tts.capture = True
        self.tts.on_speak = lambda t: self.line.emit(t, "sys")
        try:
            query = self.assistant.listen()
        except Exception as e:
            print(f"[gui mic] {e}")
            query = None
        finally:
            self.tts.capture = False
            self.tts.on_speak = None
            self._wake_capture = False
            with self._busy_lock:
                self.listening = False
            # re-arm the barge-in listener now the mic is free again — if the
            # old thread is still winding down, just flip its flag back on
            if (self._interrupt_thread is not None
                    and self._interrupt_thread.is_alive()):
                self._interrupt_on = True
            else:
                self.start_interrupt_listener()
        if query and self._convo_after:
            self._mic_convo = True
        self.listening_finished.emit(query)

    # -- wake word ------------------------------------------------------------
    def start_wake_loop(self):
        """Begin continuous 'hey jarvis' listening in a background thread."""
        if self.wake_active:
            return
        self.wake_active = True
        try:
            from core.wake_word import WakeWordListener
            self.wake = WakeWordListener(
                botname=self.assistant.botname,
                on_wake=self._on_wake_detected,
                is_paused=self._wake_paused,
            )
        except Exception as e:
            print(f"[gui wake] could not start wake word: {e}")
            self.wake_active = False
            # tell the GUI the loop is NOT running so the WAKE button
            # doesn't keep claiming it is
            self.wake_state.emit(False)
            return
        self.wake_state.emit(True)
        self.wake.start()

    def stop_wake_loop(self):
        """Stop the wake-word listener if it's running."""
        if not self.wake_active and self.wake is None:
            return
        self.wake_active = False
        if self.wake is not None:
            try:
                self.wake.stop()
            finally:
                self.wake = None
        self.wake_state.emit(False)

    def _wake_paused(self):
        """True while JARVIS is busy, so it never wakes itself up."""
        return (self._wake_capture or self.assistant.is_processing
                or self.assistant.tts.is_busy())

    def _on_wake_detected(self):
        """Wake word heard: capture one full command, then let the normal
        flow process it. The listener stays paused while that runs and
        resumes listening afterwards. Never gets stuck: even if the mic
        fails, the wake loop is unpaused and the GUI is told we're done."""
        self.wake_detected.emit()
        self._start_capture(from_wake=True)

    # -- quick actions --------------------------------------------------------
    def _launch(self, announce, fn):
        """Run a blocking action in a background thread."""
        with self._busy_lock:
            if self.assistant.is_processing:
                return
            self.assistant.is_processing = True
        # quick actions are keyboard interactions — they end the voice
        # conversation session (mirrors typing in the input bar)
        self._mic_convo = False
        self.tts.interrupted = False
        self.status.emit("PROCESSING")
        if announce:
            self.say(announce, "sys")

        def job():
            try:
                fn()
            except Exception as e:
                print(f"[gui action] {e}")
                self.error.emit("Couldn't run that action. My bad!")
            finally:
                self.assistant.is_processing = False
                self.status.emit("READY")

        threading.Thread(target=job, daemon=True).start()

    def _offline_reply(self, what):
        """Reply when an online-only quick action is clicked while offline."""
        self.reply_line(
            f"You're offline right now, so I can't {what}. "
            "The AI chat, apps and screenshots still work!")

    def action_weather(self):
        def fn():
            if not have_internet():
                self._offline_reply("check the weather")
                return
            city = get_city_from_ip()
            weather, temp, feels = get_weather_report(city)
            place = city or "your area"
            self.reply_line(
                f"It's {temp} in {place} and feels like {feels}. "
                f"{weather.capitalize()} vibes today!")
        self._launch("Let me check the skies for you...", fn)

    def action_joke(self):
        def fn():
            if not have_internet():
                self._offline_reply("tell you a joke")
                return
            self.reply_line(get_random_joke())
        self._launch("One joke, coming right up!", fn)

    def action_news(self):
        def fn():
            if not have_internet():
                self._offline_reply("fetch the news")
                return
            news = get_latest_news()
            if news:
                self.reply_line(f"Here's one: {news[0]}")
            else:
                self.reply_line("No headlines right now — news is quiet.")
        self._launch("Grabbing the headlines...", fn)

    def action_ip(self):
        def fn():
            ip = find_my_ip()
            if ip:
                self.reply_line(
                    f"Your IP Address is {ip} "
                    f"(don't worry, I won't leak it!)")
            else:
                self.reply_line(
                    "Couldn't fetch your IP — looks like you're offline.")
        self._launch(None, fn)

    def action_screenshot(self):
        def fn():
            path = take_screenshot()
            if path:
                # show the path in the log without reading it out loud
                self.line.emit(f"Screenshot saved to {path}", "sys")
            else:
                self.line.emit(
                    "Screenshot failed — couldn't capture the screen.", "err")
        self._launch("Screenshot time! Hold still...", fn)

    def action_open(self, name, func):
        def fn():
            func()
            self.line.emit(f"{name} launched.", "sys")
        self._launch(f"Opening {name.lower()} for you.", fn)

    def action_youtube(self):
        if not have_internet():
            self._offline_reply("play YouTube videos")
            return
        self.followup = "youtube"
        self.say("What do you want to jam to on YouTube?")

    def action_wikipedia(self):
        if not have_internet():
            self._offline_reply("look things up on Wikipedia")
            return
        self.followup = "wikipedia"
        self.say("What should I look up on Wikipedia, friend?")


# ---------------------------------------------------------------------------
# Toast overlay — a cyberpunk notice that pops in at the top-right of the
# HUD (timers/reminders). Child widget, so it moves with the window, never
# steals focus and is invisible to the mouse. Multiple notices queue up.
# ---------------------------------------------------------------------------
class ToastOverlay(QWidget):
    """A cyberpunk notice that pops in at the top-right of the HUD when a
    timer/reminder fires. Child widget, so it moves with the window, never
    steals focus and is invisible to the mouse. Multiple notices queue up.

    Fading uses a custom ``fade`` property (child widgets don't honour
    windowOpacity) — the panel is repainted with scaled alpha each frame.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._text = ""
        self._queue = []
        self._alpha = 0.0
        self._hold_ms = 4500
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._begin_fade_out)
        self._fade_in = QPropertyAnimation(self, b"fade", self)
        self._fade_in.setDuration(220)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.finished.connect(self._start_hold)
        self._fade_out = QPropertyAnimation(self, b"fade", self)
        self._fade_out.setDuration(450)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self._on_faded_out)
        self.setFixedWidth(340)
        self.setFixedHeight(64)
        self.hide()

    # -- animation property ------------------------------------------------
    def get_fade(self):
        return self._alpha

    def set_fade(self, value):
        self._alpha = max(0.0, min(1.0, float(value)))
        self.update()

    fade = pyqtProperty(float, get_fade, set_fade)

    # -- public API ---------------------------------------------------------
    def show_toast(self, text, hold_ms=4500):
        """Queue a notice; it appears top-right, holds, then fades away."""
        if not text or not text.strip():
            return
        self._queue.append((text.strip(), int(hold_ms)))
        if self.isVisible() or \
                self._fade_in.state() == QAbstractAnimation.State.Running or \
                self._fade_out.state() == QAbstractAnimation.State.Running:
            return  # current toast owns the stage — this one waits its turn
        self._next()

    def _next(self):
        if not self._queue:
            self.hide()
            return
        self._text, self._hold_ms = self._queue.pop(0)
        # size the panel to the wrapped message
        fm = QFontMetrics(QFont("Courier New", 9))
        wrap = fm.boundingRect(QRect(0, 0, self.width() - 30, 800),
                               Qt.TextFlag.TextWordWrap, self._text)
        self.setFixedHeight(max(64, wrap.height() + 44))
        self._position()
        self.set_fade(0.0)
        self.show()
        self.raise_()
        self._fade_in.start()

    def _start_hold(self):
        """Hold the toast on screen for its own duration, then fade out."""
        self._hold.start(self._hold_ms)

    def _position(self):
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0:
            return
        # never wider than the parent (defensive for tiny windows)
        width = min(self.width(), max(100, parent.width() - 32))
        if width != self.width():
            self.setFixedWidth(width)
        self.move(max(8, parent.width() - self.width() - 16), 16)

    def _begin_fade_out(self):
        if self._fade_out.state() == QAbstractAnimation.State.Running:
            return  # guard: already fading out
        self._fade_out.start()

    def _on_faded_out(self):
        self.hide()
        self._next()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _):
        a = self._alpha
        if a <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        # soft glow + panel
        p.setPen(QPen(qcol(C.PRI, int(70 * a)), 6))
        p.setBrush(QBrush(qcol(C.PANEL, int(255 * a))))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QPen(qcol(C.PRI, int(210 * a)), 1))
        p.drawRoundedRect(r, 8, 8)
        # accent bar
        p.fillRect(QRectF(1, 9, 3, self.height() - 18),
                   qcol(C.ACC, int(255 * a)))
        # header
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(qcol(C.ACC2, int(255 * a)))
        p.drawText(QRectF(14, 7, self.width() - 28, 15),
                   Qt.AlignmentFlag.AlignLeft, "◈ NOTICE")
        # message (word-wrapped)
        p.setFont(QFont("Courier New", 9))
        p.setPen(qcol(C.TEXT, int(255 * a)))
        p.drawText(QRectF(14, 24, self.width() - 28, self.height() - 30),
                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   self._text)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    led_update = pyqtSignal(str, str, str)      # (name, value, color)
    ollama_status = pyqtSignal(str)             # progress messages
    startup_changed = pyqtSignal(bool, str)     # (ok, message) from the toggle

    def __init__(self, assistant, worker, speaker_on=True, auto_listen=True,
                 wake_word=True):
        super().__init__()
        self.assistant = assistant
        self.worker = worker
        self._speaker_on = bool(speaker_on)
        self._auto_listen = bool(auto_listen)
        self._wake_word_enabled = bool(wake_word)
        self._startup_on = autostart_enabled()
        self._startup_busy = False
        self._ollama_ensure_started = False
        self._autolisten_pending = False
        self._wake_pending = False
        self._restart_wake_after_listen = False
        self._mic_retries = 3     # auto-listen: retry mic after boot if needed
        self._wake_mic_retries = 3  # wake-word: separate retry budget
        self._mic_ok = False
        self._tts_known = False
        self._tts_check_start = time.time()
        self._net_last = None
        self._net_last_t = 0.0
        self._leds = {}

        self.setWindowTitle(f"{assistant.botname} — Desktop HUD")
        self.resize(1120, 720)
        self.setMinimumSize(940, 600)
        self.setStyleSheet(f"QMainWindow {{ background: {C.BG}; }}")

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._build_left_panel())
        row.addWidget(self._build_center_panel(), 1)
        row.addWidget(self._build_right_panel())
        root.addLayout(row, 1)
        root.addWidget(self._build_input_bar())
        self.setCentralWidget(central)
        # the notice overlay floats above the whole window (created after
        # the central widget so it paints on top)
        self._toast = ToastOverlay(self)

        self.led_update.connect(self._apply_led)
        self.ollama_status.connect(self._on_ollama_status)
        self.startup_changed.connect(self._on_startup_changed)
        self.hud.set_muted(not self._speaker_on)
        self._connect_worker()
        self._start_timers()

    # -- widgets -----------------------------------------------------------
    def _sep(self):
        s = QFrame()
        s.setFrameShape(QFrame.Shape.HLine)
        s.setStyleSheet(f"color: {C.BORDER}; background: {C.BORDER};")
        return s

    def _hdr(self, text):
        l = QLabel(text)
        l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        return l

    def _led(self, name):
        l = QLabel(f"● {name}: INIT")
        l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        l.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._leds[name] = l
        return l

    def _build_left_panel(self):
        panel = QFrame()
        panel.setFixedWidth(168)
        panel.setStyleSheet(_PANEL_SS)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(5)

        title = QLabel(f"◈ {self.assistant.botname}")
        title.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        v.addWidget(title)
        v.addWidget(self._hdr("MODEL"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText(self.assistant.model)
        self.model_combo.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self.model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_combo.setStyleSheet(_COMBO_SS)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        v.addWidget(self.model_combo)
        v.addWidget(self._sep())
        v.addWidget(self._hdr("QUICK ACTIONS"))

        actions = [
            ("WEATHER", self.worker.action_weather),
            ("JOKE", self.worker.action_joke),
            ("NEWS", self.worker.action_news),
            ("MY IP", self.worker.action_ip),
            ("SCREENSHOT", self.worker.action_screenshot),
            ("NOTEPAD", lambda: self.worker.action_open("Notepad", open_notepad)),
            ("CMD", lambda: self.worker.action_open("Command Prompt", open_cmd)),
            ("CALCULATOR", lambda: self.worker.action_open("Calculator", open_calculator)),
            ("CAMERA", lambda: self.worker.action_open("Camera", open_camera)),
            ("YOUTUBE", self.worker.action_youtube),
            ("WIKIPEDIA", self.worker.action_wikipedia),
        ]
        for label, fn in actions:
            b = QPushButton(label)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setFixedHeight(26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_BTN_SS)
            b.clicked.connect(fn)
            v.addWidget(b)

        v.addStretch(1)
        v.addWidget(self._sep())
        v.addWidget(self._hdr("STATUS"))
        v.addWidget(self._led("OLLAMA"))
        v.addWidget(self._led("TTS"))
        v.addWidget(self._led("MIC"))
        v.addWidget(self._led("INTERNET"))
        v.addSpacing(4)

        self.speaker_btn = QPushButton(
            "SPEAKER: ON" if self._speaker_on else "SPEAKER: OFF")
        self.speaker_btn.setFixedHeight(28)
        self.speaker_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speaker_btn.setStyleSheet(_BTN_ON_SS if self._speaker_on else _BTN_SS)
        self.speaker_btn.clicked.connect(self._toggle_voice)
        v.addWidget(self.speaker_btn)

        self.autolisten_btn = QPushButton(
            "AUTO-MIC: ON" if self._auto_listen else "AUTO-MIC: OFF")
        self.autolisten_btn.setFixedHeight(28)
        self.autolisten_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.autolisten_btn.setStyleSheet(
            _BTN_ON_SS if self._auto_listen else _BTN_SS)
        self.autolisten_btn.clicked.connect(self._toggle_autolisten)
        self.autolisten_btn.setToolTip(
            "When ON, the HUD starts listening for your voice as soon as "
            "it opens (right after the greeting).")
        v.addWidget(self.autolisten_btn)

        self.wake_btn = QPushButton(
            "WAKE: ON" if self._wake_word_enabled else "WAKE: OFF")
        self.wake_btn.setFixedHeight(28)
        self.wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wake_btn.setStyleSheet(
            _BTN_ON_SS if self._wake_word_enabled else _BTN_SS)
        self.wake_btn.clicked.connect(self._toggle_wake)
        self.wake_btn.setToolTip(
            "When ON, J.A.R.V.I.S. listens continuously and only answers "
            "when you say 'Hey Jarvis'.")
        v.addWidget(self.wake_btn)

        self.startup_btn = QPushButton(
            "STARTUP: ON" if self._startup_on else "STARTUP: OFF")
        self.startup_btn.setFixedHeight(28)
        self.startup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.startup_btn.setStyleSheet(
            _BTN_ON_SS if self._startup_on else _BTN_SS)
        self.startup_btn.clicked.connect(self._toggle_startup)
        self.startup_btn.setToolTip(
            "When ON, J.A.R.V.I.S. (and the Ollama AI engine) launch "
            "automatically when Windows starts.")
        v.addWidget(self.startup_btn)

        self.test_btn = QPushButton("TEST VOICE")
        self.test_btn.setFixedHeight(28)
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.setStyleSheet(_BTN_SS)
        self.test_btn.clicked.connect(self._test_voice)
        v.addWidget(self.test_btn)

        self.reset_btn = QPushButton("RESET CHAT")
        self.reset_btn.setFixedHeight(28)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setStyleSheet(_BTN_SS)
        self.reset_btn.clicked.connect(self._reset)
        v.addWidget(self.reset_btn)
        return panel

    def _build_center_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        head = QHBoxLayout()
        l = QLabel(f"◈ {self.assistant.botname} CORE")
        l.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._hud_state_lbl = QLabel("STATE: INITIALISING")
        self._hud_state_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._hud_state_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        head.addWidget(l)
        head.addStretch(1)
        head.addWidget(self._hud_state_lbl)
        v.addLayout(head)
        self.hud = HudCanvas(self.assistant.botname)
        v.addWidget(self.hud, 1)
        return w

    def _build_right_panel(self):
        panel = QFrame()
        panel.setFixedWidth(350)
        panel.setStyleSheet(_PANEL_SS)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        v.addWidget(self._hdr("TRANSMISSION LOG"))
        self.log = LogWidget()
        v.addWidget(self.log, 1)
        v.addSpacing(4)
        v.addWidget(self._hdr("SYSTEM LOAD"))
        mrow = QHBoxLayout()
        mrow.setSpacing(6)
        self.cpu_bar = MetricBar("CPU", C.PRI)
        self.ram_bar = MetricBar("RAM", C.ACC2)
        self.net_bar = MetricBar("NET", C.GREEN)
        mrow.addWidget(self.cpu_bar)
        mrow.addWidget(self.ram_bar)
        mrow.addWidget(self.net_bar)
        v.addLayout(mrow)
        if psutil is None:
            for b in (self.cpu_bar, self.ram_bar, self.net_bar):
                b.hide()
        return panel

    def _build_input_bar(self):
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        prompt = QLabel(">")
        prompt.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        prompt.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        h.addWidget(prompt)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command or question... (Enter to send)")
        self.input.setFont(QFont("Courier New", 10))
        self.input.setStyleSheet(_INPUT_SS)
        self.input.returnPressed.connect(self._send)
        h.addWidget(self.input, 1)

        self.mic_btn = QPushButton("MIC")
        self.mic_btn.setFixedWidth(64)
        self.mic_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_btn.setStyleSheet(_BTN_SS)
        self.mic_btn.setEnabled(False)
        self.mic_btn.clicked.connect(self._toggle_mic)
        h.addWidget(self.mic_btn)

        self.send_btn = QPushButton("SEND >")
        self.send_btn.setFixedWidth(84)
        self.send_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(_BTN_SS)
        self.send_btn.clicked.connect(self._send)
        h.addWidget(self.send_btn)
        return bar

    # -- wiring ------------------------------------------------------------
    def _connect_worker(self):
        w = self.worker
        w.token.connect(self.log.append_stream)
        w.line.connect(self.log.enqueue_line)
        w.done.connect(self.log.finish_stream)
        w.error.connect(lambda m: self.log.enqueue_line(m, "err"))
        w.status.connect(self._on_status)
        w.listening_finished.connect(self._on_listening_finished)
        w.wake_detected.connect(self._on_wake_detected)
        w.wake_state.connect(self._on_wake_state)
        w.processed.connect(self._on_processed)
        w.toast.connect(self._on_toast)

    def _start_timers(self):
        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._update_metrics)
        self._metrics_timer.start(1500)

        self._ollama_timer = QTimer(self)
        self._ollama_timer.timeout.connect(self._check_ollama)
        self._ollama_timer.start(8000)

        self._internet_timer = QTimer(self)
        self._internet_timer.timeout.connect(self._check_internet)
        self._internet_timer.start(15000)

        self._speak_timer = QTimer(self)
        self._speak_timer.timeout.connect(self._poll_speech)
        self._speak_timer.start(180)

        QTimer.singleShot(350, self._startup)

    def _startup(self):
        self.log.enqueue_line(f"◈ {self.assistant.botname} CORE ONLINE", "sys")
        self.log.enqueue_line(f"MODEL: {self.assistant.model}", "sys")
        self.log.enqueue_line(
            "Tip: start a question with ULTRATHINK: to use the big model.",
            "sys")
        self.hud.set_state("READY")
        self._hud_state_lbl.setText("STATE: READY")
        self._check_ollama()
        self._check_mic()
        # Right after a restart Ollama is usually still offline - bring the
        # server up in the background so the AI is ready before you ask.
        self._ensure_ollama_background()
        self.worker.say(self._greeting_text())
        # Check connectivity in the background and warn the user once if
        # offline — never delay the greeting on a network probe.
        threading.Thread(target=self._startup_internet_check, daemon=True).start()
        if self.assistant.tts.muted:
            self.log.enqueue_line(
                "Voice replies are OFF (remembered from last time) — "
                "click SPEAKER to turn them on.", "sys")
        self.input.setFocus()
        if self._wake_word_enabled:
            # wake-word mode supersedes the one-shot auto-listen
            self._wake_pending = True
            self.log.enqueue_line(
                "Wake-word mode armed — say 'Hey Jarvis' anytime.", "sys")
        elif self._auto_listen:
            # armed now; actually starts once the mic checks out READY
            self._autolisten_pending = True
            self.log.enqueue_line(
                "Auto-mic armed — I'll listen for you as soon as I'm up.",
                "sys")

    # -- Ollama bring-up ----------------------------------------------------
    def _ensure_ollama_background(self):
        """Start the local Ollama server once, in a background thread."""
        if self._ollama_ensure_started:
            return
        self._ollama_ensure_started = True
        threading.Thread(target=self._ensure_ollama_job, daemon=True).start()

    def _ensure_ollama_job(self):
        try:
            ensure_ollama(on_status=self.ollama_status.emit)
        except Exception as e:
            print(f"[gui] ollama ensure error: {e}")
            self.ollama_status.emit("Couldn't reach Ollama - is it installed?")
        finally:
            self._check_ollama()

    def _on_ollama_status(self, msg):
        """Progress from the Ollama bring-up thread -> log + LED."""
        self.log.enqueue_line(msg, "sys")
        low = msg.lower()
        if "online" in low or "already running" in low:
            self.led_update.emit("OLLAMA", "ONLINE", C.GREEN)
        elif "couldn't find" in low or "taking long" in low:
            self.led_update.emit("OLLAMA", "OFFLINE", C.RED)
        else:
            self.led_update.emit("OLLAMA", "STARTING", C.ACC)

    # -- start-at-boot toggle ------------------------------------------------
    def _toggle_startup(self):
        """Toggle the Windows start-at-boot entry (scheduled task/shortcut)."""
        if self._startup_busy:
            return
        self._startup_busy = True
        enable = not self._startup_on
        self.startup_btn.setEnabled(False)
        self.log.enqueue_line(
            "Enabling start at Windows startup..." if enable
            else "Disabling start at Windows startup...", "sys")

        def job():
            from utils.autostart import disable_autostart, enable_autostart
            try:
                ok, msg = enable_autostart() if enable else disable_autostart()
            except Exception as e:
                print(f"[gui] autostart error: {e}")
                ok, msg = False, f"Couldn't change startup setting: {e}"
            self.startup_changed.emit(ok, msg)

        threading.Thread(target=job, daemon=True).start()

    def _on_startup_changed(self, ok, msg):
        """Result of the enable/disable job: refresh button + log outcome."""
        self._startup_busy = False
        self.startup_btn.setEnabled(True)
        self._startup_on = autostart_enabled()
        self.startup_btn.setText(
            "STARTUP: ON" if self._startup_on else "STARTUP: OFF")
        self.startup_btn.setStyleSheet(
            _BTN_ON_SS if self._startup_on else _BTN_SS)
        self.log.enqueue_line(msg, "sys" if ok else "err")

    def _greeting_text(self):
        username = self.assistant.username
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return f"Morning, {username}! Sun's up, let's do this."
        if 12 <= hour < 16:
            return f"Hey, good afternoon {username}!"
        if 16 <= hour < 19:
            return f"Evening {username}! Hope your day's going awesome."
        return f"Hey night owl {username}!"

    # -- status checks -------------------------------------------------------
    def _check_internet(self):
        def job():
            try:
                ok = have_internet()
            except Exception as e:
                print(f"[gui] internet check error: {e}")
                ok = False
            self.led_update.emit("INTERNET",
                                 "ONLINE" if ok else "OFFLINE",
                                 C.GREEN if ok else C.RED)
        threading.Thread(target=job, daemon=True).start()

    def _startup_internet_check(self):
        """Background connectivity check on boot: update the LED and warn."""
        try:
            ok = have_internet()
        except Exception:
            ok = False
        self.led_update.emit("INTERNET", "ONLINE" if ok else "OFFLINE",
                             C.GREEN if ok else C.RED)
        if not ok:
            self.worker.say(
                "Heads up — you're offline right now. Weather, news, jokes, "
                "your IP and web search won't work, but I can still chat, "
                "open apps, and take screenshots.")

    def _check_ollama(self):
        def job():
            try:
                r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
                ok = r.status_code == 200
            except Exception:
                ok = False
            self.led_update.emit("OLLAMA",
                                 "ONLINE" if ok else "OFFLINE",
                                 C.GREEN if ok else C.RED)
        threading.Thread(target=job, daemon=True).start()

    def _check_mic(self):
        def job():
            try:
                import speech_recognition as sr
                sr.Microphone.list_microphone_names()
                ok = True
            except Exception as e:
                print(f"[gui] mic unavailable: {e}")
                ok = False
            if ok:
                self.led_update.emit("MIC", "READY", C.GREEN)
            else:
                self.led_update.emit("MIC", "UNAVAILABLE", C.RED)
                self.log.enqueue_line(
                    "MIC unavailable — run from the Python 3.13 venv where "
                    "PyAudio is installed.", "err")
        threading.Thread(target=job, daemon=True).start()

    def _apply_led(self, name, value, color):
        label = self._leds.get(name)
        if label is None:
            return
        label.setText(f"● {name}: {value}")
        label.setStyleSheet(f"color: {color}; background: transparent;")
        if name == "MIC":
            self._mic_ok = value == "READY"
            self.mic_btn.setEnabled(self._mic_ok and self.send_btn.isEnabled())
            if self._mic_ok:
                if self._wake_pending and self._wake_word_enabled:
                    # wake-word mode supersedes the one-shot auto-listen
                    self._wake_pending = False
                    self._autolisten_pending = False
                    self._start_wake_loop()
                elif (self._autolisten_pending and self._auto_listen
                        and not self._wake_word_enabled):
                    self._autolisten_pending = False
                    self._schedule_autolisten()
            elif not self._mic_ok:
                # audio drivers can still be loading right after boot —
                # re-check shortly instead of giving up on auto-listen.
                if self._wake_pending and self._wake_mic_retries > 0:
                    self._wake_mic_retries -= 1
                    QTimer.singleShot(5000, self._check_mic)
                elif (self._autolisten_pending and self._mic_retries > 0):
                    self._mic_retries -= 1
                    QTimer.singleShot(5000, self._check_mic)

    # -- handlers ------------------------------------------------------------
    def _on_status(self, state):
        self.hud.set_state(state)
        self._hud_state_lbl.setText(f"STATE: {state}")
        busy = state in ("LISTENING", "THINKING", "PROCESSING")
        self.send_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.mic_btn.setEnabled(not busy and self._mic_ok)
        self.model_combo.setEnabled(not busy)

    def _on_listening_finished(self, query):
        if self._restart_wake_after_listen:
            # a manual MIC listen paused the wake loop — bring it back
            self._restart_wake_after_listen = False
            if self._wake_word_enabled and self._mic_ok:
                self.worker.start_wake_loop()
        if query:
            threading.Thread(
                target=self.worker.process, args=(query,), daemon=True
            ).start()
        else:
            # nothing heard — the conversation session ends
            self.worker._mic_convo = False
            if self.worker.wake_active:
                self._on_status("STANDBY")
            else:
                self._on_status("READY")

    def _on_toast(self, message):
        """A timer/reminder fired: log it and pop the HUD toast."""
        self.log.enqueue_line(message, "sys")
        self._toast.show_toast(message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._toast is not None:
            self._toast._position()

    def _on_processed(self):
        """After a voice-captured request finishes, open the mic again
        automatically (conversation mode) so the user never has to say
        'hey Jarvis' again. Silence ends the session and returns to
        standby."""
        if not self.worker._mic_convo:
            return
        if not (self._mic_ok and not self.assistant.is_processing):
            return
        self._schedule_convo_listen()

    def _schedule_convo_listen(self):
        """Wait for the spoken reply to finish, then capture the next
        command. Bails out if JARVIS is still talking after 30s so its own
        voice is never captured as a command."""

        def poll(waited):
            if self.assistant.tts.is_busy() and waited < 30000:
                QTimer.singleShot(200, lambda: poll(waited + 200))
                return
            if self.assistant.tts.is_busy():
                return
            if not (self.worker._mic_convo and self._mic_ok
                    and not self.assistant.is_processing):
                return
            self.log.enqueue_line("Listening for your next command...", "sys")
            self.worker.start_wake_followup_capture()

        QTimer.singleShot(500, lambda: poll(0))

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        # typing ends voice conversation mode — don't grab the mic afterwards
        self.worker._mic_convo = False
        threading.Thread(
            target=self.worker.process, args=(text,), daemon=True
        ).start()

    def _toggle_mic(self):
        # a manual listen needs the mic free — pause the wake-word loop
        # and restart it once this one-shot listen finishes. Set the
        # restart flag BEFORE stopping so _on_wake_state knows this stop
        # is a transient pause (not a real disarm) and doesn't flip the
        # WAKE button off or drop the user's wake-word setting.
        if self.worker.wake_active:
            self._restart_wake_after_listen = True
            self.worker.stop_wake_loop()
        self.worker.start_listening()

    def _schedule_autolisten(self):
        """Start listening automatically once the mic is ready.

        Waits until the spoken greeting has finished (so the mic doesn't pick
        up JARVIS's own voice), then kicks off a single listening pass.
        Bails out after ~10s if speech never becomes idle.
        """

        def poll(waited):
            if self.assistant.tts.is_busy() and waited < 10000:
                QTimer.singleShot(200, lambda: poll(waited + 200))
                return
            if (self._auto_listen and self._mic_ok
                    and not self.assistant.is_processing):
                self.log.enqueue_line(
                    "Listening for your command...", "sys")
                self.worker.start_listening(convo=True)

        QTimer.singleShot(500, lambda: poll(0))

    def _toggle_autolisten(self):
        """Toggle auto-listen-on-startup, remembered between sessions."""
        self._auto_listen = not self._auto_listen
        _save_autolisten(self._auto_listen)
        self.autolisten_btn.setText(
            "AUTO-MIC: ON" if self._auto_listen else "AUTO-MIC: OFF")
        self.autolisten_btn.setStyleSheet(
            _BTN_ON_SS if self._auto_listen else _BTN_SS)
        self.log.enqueue_line(
            "Auto-mic ON — I'll listen right after launch." if self._auto_listen
            else "Auto-mic OFF — click MIC when you want me to listen.", "sys")

    def _start_wake_loop(self):
        self.log.enqueue_line("Activating wake-word listener...", "sys")
        self.worker.start_wake_loop()

    def _toggle_wake(self):
        """Toggle the 'Hey Jarvis' wake-word listener, remembered."""
        on = not self._wake_word_enabled
        self._wake_word_enabled = on
        _save_wake(on)
        self.wake_btn.setText("WAKE: ON" if on else "WAKE: OFF")
        self.wake_btn.setStyleSheet(_BTN_ON_SS if on else _BTN_SS)
        if on:
            if self._mic_ok:
                self._start_wake_loop()
            else:
                self._wake_pending = True
                self._check_mic()
        else:
            self._wake_pending = False
            self.worker.stop_wake_loop()
            self.worker._mic_convo = False
        self.log.enqueue_line(
            "Wake-word ON — I'll only answer to 'Hey Jarvis'." if on
            else "Wake-word OFF — use the MIC button or typing instead.",
            "sys")

    def _on_wake_detected(self):
        self.log.enqueue_line("Wake word heard — listening...", "sys")

    def _on_wake_state(self, on):
        """Mirror the wake-word listener state on the WAKE button.

        A stop is not always a real turn-off: a manual MIC listen pauses
        the loop and restarts it when done (_restart_wake_after_listen),
        so that stop must NOT flip the button off or clobber the user's
        wake-word setting. Only a genuine disarm (goodbye, toggle-off or
        a failed start) updates the button and the enabled flag.
        """
        if on:
            self._wake_word_enabled = True
            self.wake_btn.setText("WAKE: ON")
            self.wake_btn.setStyleSheet(_BTN_ON_SS)
            self._on_status("STANDBY")
            self.log.enqueue_line(
                "Standing by — say 'Hey Jarvis' to wake me.", "sys")
        elif not self._restart_wake_after_listen:
            # genuine disarm — the listener stays down until re-armed
            self._wake_word_enabled = False
            self.wake_btn.setText("WAKE: OFF")
            self.wake_btn.setStyleSheet(_BTN_SS)
            if not self.assistant.is_processing:
                self._on_status("READY")

    def _on_model_changed(self, name):
        """Switch the active model used for the next question."""
        name = str(name or "")
        if name not in MODELS:
            return
        if name == self.assistant.model and name == self.assistant.ollama.model:
            return
        self.assistant.model = name
        self.assistant.ollama.model = name
        _save_model(name)
        self.log.enqueue_line(
            f"MODEL → {name}. Your next question uses {name}.", "sys")

    def _toggle_voice(self):
        tts = self.assistant.tts
        tts.muted = not tts.muted
        on = not tts.muted
        self._speaker_on = on
        _save_speaker(on)
        self.speaker_btn.setText("SPEAKER: ON" if on else "SPEAKER: OFF")
        self.speaker_btn.setStyleSheet(_BTN_ON_SS if on else _BTN_SS)
        self.hud.set_muted(not on)
        self.log.enqueue_line(
            "Voice replies ON — I'll speak my answers." if on
            else "Voice replies OFF — answers are text-only for now "
                 "(click SPEAKER to turn them back on).",
            "sys")

    def _test_voice(self):
        """Speak a test phrase so the user can verify their speakers."""
        tts = self.assistant.tts
        if not tts.tts_available:
            errs = tts.drain_errors()
            detail = f" — {errs[0]}" if errs else ""
            self.log.enqueue_line(
                f"Speech engine is not available{detail}. "
                "Check your speakers/volume, then restart the app.", "err")
            self.led_update.emit("TTS", "UNAVAILABLE", C.RED)
            return
        if tts.muted:
            self.log.enqueue_line(
                "Speaker is OFF — turn it on first, then test again.", "sys")
            return
        tts.speak("This is a test. Can you hear me?")
        self.log.enqueue_line(
            "Test phrase sent to your speakers — did you hear it?", "sys")

    def _reset(self):
        self.assistant.history = []
        self.log.clear_log()
        self.log.enqueue_line("Conversation reset — fresh start.", "sys")

    def _poll_speech(self):
        self.hud.set_speaking(self.assistant.tts.is_busy())
        # surface any engine errors so silent failures are visible
        for err in self.assistant.tts.drain_errors():
            self.log.enqueue_line(f"TTS problem: {err}", "err")
        if not self._tts_known:
            if self.assistant.tts.tts_available:
                self._tts_known = True
                self.led_update.emit("TTS", "READY", C.GREEN)
            elif time.time() - self._tts_check_start > 6:
                self._tts_known = True
                self.led_update.emit("TTS", "UNAVAILABLE", C.RED)
                self.log.enqueue_line(
                    "TTS unavailable — no spoken replies. Check that your "
                    "speakers/volume work, then restart the app.", "err")

    def _update_metrics(self):
        if psutil is None:
            return
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        self.cpu_bar.set_value(cpu, f"{cpu:.0f}%")
        self.ram_bar.set_value(mem, f"{mem:.0f}%")
        now = time.time()
        nc = psutil.net_io_counters()
        if self._net_last is not None:
            dt = now - self._net_last_t
            if dt > 0:
                mbps = (nc.bytes_sent + nc.bytes_recv
                        - self._net_last.bytes_sent - self._net_last.bytes_recv) \
                    / dt / 1e6
                self.net_bar.set_value(min(100.0, mbps * 5), f"{mbps:.1f} MB/s")
        self._net_last = nc
        self._net_last_t = now

    def closeEvent(self, event):
        try:
            self.worker._mic_convo = False
            self.worker.stop_interrupt_listener()
            self.worker.stop_wake_loop()
            if self.assistant.history:
                threading.Thread(
                    target=self.assistant.summarize_and_save_history,
                    daemon=True,
                ).start()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    botname = config("BOTNAME", default="JARVIS")
    settings = _load_settings()
    speaker_on = settings.get("speaker_on", True)
    auto_listen = settings.get("auto_listen", True)
    wake_word = settings.get("wake_word", True)
    start_model = settings.get("model", DEFAULT_MODEL)
    if start_model not in MODELS:
        start_model = DEFAULT_MODEL
    # GuiSpeech owns the TTS engine on a dedicated speech thread, so it is
    # handed to the assistant directly — no default engine is ever spun up
    # on the GUI thread.
    assistant = PersonalizedAssistant(start_model, botname, load_history(),
                                      tts=GuiSpeech())
    assistant.tts.muted = not speaker_on
    worker = AssistantWorker(assistant)
    # route timer/reminder announcements through the worker so they land in
    # the transmission log as well as the speakers
    assistant.timers.on_fire = worker.announce
    # barge-in: 'stop' while speaking cuts the reply off
    worker.start_interrupt_listener()
    window = MainWindow(assistant, worker, speaker_on, auto_listen, wake_word)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
