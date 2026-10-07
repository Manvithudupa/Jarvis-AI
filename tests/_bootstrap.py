"""Shared helpers for the test suite.

Puts the project root on ``sys.path`` (so ``import core.assistant`` works no
matter how the tests are launched) and provides a silent fake TTS so no
audio or real devices are touched during tests.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeTTS:
    """Records everything 'spoken'; never touches audio hardware.

    Mirrors GuiSpeech's interface (capture / on_speak / interrupted /
    muted) so the GUI worker's log-forwarding hooks and barge-in behave
    like production instead of crashing on missing attributes.
    """

    def __init__(self):
        self.messages = []
        self.capture = False
        self.on_speak = None
        self.interrupted = False
        self.muted = False
        self.tts_available = True

    def speak(self, text):
        if self.capture and self.on_speak:
            try:
                self.on_speak(text)
            except Exception:
                pass
        if not self.muted:
            self.messages.append(text)

    def stop(self):
        pass

    def drain_errors(self):
        return []

    def is_busy(self):
        return False


def make_assistant():
    """A PersonalizedAssistant with a fake TTS and no personal memory, so
    tests are deterministic and never read/write the user's real files."""
    from core.assistant import PersonalizedAssistant
    a = PersonalizedAssistant("qwen3:4b", "JARVIS", "", tts=FakeTTS())
    a.memory = {}
    a.username = "tester"
    return a
