import importlib
import os
import unittest

import core.ollama as ollama_streaming


class ModelConfigTests(unittest.TestCase):
    """MODEL / BIG_MODEL come from the environment (or .env) via decouple."""

    def setUp(self):
        self._backup = {k: os.environ.get(k) for k in ("MODEL", "BIG_MODEL")}
        os.environ.pop("MODEL", None)
        os.environ.pop("BIG_MODEL", None)

    def tearDown(self):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _reload(self):
        importlib.reload(ollama_streaming)
        return ollama_streaming

    def test_defaults_when_unset(self):
        o = self._reload()
        self.assertEqual(o.DEFAULT_MODEL, "qwen3:4b")
        self.assertEqual(o.BIG_MODEL, "qwen3:4b")

    def test_model_override_propagates_to_big(self):
        os.environ["MODEL"] = "qwen3:4b"
        o = self._reload()
        self.assertEqual(o.DEFAULT_MODEL, "qwen3:4b")
        # BIG_MODEL falls back to MODEL when not set
        self.assertEqual(o.BIG_MODEL, "qwen3:4b")

    def test_big_model_override_wins(self):
        os.environ["MODEL"] = "qwen3:4b"
        os.environ["BIG_MODEL"] = "qwen3:14b"
        o = self._reload()
        self.assertEqual(o.DEFAULT_MODEL, "qwen3:4b")
        self.assertEqual(o.BIG_MODEL, "qwen3:14b")

    def test_empty_big_model_falls_back(self):
        os.environ["MODEL"] = "qwen3:4b"
        os.environ["BIG_MODEL"] = ""
        o = self._reload()
        self.assertEqual(o.BIG_MODEL, "qwen3:4b")


class OllamaStreamErrorTests(unittest.TestCase):
    """generate_stream must raise OllamaError (never yield a raw error
    token), and check_model must diagnose offline / missing-model cases."""

    def test_generate_stream_raises_ollama_error(self):
        from unittest.mock import patch
        with patch("core.ollama.requests.post",
                   side_effect=Exception("connection refused")):
            gen = ollama_streaming.StreamingOllama(
                model="qwen3:4b").generate_stream("hi")
            with self.assertRaises(ollama_streaming.OllamaError):
                next(gen)

    def test_generate_stream_raises_on_http_error(self):
        from unittest.mock import MagicMock, patch
        import requests
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404", response=resp)
        with patch("core.ollama.requests.post", return_value=resp):
            gen = ollama_streaming.StreamingOllama(
                model="qwen3:4b").generate_stream("hi")
            with self.assertRaises(ollama_streaming.OllamaError):
                next(gen)

    def test_check_model_online(self):
        from unittest.mock import patch
        with patch("core.ollama.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {
                "models": [{"name": "qwen3:4b"}, {"name": "qwen3:8b"}]}
            self.assertEqual(
                ollama_streaming.check_model("qwen3:4b")[0], "ok")
            self.assertEqual(
                ollama_streaming.check_model("llama3")[0], "model-missing")

    def test_check_model_offline(self):
        from unittest.mock import patch
        with patch("core.ollama.requests.get",
                   side_effect=Exception("down")):
            self.assertEqual(
                ollama_streaming.check_model("qwen3:4b")[0], "offline")


class RequestPayloadTests(unittest.TestCase):
    """The request body must never send a `think` flag, and streaming must
    yield the answer ONLY — never the model's reasoning.

    With Ollama 0.35.1 + qwen3:4b, ``think: false`` does not stop the
    reasoning pass; it just stops the server splitting it out, so the whole
    chain of thought lands in ``response`` and gets spoken aloud. Leaving
    the flag off keeps reasoning in the separate ``thinking`` field, which
    must be discarded.
    """

    @staticmethod
    def _streaming_response(lines):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = lines
        return resp

    def test_no_think_flag_is_sent(self):
        from unittest.mock import patch
        resp = self._streaming_response(
            [b'{"response": "Hello", "done": false}',
             b'{"response": " there", "done": true}'])
        with patch("core.ollama.requests.post", return_value=resp) as post:
            text = "".join(ollama_streaming.StreamingOllama(
                model="qwen3:4b").generate_stream("hi"))
        self.assertEqual(text, "Hello there")
        body = post.call_args.kwargs["json"]
        self.assertNotIn("think", body)
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertTrue(body["stream"])

    def test_reasoning_tokens_are_not_yielded(self):
        """Chain-of-thought streams in `thinking` and must stay out of the
        reply, otherwise J.A.R.V.I.S. reads its inner monologue aloud."""
        from unittest.mock import patch
        resp = self._streaming_response([
            b'{"thinking": "Okay, the user asks who won. Let me recall...", "done": false}',
            b'{"thinking": " The 2022 final was Argentina vs France.", "done": false}',
            b'{"response": "Argentina won the 2022 World Cup.", "done": true}',
        ])
        with patch("core.ollama.requests.post", return_value=resp):
            text = "".join(ollama_streaming.StreamingOllama(
                model="qwen3:4b").generate_stream("hi"))
        self.assertEqual(text, "Argentina won the 2022 World Cup.")
        self.assertNotIn("Okay, the user", text)

    def test_http_error_raises_ollama_error(self):
        from unittest.mock import MagicMock, patch
        bad = MagicMock()
        bad.status_code = 400
        bad.text = '{"error": "model not found"}'
        bad.raise_for_status.side_effect = Exception("400 Bad Request")
        with patch("core.ollama.requests.post", return_value=bad) as post:
            gen = ollama_streaming.StreamingOllama(
                model="qwen3:4b").generate_stream("hi")
            with self.assertRaises(ollama_streaming.OllamaError):
                next(gen)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
