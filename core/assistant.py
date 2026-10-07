"""The J.A.R.V.I.S. assistant core: personality, chat, memory and commands.

Holds the :class:`PersonalizedAssistant` (used by the HUD and the test
suite), the system prompt, command handling, and conversation history
persistence.
"""

import os
import re
import sys
import time
from datetime import datetime

import speech_recognition as sr
from decouple import config

from .ollama import OllamaError, StreamingOllama, check_model
from .speech import Speech, strip_for_speech
from .stt import has_audible_speech, new_recognizer, transcribe
from .timers import (
    TimerManager, format_duration, parse_reminder_command,
    parse_time_reply, parse_timer_command,
)
from .memory import (
    apply_facts, clear_memory, confirmation_text, extract_facts,
    load_memory, memory_snippet, memory_summary, save_memory,
)
from functions.online_ops import (
    find_my_ip, get_city_from_ip, get_latest_news, get_random_joke,
    get_weather_report, have_internet, play_on_youtube,
    search_on_wikipedia, search_on_google
)
from functions.os_ops import (
    battery_status, looks_like_command, lock_workstation,
    media_next, media_play_pause, media_previous, media_stop,
    open_application, open_in_browser, parse_open_command, search_url,
    set_mute, set_volume, strip_politeness, take_screenshot,
    volume_down, volume_up,
)

# Data files live at the project root (not inside the package folders), so
# they keep working across code moves and stay in .gitignore.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(PROJECT_ROOT, "ai_conversation_history.txt")

def load_history():
    """Read saved conversation history."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def append_history(summary):
    """Append summary to history file."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(summary.strip() + "\n\n")


_CITY_RE = re.compile(
    r"(?:weather|temperature)\s+(?:in|at|for)\s+(.+?)\s*$",
    re.IGNORECASE,
)

# Informational / factual questions that should get automatic web context so
# the (small) local model answers from real facts instead of guessing or
# claiming it's "a coding assistant".
_FACTUAL_RE = re.compile(
    r"\btell\s+me\b.{0,20}\b(?:about|on|regarding)\b|"
    r"\b(?:give|gimme|get|show|find)\s+me\b.{0,30}\b"
    r"(?:information|info|details|facts)\b|"
    r"\binformation\s+(?:on|about|regarding)\b|"
    r"\b(?:details|facts|info)\s+(?:about|on|regarding)\b|"
    r"\b(?:biography|history)\s+of\b|"
    r"\bwho\s+(?:is|was|are|were|won|invented|discovered|wrote|"
    r"created|founded|invented|scored|plays|directed)\s+"
    r"(?!my\b|your\b|you\b|we\b)|"
    r"\bwhen\s+(?:did|was|does|is|will)\s+(?!i\b|we\b|you\b)|"
    r"\bwhere\s+(?:is|was|are)\s+(?!my\b|your\b|i\b|the\s+nearest\b)|"
    r"\bhow\s+(?:many|much|tall|old|far|long\s+ago|deep|high|big)\b|"
    r"\b(?:define|explain)\b|"
    r"\bwhat(?:'s|\s+(?:is|are|was|were))\s+"
    r"(?!my\b|your\b|this\b|that\b|it\b|up\b|these\b|we\b)",
    re.IGNORECASE,
)

# Facts that change over time — presidents, versions, prices, champions. A
# local model's training data is ALWAYS stale for these, so (unlike the
# general-knowledge patterns above) they get web context every time.
_TIMELY_RE = re.compile(
    r"\b(?:current|currently|latest|newest|most recent|recently|nowadays|"
    r"this (?:year|week|month)|right now|as of|release date|price of|"
    r"stock price|match score|who won|who's winning|news about|"
    r"what(?:'s| is) the (?:current|latest|newest))\b",
    re.IGNORECASE,
)

# Casual openers and small talk that look like questions but answer better
# from the model itself than from a web snippet.
_CASUAL_RE = re.compile(
    r"^\s*(?:how are you|how's it going|how are things|what's up|"
    r"thanks|thank you|good (?:morning|afternoon|evening)|hello|hi|hey|yo)\b",
    re.IGNORECASE,
)

# Identity questions where the model should describe itself / the user —
# never web-search these.
_IDENTITY_RE = re.compile(
    r"\bwho\s+are\s+you\b|"
    r"\btell\s+me\s+about\s+(?:you|yourself|yours|my|your)\b",
    re.IGNORECASE,
)


