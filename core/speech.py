"""
SAPI5 speech engine (Windows) — a reliable drop-in for pyttsx3.

Why not pyttsx3? On many Windows systems pyttsx3's ``runAndWait()`` only
produces audio for the *first* utterance per engine instance — every
subsequent call returns instantly and silently. Driving SAPI5 directly via
win32com and calling ``SpVoice.Speak()`` in synchronous mode blocks until
each phrase is actually spoken, which is reliable for streaming TTS.

Usage (create one instance on each thread that speaks):
    speech = SapiSpeech(rate=1)
    if speech.ok:
        speech.speak("Hello there!")

Also hosts the synchronous ``Speech`` wrapper (SAPI5 + pyttsx3 fallback)
used as the default TTS; the GUI substitutes its own thread-based version.
"""

import re
import threading

try:
    import pythoncom
    import win32com.client
    _COM_OK = True
except Exception:
    _COM_OK = False

# SAPI5 reads emoji and pictographs out loud as long descriptions ("smiling
# face with smiling eyes", "black heart", "black right-pointing triangle",
# ...). Strip those — plus markdown markers and ASCII smilies — before
# speaking so only real words reach audio.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emojis & pictographs (faces, animals, food...)
    "\U00002600-\U000027BF"   # misc symbols & dingbats (☀ ★ ♥ ⚠ ✈ ...)
    "\U00002190-\U000021FF"   # arrows (← → ↑ ↓ ...)
    "\U00002300-\U000023FF"   # misc technical (⏰ ⌚ ...)
    "\U000025A0-\U000025FF"   # geometric shapes (▶ ■ ◆ ● ...)
    "\U00002B00-\U00002BFF"   # arrows & math symbols (⬅ ⬆ ⬇ ⭕ ...)
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flag emojis)
    "\U0000FE0F"              # variation selector-16 (emoji presentation)
    "\U0001F3FB-\U0001F3FF"   # skin-tone modifiers
    "\U0000200D"              # zero-width joiner
    "]+"
)
_SMILEY_RE = re.compile(r"(?i)[:;=]-?[)DdPpOo/\\*\[\]]+|[:;=]-?\(|</3")
_MD_RE = re.compile(r"[*_`~]+")
_WS_RE = re.compile(r"\s+")

# Small local models answer in markdown + LaTeX with no idea it's being read
# aloud. "$17 \times 23 = \boxed{391}$" comes out of SAPI as "dollar
# seventeen backslash times twenty three equals backslash boxed braces three
# nine one braces dollar". Translate the common operators to words and drop
# the markup so a spoken answer sounds human.
_LATEX_WORDS = (
    (re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r" \1 over \2 "),
    (re.compile(r"\\sqrt\s*\{([^{}]*)\}"), r" square root of \1 "),
    (re.compile(r"\\times\b"), " times "),
    (re.compile(r"\\cdot\b"), " times "),
    (re.compile(r"\\div\b"), " divided by "),
    (re.compile(r"\\pm\b"), " plus or minus "),
    (re.compile(r"\\approx\b"), " approximately "),
    (re.compile(r"\\%"), " percent "),
)
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")      # any leftover \command
_LATEX_DELIM_RE = re.compile(r"\$\$|\$|\\\(|\\\)|\\\[|\\\]")
_MATH_CHARS_RE = re.compile(r"[{}^_]+")
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_RE = re.compile(r"\s*\|\s*")


def strip_for_speech(text):
    """Clean text before it reaches the speech engine.

    Removes emoji/pictographs (which SAPI reads as words like "smiling face
    with smiling eyes"), ASCII smilies (``:)``, ``:D``), markdown emphasis
    markers (``**bold**``, backticks) and the markdown/LaTeX furniture small
    models sprinkle into answers (headings, bullet markers, table pipes and
    ``$...$`` math) so the assistant only speaks the actual words.
    """
    if not text:
        return ""
    cleaned = _FENCE_RE.sub(" ", text)
    for pattern, replacement in _LATEX_WORDS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = _LATEX_CMD_RE.sub(" ", cleaned)
    cleaned = _LATEX_DELIM_RE.sub(" ", cleaned)
    cleaned = _MATH_CHARS_RE.sub(" ", cleaned)
    cleaned = _MD_HEADING_RE.sub("", cleaned)
    cleaned = _MD_BULLET_RE.sub("", cleaned)
    cleaned = _TABLE_RE.sub(" ", cleaned)
    cleaned = _EMOJI_RE.sub(" ", cleaned)
    cleaned = _SMILEY_RE.sub(" ", cleaned)
    cleaned = _MD_RE.sub(" ", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned)
    return cleaned.strip()


