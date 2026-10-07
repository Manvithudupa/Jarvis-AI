"""Smoke tests: the Desktop HUD window constructs and its toggle buttons
wire up correctly.

Runs Qt fully offscreen — no window is shown, no audio is touched, no
network is used — so it exercises the real MainWindow constructor and
signal wiring (wake/speaker/auto-mic toggles, wake-state sync) without
any hardware.
"""

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# must be set before any QWidget is created (imports below create classes,
# but the application itself is created lazily in setUpClass)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from _bootstrap import make_assistant

from ui.gui import AssistantWorker, MainWindow


class FakeWakeListener:
    """Stand-in for core.wake_word.WakeWordListener — no audio engine."""

    def __init__(self, *args, **kwargs):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass


class StartupSequenceTests(unittest.TestCase):
    """Boots the REAL HUD (offscreen, no window shown) and lets the full
    startup sequence run with fake audio/network: greeting, status LEDs,
    mic readiness and wake-word arming (or auto-mic arming) — everything
    except the actual devices.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _boot(self, wake_word=True, auto_listen=True):
        assistant = make_assistant()
        worker = AssistantWorker(assistant)
        # The startup sequence fires ~350 ms later inside the Qt event
        # loop (QTimer.singleShot in _start_timers), so these patches must
        # stay active for the WHOLE test — a `with` block would have
        # reverted them before _startup ever ran and the real wake
        # listener would grab the real devices.
        patchers = [
            patch("ui.gui.autostart_enabled", return_value=False),
            patch("ui.gui.requests.get",
                  return_value=SimpleNamespace(status_code=200)),
            patch("ui.gui.have_internet", return_value=True),
            patch("ui.gui.ensure_ollama",
                  lambda timeout=30, on_status=None:
                  (on_status("Ollama is already running.")
                   if on_status else None) or True),
            patch("speech_recognition.Microphone.list_microphone_names",
                  return_value=["Fake Mic"]),
            patch("core.wake_word.WakeWordListener", FakeWakeListener),
            patch("ui.gui._save_wake"),
            patch("ui.gui._save_speaker"),
            patch("ui.gui._save_autolisten"),
            patch("ui.gui._save_model"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        window = MainWindow(assistant, worker, speaker_on=True,
                            auto_listen=auto_listen, wake_word=wake_word)
        self.window, self.worker = window, worker
        return window, worker

    def tearDown(self):
        if getattr(self, "window", None) is not None:
            w = self.window
            for t in (w._metrics_timer, w._ollama_timer,
                      w._internet_timer, w._speak_timer):
                try:
                    t.stop()
                except Exception:
                    pass
            w.close()
            self.worker.stop_interrupt_listener()
            self.worker.stop_wake_loop()
            self.window = None

    def _pump(self, pred, timeout=8.0):
        """Run the Qt event loop until ``pred`` is true."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            StartupSequenceTests.app.processEvents()
            if pred():
                return True
            time.sleep(0.01)
        return False

    def test_wake_mode_startup(self):
        window, worker = self._boot(wake_word=True, auto_listen=True)
        # Wait for the whole bring-up: mic ready -> wake loop started, the
        # async Ollama / internet probes have painted their LEDs, and the
        # log's typewriter has fully typed the last boot line ("Standing
        # by") — typing is sequential, so that also proves everything
        # before it (CORE ONLINE) made it to the screen.
        ok = self._pump(
            lambda: worker.wake_active and window._mic_ok
                    and "ONLINE" in window._leds["OLLAMA"].text()
                    and "ONLINE" in window._leds["INTERNET"].text()
                    and "Standing by" in window.log.toPlainText())
        self.assertTrue(ok, "wake loop never started during startup")

        # wake-word mode supersedes auto-mic
        self.assertTrue(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: ON")
        self.assertEqual(window._hud_state_lbl.text(), "STATE: STANDBY")
        self.assertEqual(window._leds["MIC"].text(), "● MIC: READY")
        self.assertIn("ONLINE", window._leds["OLLAMA"].text())
        self.assertIn("ONLINE", window._leds["INTERNET"].text())

        # greeting was spoken and the boot lines hit the log
        self.assertTrue(window.assistant.tts.messages)
        log_text = window.log.toPlainText()
        self.assertIn("CORE ONLINE", log_text)
        self.assertIn("Standing by", log_text)

    def test_auto_mic_mode_startup(self):
        window, worker = self._boot(wake_word=False, auto_listen=True)
        called = []
        with patch.object(window.assistant, "listen",
                          lambda: called.append(True) or None):
            ok = self._pump(
                lambda: called and not worker.listening
                        and window._hud_state_lbl.text() == "STATE: READY")
        self.assertTrue(ok, "auto-mic capture never armed and completed")

        self.assertEqual(window.wake_btn.text(), "WAKE: OFF")
        self.assertEqual(window._leds["MIC"].text(), "● MIC: READY")
        self.assertTrue(called, "the one-shot capture never ran")
        # capture finished: mic released, conversation mode not engaged
        self.assertFalse(worker.listening)
        self.assertFalse(worker._mic_convo)
        self.assertEqual(window._hud_state_lbl.text(), "STATE: READY")


class HudSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        assistant = make_assistant()
        worker = AssistantWorker(assistant)
        with patch("ui.gui.autostart_enabled", return_value=False), \
                patch.object(MainWindow, "_start_timers"), \
                patch.object(MainWindow, "_check_mic"), \
                patch("ui.gui._save_wake"), patch("ui.gui._save_speaker"), \
                patch("ui.gui._save_autolisten"), \
                patch("ui.gui._save_model"):
            window = MainWindow(assistant, worker, speaker_on=True,
                                auto_listen=True, wake_word=True)
        return window, worker

    # -- construction ------------------------------------------------------
    def test_window_constructs_with_wake_on(self):
        window, _ = self.make_window()
        self.assertTrue(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: ON")
        self.assertEqual(window.speaker_btn.text(), "SPEAKER: ON")
        self.assertEqual(window.autolisten_btn.text(), "AUTO-MIC: ON")

    # -- toggles -----------------------------------------------------------
    def test_toggle_wake_off_and_back_on(self):
        window, _ = self.make_window()
        window._toggle_wake()  # ON -> OFF
        self.assertFalse(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: OFF")
        window._toggle_wake()  # OFF -> ON
        self.assertTrue(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: ON")

    def test_toggle_voice_flips_speaker(self):
        window, _ = self.make_window()
        window._toggle_voice()
        self.assertFalse(window._speaker_on)
        self.assertEqual(window.speaker_btn.text(), "SPEAKER: OFF")
        window._toggle_voice()
        self.assertTrue(window._speaker_on)
        self.assertEqual(window.speaker_btn.text(), "SPEAKER: ON")

    # -- wake-state sync (regression for the transient-pause bug) ---------
    def test_transient_wake_stop_keeps_button_on(self):
        """A stop that is a transient pause (manual MIC listen) must not
        flip the WAKE button off or drop the user's setting."""
        window, worker = self.make_window()
        window._restart_wake_after_listen = True
        worker.wake_state.emit(False)
        self.assertTrue(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: ON")

    def test_real_wake_stop_disarms_button(self):
        """A genuine stop (goodbye / toggle-off) turns the WAKE button off."""
        window, worker = self.make_window()
        worker.wake_state.emit(False)
        self.assertFalse(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: OFF")

    def test_wake_start_turns_button_on(self):
        window, worker = self.make_window()
        worker.wake_state.emit(False)  # disarm first
        worker.wake_state.emit(True)   # loop started
        self.assertTrue(window._wake_word_enabled)
        self.assertEqual(window.wake_btn.text(), "WAKE: ON")


if __name__ == "__main__":
    unittest.main()