def parse_city_from_query(query):
    """Pull a city out of 'weather in london today' or "what's the weather
    today in new york". Returns None when no city is mentioned."""
    m = _CITY_RE.search(query or "")
    if not m:
        return None
    city = m.group(1).strip()
    # drop trailing words that aren't part of the city name
    city = re.sub(
        r"\b(today|right now|now|please|tomorrow|this week|this afternoon|"
        r"this evening|tonight|currently)\b.*$",
        "", city, flags=re.IGNORECASE).strip()
    return city or None


# Phrases that interrupt J.A.R.V.I.S. mid-sentence (barge-in). The whole
# transcript must be one of these, so ordinary speech never cuts it off.
_STOP_PHRASES = {
    "stop", "stop it", "stop that", "stop talking", "stop speaking",
    "shut up", "shut it", "be quiet", "quiet", "quiet please",
    "cancel", "cancel that", "enough", "that's enough", "thats enough",
    "cut it out", "cut that out", "silence", "hush", "enough already",
}


def is_stop_phrase(text):
    """True when the whole transcript is a 'stop talking' style phrase.
    Case-insensitive and strict on purpose so J.A.R.V.I.S.'s own replies
    are never mistaken for an interrupt ('stop the car' is chat, not a
    barge-in)."""
    t = (text or "").lower().strip()
    t = re.sub(r"[^a-z' ]", " ", t)  # drop punctuation (incl. commas)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return False
    if t in _STOP_PHRASES:
        return True
    # tolerate small stumbles: "stop", "stop now", "stop please",
    # "stop it now" — but never longer phrases like "stop the car"
    if t.startswith("stop"):
        words = t.split()
        # "stop" alone, or a tiny stumble like "stop now" / "stop please"
        if len(words) == 1 and t == "stop":
            return True
        if len(words) == 2 and words[1] in ("it", "that", "now", "please"):
            return True
    return False


def is_farewell(text):
    """True when the message is a plain goodbye, not a command or question
    that merely contains a 'bye' word.

    'bye', 'goodbye jarvis' and 'ok see you' end the chat, but 'play bye
    bye bye on youtube', 'search for goodbyes in movies' and 'what does bye
    mean' must keep going. A farewell is short, has no command prefix and
    isn't a question.
    """
    t = strip_politeness((text or "").strip().lower())
    if not t or looks_like_command(t):
        return False
    if len(t.split()) > 4:
        return False
    if re.match(
        r"^(what|when|why|how|where|who|which|do|does|did|is|are|"
        r"can|could|will|would|tell|explain|define|search|play|open)\b",
        t,
    ):
        return False
    return bool(re.search(
        r"\b(bye|goodbye|good\s+night|see\s+ya|see\s+you)\b", t))