class SapiSpeech:
    """Thin, reliable SAPI5 text-to-speech wrapper."""

    def __init__(self, rate=1):
        self.ok = False
        self.error = "win32com (pywin32) is not installed"
        if not _COM_OK:
            return
        try:
            pythoncom.CoInitialize()
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._voice.Volume = 100
            self._voice.Rate = rate
            self._pick_voice()
            self.ok = True
            self.error = None
        except Exception as e:
            self.error = str(e)

    def _pick_voice(self):
        """Prefer Microsoft David / Zira / Hazel / Mark, else the first."""
        try:
            voices = self._voice.GetVoices()
            n = voices.Count
            for pref in ("david", "zira", "hazel", "mark"):
                for i in range(n):
                    if pref in voices.Item(i).GetDescription().lower():
                        self._voice.Voice = voices.Item(i)
                        return
            if n > 0:
                self._voice.Voice = voices.Item(0)
        except Exception:
            pass

    def speak(self, text):
        """Speak synchronously — returns once the phrase has been spoken."""
        clean = strip_for_speech(text)
        if not self.ok or not clean:
            return
        self._voice.Speak(clean)

    def stop(self):
        """Immediately cut off whatever is currently being spoken."""
        if not self.ok:
            return
        try:
            # SVSFlagsAsync(1) | SVSPurgeBeforeSpeak(2) with empty text
            # stops the current utterance and purges queued ones.
            self._voice.Speak("", 3)
        except Exception as e:
            print(f"[speech] stop error: {e}")


class Speech:
    """Synchronous text-to-speech wrapper (SAPI5 primary, pyttsx3 fallback)."""

    def __init__(self):
        self.is_speaking = False
        self.speech_lock = threading.Lock()
        self.tts_available = False
        self._sapi = None
        self._engine = None
        try:
            self._sapi = SapiSpeech(rate=1)
            if self._sapi.ok:
                self.tts_available = True
                print("TTS ready")
                return
            print(f"SAPI5 unavailable ({self._sapi.error}); trying pyttsx3...")
        except Exception as e:
            print(f"TTS init error: {e}")
        # fallback: pyttsx3 (on some systems only the first utterance
        # produces audio — the SAPI5 path above is preferred)
        try:
            import pyttsx3
            self._engine = pyttsx3.init('sapi5')
            self._engine.setProperty('rate', 180)
            self._engine.setProperty('volume', 1.0)
            voices = self._engine.getProperty('voices')
            if voices and len(voices) > 1:
                self._engine.setProperty('voice', voices[-1].id)
            self.tts_available = True
            print("TTS ready")
        except Exception as e:
            print(f"TTS failed: {e}")
            self.tts_available = False

    def speak(self, text):
        if not text or not text.strip():
            return
        with self.speech_lock:
            if not self.tts_available:
                print(f"TTS unavailable. Text: {text}")
                return
            try:
                self.is_speaking = True
                print(f"Speaking: {text}")
                if self._sapi is not None:
                    self._sapi.speak(text)
                else:
                    self._engine.say(strip_for_speech(text))
                    self._engine.runAndWait()
            except Exception as e:
                print(f"TTS error: {e}")
            finally:
                self.is_speaking = False

    def stop(self):
        """Cut off current speech (used by the interrupt feature)."""
        try:
            if self._sapi is not None:
                self._sapi.stop()
            elif self._engine is not None:
                self._engine.stop()
        except Exception as e:
            print(f"TTS stop error: {e}")

    def is_busy(self):
        return self.is_speaking
