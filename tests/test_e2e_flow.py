"""End-to-end conversation flow through the real assistant stack.

No hardware and no real Ollama: the model stream is a canned generator and
online dependencies are mocked, but everything else runs for real —
PersonalizedAssistant, command routing, memory, timers, prompt building and
the GUI worker's process() entry — chained into the same multi-turn session
a user would have.
"""

import time
import unittest
from unittest.mock import patch

from _bootstrap import make_assistant

from ui.gui import AssistantWorker


class FakeOllama:
    """Stand-in for StreamingOllama that streams canned replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.model = "qwen3:4b"

    def generate_stream(self, prompt):
        reply = self.replies.pop(0) if self.replies else "I'm not sure."
        return iter(list(reply))


class EndToEndFlowTests(unittest.TestCase):
    def setUp(self):
        self.assistant = make_assistant()
        self.assistant.ollama = FakeOllama([
            "Hey Sam! Great to see you.",
            "The capital of France is Paris.",
        ])
        self.assistant.timers.cancel_all()
        # never write to the real conversation-history or memory files
        # during tests (handle_command 'my name is ...' saves memory)
        patch("core.assistant.append_history").start()
        patch("core.assistant.save_memory").start()
        self.addCleanup(patch.stopall)

    # -- a realistic multi-turn session ------------------------------------
    def test_full_session_chat_and_commands(self):
        a = self.assistant

        # learns the user's name and keeps it for the rest of the session
        self.assertTrue(a.handle_command("my name is sam"))
        self.assertEqual(a.username, "sam")
        self.assertTrue(a.handle_command("i like chess"))
        self.assertIn("chess", a.memory.get("interests", []))

        # identity questions answer from memory, not the model
        a.tts.messages.clear()
        self.assertTrue(a.handle_command("who am i"))
        self.assertIn("sam", " ".join(a.tts.messages).lower())

        # quick commands (weather) with online deps mocked
        with patch("core.assistant.have_internet", return_value=True), \
                patch("core.assistant.get_city_from_ip", return_value=None), \
                patch("core.assistant.get_weather_report",
                      return_value=("sunny", "23℃", "21℃")):
            self.assertTrue(a.handle_command("what's the weather"))
        self.assertIn("23℃", " ".join(a.tts.messages))

        # timers
        self.assertTrue(a.handle_command("set a timer for 10 minutes"))
        self.assertEqual(len(a.timers.list()), 1)

        # model chat — streams a reply and speaks it
        a.ollama = FakeOllama(["The capital of France is Paris."])
        with patch("core.assistant.have_internet", return_value=True), \
                patch("core.assistant.search_on_google",
                      return_value="Paris is the capital of France."):
            a.tts.messages.clear()
            reply = "".join(a.generate_reply("tell me about paris",
                                              speak=True))
        self.assertTrue(reply.strip())
        self.assertIn("Paris", " ".join(a.tts.messages))

        # farewell saves the session and answers
        with patch.object(a, "summarize_and_save_history") as summ:
            self.assertTrue(a.handle_command("bye"))
        summ.assert_called_once()

    # -- memory flows into the prompt --------------------------------------
    def test_memory_feeds_the_prompt(self):
        a = self.assistant
        a.handle_command("my name is sam")
        a.handle_command("i like chess")
        prompt = a.build_prompt("how are you")
        self.assertIn("sam", prompt)
        self.assertIn("chess", prompt)

    # -- factual questions get web context ---------------------------------
    def test_web_enhanced_prompt_and_reply(self):
        a = self.assistant
        with patch("core.assistant.have_internet", return_value=True), \
                patch("core.assistant.search_on_google",
                      return_value="Paris is the capital of France.") as search:
            reply = "".join(a.generate_reply("who is the president of france",
                                             speak=False))
        search.assert_called_once()
        self.assertTrue(reply.strip())

    # -- the desktop GUI worker path ---------------------------------------
    def test_worker_process_streams_chat_and_history(self):
        a = self.assistant
        worker = AssistantWorker(a)
        tokens, done, errors = [], [], []
        worker.token.connect(tokens.append)
        worker.done.connect(lambda: done.append(True))
        worker.error.connect(errors.append)
        with patch("ui.gui.is_online", return_value=True):
            worker.process("hello there")
        self.assertFalse(errors)
        self.assertTrue(tokens)
        self.assertEqual(done, [True])
        self.assertIn("User: hello there", a.history)
        self.assertTrue(any(line.startswith("AI:") for line in a.history))
        self.assertFalse(a.is_processing)  # busy flag always restored

    def test_worker_wikipedia_followup(self):
        a = self.assistant
        worker = AssistantWorker(a)
        lines = []
        worker.line.connect(lambda t, tag: lines.append(t))
        worker.process("wikipedia")
        with patch("ui.gui.have_internet", return_value=True), \
                patch("ui.gui.search_on_wikipedia",
                      return_value="Albert Einstein was a physicist."):
            worker.process("einstein")
        self.assertIn("Wikipedia says", " ".join(lines))
        self.assertEqual(worker.followup, "")  # follow-up consumed

    def test_worker_command_replies_in_log(self):
        a = self.assistant
        worker = AssistantWorker(a)
        lines = []
        worker.line.connect(lambda t, tag: lines.append(t))
        with patch("core.assistant.have_internet", return_value=True), \
                patch("core.assistant.get_weather_report",
                      return_value=("sunny", "23℃", "21℃")), \
                patch("core.assistant.get_city_from_ip", return_value=None):
            worker.process("weather")
        self.assertTrue(any("23℃" in t for t in lines))

    # -- timers fire end to end through the assistant -----------------------
    def test_timer_fires_end_to_end(self):
        a = self.assistant
        a.timers.add(0.1, "ding-dong")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any("ding-dong" in m for m in a.tts.messages):
                break
            time.sleep(0.05)
        self.assertTrue(any("ding-dong" in m for m in a.tts.messages),
                        "timer never fired")


if __name__ == "__main__":
    unittest.main()