# ---------------------------------------------------------------------------
# Safe printing — consoles that can't encode emoji/℃ (cp1252) must never
# crash the assistant, even when running outside main.py's utf-8 setup.
# ---------------------------------------------------------------------------
def _safe_print(*args, **kwargs):
    """print() that never raises on characters the console can't encode."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        kwargs.pop("file", None)
        safe = []
        for a in args:
            safe.append(a.encode(enc, "replace").decode(enc)
                        if isinstance(a, str) else a)
        print(*safe, **kwargs)


# ---------------------------------------------------------------------------
# Quick-command matching ("weather", "joke", "news", ...)
# ---------------------------------------------------------------------------
# A plain substring check makes ordinary chat trigger commands: "i read the
# newspaper" fires news, "what about the weatherman" fires weather, "you're
# a joke" fires joke. So a quick command only counts when the keyword is a
# whole word (plurals allowed) AND the phrase clearly requests it — the
# keyword leads, the message is short, or a request word sits just before
# it. Statements ("this weather is nice", "you're a joke") fall through to
# normal chat.
_QUICK_PLURALS = {"joke": "jokes", "screenshot": "screenshots"}

_QUICK_REQUEST_BEFORE = {
    "what", "whats", "what's", "how", "hows", "how's", "tell", "give",
    "gimme", "get", "show", "check", "any", "play", "another", "latest",
    "top", "current", "today", "today's", "todays", "some", "about",
    "update", "headlines", "hear", "want", "need", "would", "can",
    "could", "do", "does", "is", "are",
}

_QUICK_STATEMENT_STARTS = (
    "you're", "you are", "i'm", "i am", "this is", "that is", "it's",
    "it is", "there is", "there are", "i like", "i love", "i hate",
    "i read", "i saw", "i heard", "i think", "i know",
)


def _is_quick_command(text, key):
    """True when ``text`` is a REQUEST for the quick command ``key``."""
    t = strip_politeness(text).strip(" \t.,!?")
    if not t:
        return False
    low = t.lower()
    # multi-word keys ("ip address", "my ip") are unambiguous as-is
    if " " in key:
        return bool(re.search(r"\b" + re.escape(key) + r"\b", low))
    if low.startswith(_QUICK_STATEMENT_STARTS):
        return False
    words = low.split()
    idx = None
    for i, w in enumerate(words):
        if w == key or w == _QUICK_PLURALS.get(key, ""):
            idx = i
            break
    if idx is None:
        return False
    if idx == 0 or len(words) <= 3:
        return True
    return any(w in _QUICK_REQUEST_BEFORE
               for w in words[max(0, idx - 3):idx])

class PersonalizedAssistant:
    def __init__(self, model_name, botname, history_text, tts=None):
        self.botname = botname
        self.tts = tts or Speech()
        self.is_processing = False
        self.model = model_name
        self.ollama = StreamingOllama(model=self.model)
        # Timers & reminders fire on a background daemon thread.
        self.timers = TimerManager(on_fire=self._timer_fired)
        self.history = []
        self.history_text = history_text
        # Persistent personal memory — facts the user shared in past chats
        # (name, interests, favorites...) override the .env fallback name.
        self.memory = load_memory()
        mem_name = self.memory.get("name")
        self.username = mem_name or config('USER', default='Friend')
        # FRIENDLY, CASUAL, HELPFUL PROMPT
        self.prompt_template = (
            f"{self.history_text}\n"
            "You are {botname}, a general-purpose personal assistant and friendly sidekick "
            "for {username}. You are NOT a coding assistant, NOT a programming tool, and NOT "
            "a chatbot that only talks about code. Never say things like \"I am a coding "
            "assistant\", \"I am an AI assistant\", or \"I can't help with that\". You can "
            "and should answer ANY question — general knowledge, history, politics, science, "
            "sports, celebrities, travel, food, anything. "
            "The person talking to you is the user, and their name is {username}. "
            "Be warm, casual, and supportive—like a buddy who always has their back. "
            "Your tone should be fun, easygoing, and helpful, not too formal or robotic. "
            "Keep your answers short (1-2 sentences) for casual chat, but when the user asks "
            "for detailed or complete information (like \"give me all information on X\"), "
            "give a thorough, well-organized answer with several sentences or short paragraphs. "
            "When the prompt includes a [Web info about ...] block, base your answer on that "
            "information and summarize it in your own words — don't make up facts that aren't in it. "
            "IMPORTANT: when the user asks about themselves (like \"who am I\" or \"what is my "
            "name\"), answer about the USER ({username}) — never describe yourself for those "
            "questions. Only describe yourself when they ask \"who are you\".\n"
            "{memory}\n"
            "{ongoing}\nUser: {question}\nAI:"
        )

    def listen(self):
        """Capture one spoken command, returned lowercased (or None).

        Never raises: a missing/unavailable microphone, listening timeouts
        and unrecognized speech all just return None so callers (HUD, wake
        word) never crash on mic problems.
        """
        recognizer = new_recognizer()
        try:
            with sr.Microphone() as source:
                print('Listening...')
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.7)
                    # calibration lands around ambient*ratio — floor it so
                    # quiet voices still cross the threshold (the listener
                    # pauses on anything below it, so a sane floor also keeps
                    # phrase-ending silence detectable)
                    recognizer.energy_threshold = max(
                        recognizer.energy_threshold, 60)
                    audio = recognizer.listen(source, timeout=5,
                                              phrase_time_limit=5)
                    print('Recognizing...')
                    # local whisper first (offline), Google as fallback.
                    # transcribe() never raises: it returns None for silence,
                    # noise and unrecognized speech alike.
                    query = transcribe(audio, recognizer, language='en-in')
                    if query:
                        print(f"You said: {query}")
                        return query.lower()
                    # Nothing was understood. Only apologize when the mic
                    # actually caught speech-like sound — ambient noise that
                    # tripped the VAD shouldn't trigger a spoken apology (or
                    # a random "heard" command).
                    if has_audible_speech(audio):
                        print("Didn't understand audio")
                        self.tts.speak(
                            "Sorry, I couldn't catch that. Mind saying it again?")
                    else:
                        print("No speech heard")
                except sr.WaitTimeoutError:
                    print("Timeout")
                except Exception as e:
                    print(f"Recognition error: {e}")
        except Exception as e:
            print(f"Mic unavailable: {e}")
        return None

    def build_prompt(self, question):
        """Build the full prompt for a question, adding web context when useful.

        Web context is fetched for explicit "google X" requests AND automatically
        for factual questions ("tell me about X", "who is X", "give me information
        on X"...) so the small local model answers from real web facts.
        """
        ongoing = "\n".join(self.history[-6:])
        lower_q = (question or "").lower()
        explicit_search = (lower_q.startswith("google ")
                           or lower_q.startswith("search on google"))

        # Web context addition — explicit "google ..." wins; otherwise auto-search
        # factual/informational questions.
        google_query = None
        if explicit_search:
            google_query = (question.partition(" ")[2]
                            if lower_q.startswith("google ")
                            else question.partition("search on google")[2].strip())
        elif self._looks_factual(question):
            google_query = question

        google_context = ""
        if google_query:
            if have_internet():
                self.speak("Give me a sec — looking that up for you!")
                google_results = search_on_google(google_query)
                # only inject real results; never the failure placeholder
                if google_results and google_results != "No results found on the web.":
                    google_context = f"\n[Web info about '{google_query}']:\n{google_results}\n"
            elif explicit_search:
                # explicit "google X" request — say search is unavailable;
                # auto-searched factual questions just fall back to the model
                self.speak("You're offline, so I can't search the web right now — "
                           "I'll answer from what I know.")

        # NOTE: str.format inserts values verbatim, so braces in the user's
        # question (e.g. "what is a python dict {}") are safe as-is.
        return self.prompt_template.format(
            botname=self.botname,
            username=self.username,
            memory=memory_snippet(self.memory),
            ongoing=ongoing + google_context,
            question=question
        )

    def _looks_factual(self, question):
        """True when the question is informational ("tell me about X", "who is X",
        "give me information on X", "what's the latest X") and would benefit
        from web context.

        Questions about the user/assistant themselves and casual small talk
        are excluded — those are answered from memory and the model.
        """
        q = question or ""
        if _IDENTITY_RE.search(q) or _CASUAL_RE.search(q):
            return False
        return bool(_FACTUAL_RE.search(q) or _TIMELY_RE.search(q))

    def generate_reply(self, question, speak=True, paced=True):
        """Yield reply tokens one at a time, speaking them in sentence chunks.

        A generator so callers (CLI, GUI) can stream tokens as they arrive.
        Speech starts as soon as a phrase or sentence forms: the buffer flushes
        on sentence punctuation (period / exclamation / question), on softer
        breaks (commas, semicolons, colons, dashes, newlines) once a minimum
        length is reached, or after ``max_chunk`` characters so a long run of
        unpunctuated text never delays audio for long. When ``paced`` is True
        the generator waits for each chunk to finish speaking (good for the
        CLI); set it to False when another thread owns the speech queue so
        tokens flow at model speed.
        """
        prompt = self.build_prompt(question)
        buffer = ""
        hard_ends = {".", "!", "?"}
        soft_ends = {",", ";", ":", "\n", "\u2014", "\u2013"}  # , ; : newline — –
        min_chunk = 14     # don't speak tiny fragments like "Okay," alone
        max_chunk = 100    # force a flush so audio never lags far behind

        def speak_chunk(chunk):
            self.tts.speak(chunk)
            if paced:
                while self.tts.is_busy():
                    time.sleep(0.07)

        def flush(force=False):
            nonlocal buffer
            chunk = buffer.strip()
            buffer = ""
            if chunk and (force or len(chunk) >= min_chunk):
                speak_chunk(chunk)

        try:
            for token in self.ollama.generate_stream(prompt):
                _safe_print(token, end="", flush=True)
                yield token
                if speak:
                    buffer += token
                    last = buffer[-1] if buffer else ""
                    if last in hard_ends:
                        flush(force=True)      # complete sentence — always speak
                    elif last in soft_ends and len(buffer) >= min_chunk:
                        flush()                # natural pause, long enough
                    elif len(buffer) >= max_chunk:
                        flush(force=True)      # long run — don't stall audio
        except OllamaError as e:
            # Ollama offline / model missing: never leak a raw error token
            # to the screen or speakers — say something helpful instead.
            _safe_print(f"\n[assistant] Ollama unavailable: {e}")
            friendly = self._ollama_fallback_message()
            yield friendly
            if speak:
                buffer = friendly
        if speak and buffer.strip():
            flush(force=True)

    def _ollama_fallback_message(self):
        """A friendly, actionable reply when the local AI engine is down."""
        status, _ = check_model(self.model)
        if status == "offline":
            return ("The local AI engine (Ollama) isn't responding. "
                    "Start Ollama, then try asking me again.")
        if status == "model-missing":
            return (f"I can't find the AI model {self.model!r} on Ollama. "
                    f"Open a terminal and run: ollama pull {self.model}.")
        return ("Hmm, I hit a glitch talking to my brain. "
                "Try asking me again in a moment.")

    def speak(self, text):
        # some consoles (cp1252) can't print emoji like ⏰ or ℃ — never crash
        _safe_print(f"{self.botname}: {text}")
        self.tts.speak(text)

    def wait_for_speech_completion(self):
        while self.tts.is_busy():
            time.sleep(0.1)

    def _offline(self, action):
        """Speak a friendly notice when an online-only feature is asked for
        while the machine has no internet."""
        self.speak(
            f"Sorry, you're offline right now, so I can't {action}. "
            "I can still chat, open apps, and take screenshots.")

    def weather_report(self, query=None):
        """Report the weather for a city mentioned in the query, or the
        location detected from the public IP. Never fails hard: a key-free
        wttr.in fallback answers even with no API keys configured."""
        if not have_internet():
            self._offline("check the weather")
            return
        # weather_report can run nested inside handle_command while the
        # caller already holds is_processing=True (the GUI worker) —
        # save/restore it instead of forcing False, so the busy guard never
        # drops mid-request and a second request can't sneak in.
        was_processing = self.is_processing
        try:
            self.is_processing = True
            city = parse_city_from_query(query) if query else None
            if not city:
                city = get_city_from_ip()
            weather, temperature, feels_like = get_weather_report(city)
            place = city or "your area"
            # get_weather_report already includes the ℃ symbol
            report = (f"It's {temperature} in {place} and feels like "
                      f"{feels_like}. {weather.capitalize()} vibes today!")
            self.speak(report)
        except Exception as e:
            self.speak("Oops, couldn't get the weather right now. Blame the clouds!")
            print(f"Weather error: {e}")
        finally:
            self.is_processing = was_processing

    def _handle_open_command(self, kind, target, browser):
        """Execute a parsed open/search command with spoken feedback."""
        where = f" in {browser}" if browser else ""
        if kind == "search":
            target = (target or "").strip()
            if not target:
                return
            self.speak(f"Searching for {target}{where} — hold on!")
            ok = open_in_browser(search_url(target), browser)
            if not ok:
                self.speak(
                    f"Couldn't find {browser} on this machine. Check your install?")
            return
        # kind == "app": an application or a website
        self.speak(f"Opening {target}{where} — on it!")
        ok = open_application(target, browser)
        if not ok:
            self.speak(f"Hmm, I couldn't find {target} on this machine.")

    def summarize_and_save_history(self):
        """Summarize conversation and save to file."""
        if not self.history:
            return
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        summary_prompt = (
            "Summarize the following chat in a chill, friendly way (3-5 lines), and start with the date/time (YYYY-MM-DD HH:MM):\n"
            f"Date: {date_str}\n"
            + "\n".join(self.history[-12:])
        )
        summary = ""
        try:
            for token in self.ollama.generate_stream(summary_prompt):
                summary += token
        except Exception as e:
            # never crash a background thread when Ollama is down
            print(f"[assistant] history summary skipped: {e}")
            return
        if not summary.strip():
            return
        summary_with_date = f"{date_str} - {summary.strip()}"
        try:
            append_history(summary_with_date)
        except Exception as e:
            print(f"[assistant] could not save history: {e}")
            return
        _safe_print(f"\n[History saved]\n{summary_with_date}\n")

    def handle_command(self, query):
        """Handle non-chat commands. Return True if handled, else False."""
        lowered = (query or "").lower()

        # 0. identity questions — the small local model often describes ITSELF
        # when asked "who am I", so answer these directly from the user's name.
        idq = strip_politeness(lowered)
        if re.search(
            r"\bwho (am|i am)\b|\bwhat('s| is) my name\b|"
            r"do you know (who i am|my name)|tell me my name",
            idq,
            flags=re.IGNORECASE,
        ):
            self.speak(
                f"You're {self.username}, of course! My favourite human. "
                f"Anything for you, {self.username}?"
            )
            return True

        # 0b. personal memory — recall or wipe the facts JARVIS knows
        if re.search(
            r"what do you know about me|what do you remember about me|"
            r"do you remember (me|my name)",
            idq,
        ):
            self.speak(memory_summary(self.memory))
            return True

        # forget command — but never fire on negated phrasings like
        # "don't forget about me" (that's an ordinary chat message)
        if not re.search(
            r"\b(don't|dont|do not|never)\s+forget\b", idq
        ):
            if re.search(
                r"\bforget everything\b|\bclear (your|the) memory\b|"
                r"\berase (your|the) memory\b|\bforget about me\b",
                idq,
            ):
                if clear_memory():
                    self.memory = {}
                    self.speak("Memory wiped! I know nothing about you again. "
                               "Refreshing!")
                else:
                    self.speak("Couldn't clear my memory. Try again?")
                return True

        # 1. natural-language launcher: "open github in brave",
        #    "search for best laptops in chrome", "can you open notepad"...
        parsed = parse_open_command(lowered)
        if parsed:
            try:
                self._handle_open_command(*parsed)
            except Exception as e:
                print(f"Open command error: {e}")
                self.speak("Couldn't open that. My bad!")
            return True

        # 2. fixed quick commands (matched only when clearly requested —
        #    casual mentions like "this weather is nice" stay chat)
        commands = {
            'ip address': (self._report_ip, None),
            'my ip': (self._report_ip, None),
            'weather': (lambda: self.weather_report(query), None),
            'joke': (self._report_joke, None),
            'news': (self.report_news, None),
            'screenshot': (self._announce_screenshot, "Screenshot time! Hold still..."),
        }
        # a "play X on youtube" request must reach YouTube, never a quick
        # command whose keyword happens to sit inside X ("play some news on
        # youtube" used to read out the news instead of playing it)
        if not re.search(r"\bon\s+youtube\b", lowered):
            for key, (func, announce) in commands.items():
                if _is_quick_command(lowered, key):
                    if announce:
                        self.speak(announce)
                    try:
                        func()
                    except Exception as e:
                        self.speak(
                            f"Couldn't run that command: {key}. My bad!")
                        _safe_print(f"Command error ({key}): {e}")
                    return True

        # 3. system & media control (volume / mute / lock / battery / keys)
        if re.search(
            r"\b(?:turn\s+up\b|turn\s+(?:it|the\s+volume|volume)\s+up\b|"
            r"volume\s+up\b|increase\s+(?:the\s+)?volume\b|louder\b|"
            r"raise\s+(?:the\s+)?volume\b)", lowered):
            self._do_volume("up")
            return True
        if re.search(
            r"\b(?:turn\s+down\b|turn\s+(?:it|the\s+volume|volume)\s+down\b|"
            r"volume\s+down\b|decrease\s+(?:the\s+)?volume\b|quieter\b|"
            r"lower\s+(?:the\s+)?volume\b)", lowered):
            self._do_volume("down")
            return True
        m = re.search(
            r"\b(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})"
            r"\s*(?:percent|%)?\b", lowered)
        if m:
            self._do_volume("level", int(m.group(1)))
            return True
        if "unmute" in lowered:
            self._do_mute("off")
            return True
        if re.search(r"\bmute\b", lowered):
            self._do_mute("on")
            return True
        if re.search(
            r"\block\s+(?:the\s+)?(?:pc|computer|laptop|screen|workstation)\b",
            lowered,
        ):
            self._do_lock()
            return True
        if re.search(
            r"\bbattery\s+(?:percentage|percent|level|status|left|remaining|"
            r"life|charge)\b|\bhow\s+much\s+(?:battery|charge|power)\b|"
            r"\bbattery\s+left\b",
            lowered,
        ):
            self._report_battery()
            return True
        if re.search(
            r"\b(?:next|skip)\s+(?:track|song)\b|\bnext\s+one\b", lowered):
            self._do_media("next")
            return True
        if re.search(r"\b(?:previous|prev|last)\s+(?:track|song)\b",
                     lowered):
            self._do_media("previous")
            return True
        if re.search(r"\b(?:stop|halt)\s+(?:the\s+)?(?:music|song|audio)\b",
                     lowered):
            self._do_media("stop")
            return True
        if (re.search(r"\bpause\b|\bresume\b|\bunpause\b|"
                      r"\bcontinue\s+(?:playing|the\s+music)\b", lowered)
                or re.fullmatch(
                    r"(?:please\s+)?play(?:\s+(?:some\s+|the\s+)?"
                    r"(?:music|a\s+song|songs|audio))?",
                    strip_politeness(query), flags=re.IGNORECASE)):
            self._do_media("play_pause")
            return True

        # 4. timers & reminders
        parsed = parse_timer_command(lowered)
        if parsed:
            seconds, _unit = parsed
            self.timers.add(seconds, "⏰ Time's up! Your timer's done.")
            self.speak(
                f"Timer set for {format_duration(seconds)}. I'll ping you "
                "when it's up!")
            return True
        parsed = parse_reminder_command(lowered)
        if parsed:
            task, seconds = parsed
            msg = f"⏰ Reminder: {task}" if task else "⏰ Time's up!"
            self.timers.add(seconds, msg)
            self.speak(
                f"Got it — I'll remind you{(' to ' + task) if task else ''} "
                f"in {format_duration(seconds)}.")
            return True
        if "remind me" in lowered:
            # a reminder with no time — ask for it, then parse the reply
            m_task = re.search(r"\bremind\s+me\s+to\s+(.+?)\s*$", lowered)
            task = m_task.group(1).strip().strip(".,!?") if m_task else ""
            self.speak("Sure! When should I remind you?")
            self.wait_for_speech_completion()
            when = self.listen()
            if when:
                seconds = parse_time_reply(when)
                if seconds:
                    msg = f"⏰ Reminder: {task}" if task else "⏰ Time's up!"
                    self.timers.add(seconds, msg)
                    self.speak(f"Done — reminder set for "
                               f"{format_duration(seconds)}.")
                else:
                    self.speak("Sorry, I didn't catch the time. Try "
                               "'remind me to X in N minutes'.")
            return True
        if re.search(r"\b(?:what|any)\s+(?:timers?|reminders?)\b|"
                     r"\b(?:timers?|reminders?)\s+(?:active|set|running)\b",
                     lowered):
            self._report_timers()
            return True
        if re.search(r"\b(?:cancel|clear|delete|remove)\s+(?:all\s+)?"
                     r"(?:timers?|reminders?)\b", lowered):
            self.timers.cancel_all()
            self.speak("All timers and reminders cleared.")
            return True

        # 5. "play <song> on youtube" -> play it right away (no follow-up Q)
        clean = strip_politeness(query)
        if re.match(r"^(please\s+)?play\s+", clean, flags=re.IGNORECASE):
            if not have_internet():
                self._offline("play YouTube videos")
                return True
            video = re.sub(r"^(please\s+)?play\s+", "", clean,
                           flags=re.IGNORECASE)
            video = re.sub(r"\s+(on\s+)?youtube\s*$", "", video).strip()
            if video and video.lower() != clean.lower():
                self.speak(f"Playing {video} on YouTube—enjoy!")
                play_on_youtube(video)
                return True

        if 'wikipedia' in lowered:
            if not have_internet():
                self._offline("look things up on Wikipedia")
                return True
            self.speak('What should I look up on Wikipedia, friend?')
            self.wait_for_speech_completion()
            search_query = self.listen()
            if search_query:
                try:
                    results = search_on_wikipedia(search_query)
                    short_result = results[:200] + "..." if len(results) > 200 else results
                    self.speak(f"Wikipedia says: {short_result}")
                except Exception:
                    self.speak("Wikipedia's not playing nice right now.")
            return True

        if 'youtube' in lowered:
            if not have_internet():
                self._offline("play YouTube videos")
                return True
            self.speak('What do you want to jam to on YouTube?')
            self.wait_for_speech_completion()
            video = self.listen()
            if video:
                self.speak(f"Playing {video} on YouTube—enjoy!")
                play_on_youtube(video)
            return True

        if is_farewell(query):
            self.speak(f'See ya, {self.username}! Ping me anytime!')
            self.summarize_and_save_history()
            # Never exit() from a library method — the GUI owns the process
            # lifecycle and intercepts farewells before commands. Exiting
            # here would kill the whole app mid-use.
            return True

        # 6. personal memory — learn facts the user shares ("my name is X",
        #    "i like football") and save them for future sessions. Runs last
        #    so every real command above takes precedence.
        learned = extract_facts(idq)
        if learned:
            applied = apply_facts(self.memory, learned)
            if applied:
                save_memory(self.memory)
                if self.memory.get("name"):
                    self.username = self.memory["name"]
                self.speak(confirmation_text(applied))
            else:
                # nothing new (already known, or at the interest cap) —
                # acknowledge instead of silence
                self.speak("Got it!")
            return True

        return False

    def report_news(self):
        if not have_internet():
            self._offline("fetch the news")
            return
        try:
            self.speak("Let me grab some news headlines for you...")
            news = get_latest_news()
            if news:
                self.speak(f"Here's one: {news[0]}")
        except Exception:
            self.speak("Couldn't fetch news. I blame the internet goblins!")

    def _report_joke(self):
        if not have_internet():
            self._offline("tell you a joke")
            return
        try:
            self.speak(get_random_joke())
        except Exception as e:
            print(f"Joke error: {e}")
            self.speak("Couldn't fetch a joke. I blame the internet goblins!")

    def _report_ip(self):
        """Speak the public IP, or a friendly message when offline."""
        ip = find_my_ip()
        if ip:
            self.speak(f"Your IP address is {ip}. Don't worry, I won't leak it!")
        else:
            self.speak("Couldn't fetch your IP — looks like you're offline.")

    def _announce_screenshot(self):
        """Take a screenshot; speak a short confirmation, log the path."""
        path = take_screenshot()
        if path:
            print(f"Screenshot saved to {path}")
            self.speak("Screenshot saved — check the screenshots folder!")
        else:
            self.speak("Screenshot failed — couldn't capture the screen.")

    # -- system & media control -------------------------------------------
    def _do_volume(self, direction, level=None):
        """Volume up/down (media keys) or set an exact level (pycaw)."""
        if direction == "up":
            ok = volume_up()
            self.speak("Volume up!" if ok
                       else "Couldn't turn the volume up.")
        elif direction == "down":
            ok = volume_down()
            self.speak("Volume down." if ok
                       else "Couldn't turn the volume down.")
        else:
            level = max(0, min(100, int(level or 50)))
            if set_volume(level):
                self.speak(f"Volume set to {level} percent.")
            else:
                self.speak("I can't set an exact volume level on this "
                           "machine — try 'volume up' or 'volume down' "
                           "instead.")

    def _do_mute(self, state):
        # set_mute is exact when pycaw is installed; falls back to the mute
        # media key (toggle) otherwise
        if set_mute(state == "on"):
            self.speak("Muted!" if state == "on" else "Unmuted — "
                       "speaking again!")
        else:
            self.speak("Couldn't change the mute state.")

    def _do_lock(self):
        if lock_workstation():
            self.speak("Locking the computer. See you in a bit!")
        else:
            self.speak("Couldn't lock the computer.")

    def _report_battery(self):
        status = battery_status()
        if status is None:
            self.speak("I can't read a battery — this looks like a "
                       "desktop without one.")
        else:
            pct, plugged = status
            if plugged:
                self.speak(f"You're at {pct} percent and charging.")
            else:
                self.speak(f"You're at {pct} percent on battery.")

    def _do_media(self, action):
        fns = {"play_pause": media_play_pause, "next": media_next,
               "previous": media_previous, "stop": media_stop}
        fn = fns.get(action)
        if fn and fn():
            words = {"play_pause": "playing/pausing", "next": "skipping",
                     "previous": "going back", "stop": "stopping"}
            self.speak(f"{words[action].capitalize()} the media.")
        else:
            self.speak("Couldn't control media on this machine.")

    # -- timers & reminders ------------------------------------------------
    def _timer_fired(self, message):
        """Called by TimerManager when a timer/reminder is due."""
        try:
            self.speak(message)
        except Exception as e:
            print(f"[timers] announce failed: {e}")

    def _report_timers(self):
        items = self.timers.list()
        if not items:
            self.speak("No timers or reminders are set right now.")
            return
        parts = [f"{format_duration(s)}: {msg}" for s, msg in items]
        self.speak("Here's what's coming up: " + "; ".join(parts) + ".")
