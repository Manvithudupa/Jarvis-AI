import json

import requests
from decouple import config

# Everyday model + the bigger model used when a question is marked with the
# ULTRATHINK keyword (e.g. "ULTRATHINK: explain black holes").
#
# Both are configurable from your .env file (MODEL / BIG_MODEL) so you can
# switch models without editing code. Defaults to qwen3:4b — a 1.7B model is
# too small to answer general-knowledge questions without inventing facts.
# Want smarter ULTRATHINK answers? Pull a larger model and set BIG_MODEL:
#     ollama pull qwen3:8b
DEFAULT_MODEL = config("MODEL", default="qwen3:4b")
BIG_MODEL = config("BIG_MODEL", default="") or DEFAULT_MODEL
DEEP_MARKER = "ultrathink"


class OllamaError(Exception):
    """Raised when the local Ollama server can't answer (offline, model
    missing, network/request failure). Callers reply with a friendly
    message instead of leaking the raw error to the user."""


def check_model(model, base_url="http://localhost:11434"):
    """Diagnose why the local model can't answer — never raises.

    Returns (status, available_models):
        ("ok", names)            — server online and model is pulled;
        ("model-missing", names) — server online, model not downloaded;
        ("offline", None)        — server unreachable.
    """
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=2)
        if resp.status_code != 200:
            return "offline", None
        names = [t.get("name", "") for t in (resp.json().get("models") or [])]
        if any(n == model or n.startswith(model + ":") for n in names):
            return "ok", names
        return "model-missing", names
    except Exception:
        return "offline", None


def split_deep_marker(message):
    """Split off an ULTRATHINK marker from the start of a message.

    Returns (clean_message, is_deep). Questions marked like
    "ULTRATHINK: explain X" or "ULTRATHINK explain X" are routed to the
    bigger model. Matching is case-insensitive and only at the start, so
    ordinary questions that merely mention the word are unaffected.
    """
    m = (message or "").strip()
    low = m.lower()
    if low.startswith(DEEP_MARKER):
        rest = m[len(DEEP_MARKER):]
        if rest == "" or rest[0] in (":", " ", "\t"):
            return rest.lstrip(": \t").strip(), True
    return message, False


class StreamingOllama:
    def __init__(self, model=DEFAULT_MODEL, base_url="http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def _payload(self, prompt):
        """Request body for /api/generate.

        The ``think`` flag is deliberately NEVER sent. On Ollama 0.35.1 with
        qwen3:4b, ``think: false`` does not actually stop the reasoning pass —
        it only stops the server from splitting it out, so the whole chain of
        thought arrives inside ``response`` and J.A.R.V.I.S. reads its own
        inner monologue aloud ("Okay, the user is asking who won...").
        Leaving the flag off keeps reasoning in the separate ``thinking``
        field, which :meth:`generate_stream` discards.
        """
        return {"model": self.model, "prompt": prompt, "stream": True}

    def generate_stream(self, prompt):
        """Stream the model's reply tokens, raising :class:`OllamaError` on
        any failure (server offline, model missing, HTTP error, or an error
        field mid-stream). Callers turn that into a friendly reply.

        Thinking models (Qwen3, DeepSeek-R1...) interleave reasoning tokens
        in a separate ``thinking`` field. Those are never yielded — only the
        final answer reaches the screen or the speakers. Because that pass
        can run for tens of seconds on a CPU, the first reasoning token logs
        a one-off console note so the silence is explained rather than
        mistaken for a hang.
        """
        url = f"{self.base_url}/api/generate"
        announced_thinking = False
        try:
            response = requests.post(url, json=self._payload(prompt),
                                     stream=True, timeout=120)
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    part = json.loads(line.decode("utf-8"))
                    if part.get("error"):
                        raise OllamaError(f"Ollama: {part['error']}")
                    if part.get("thinking") and not announced_thinking:
                        announced_thinking = True
                        print("[assistant] thinking...", flush=True)
                    token = part.get("response", "")
                    if token:
                        yield token
                except OllamaError:
                    raise
                except Exception:
                    continue
        except requests.exceptions.HTTPError as e:
            detail = ""
            try:
                detail = (e.response.json() or {}).get("error", "")
            except Exception:
                pass
            msg = (f"model {self.model!r} not found" if not detail
                   else str(detail))
            raise OllamaError(f"Ollama: {msg}") from e
        except requests.exceptions.RequestException as e:
            raise OllamaError(
                "the local AI engine (Ollama) isn't responding") from e
        except Exception as e:
            raise OllamaError(str(e)) from e