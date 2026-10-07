"""Tests for the pre-speech text cleanup (core/speech.py).

``strip_for_speech`` is the last thing between the model's answer and the
speech engine. SAPI5 reads symbols out loud as words, so markdown furniture
and LaTeX have to be turned into something a human would say — otherwise
"$17 \\times 23 = \\boxed{391}$" comes out as "dollar seventeen backslash
times twenty three equals backslash boxed braces...".
"""

import unittest

from core.speech import strip_for_speech

ROCKET = "\U0001F680"  # written as an escape so this file stays console-safe


class StripForSpeechTests(unittest.TestCase):
    def test_removes_markdown_emphasis(self):
        self.assertEqual(strip_for_speech("This is **bold** and _italic_"),
                         "This is bold and italic")

    def test_spoken_latex_becomes_words(self):
        spoken = strip_for_speech(r"$17 \times 23 = \boxed{391}$")
        self.assertIn("17 times 23", spoken)
        self.assertIn("391", spoken)
        for junk in ("$", "\\", "{", "}"):
            self.assertNotIn(junk, spoken)

    def test_fractions_and_roots(self):
        spoken = strip_for_speech(r"\frac{1}{2} of \sqrt{16}")
        self.assertIn("1 over 2", spoken)
        self.assertIn("square root of 16", spoken)

    def test_strips_headings_bullets_and_tables(self):
        spoken = strip_for_speech("### Points\n- first\n- second\n| a | b |")
        self.assertIn("Points", spoken)
        self.assertIn("first", spoken)
        for junk in ("#", "-", "|"):
            self.assertNotIn(junk, spoken)

    def test_strips_emoji_and_smilies(self):
        spoken = strip_for_speech(f"Nice work :) {ROCKET} done")
        self.assertIn("Nice work", spoken)
        self.assertIn("done", spoken)
        self.assertNotIn(ROCKET, spoken)
        self.assertNotIn(":)", spoken)

    def test_keeps_a_plain_sentence_intact(self):
        sentence = "The capital of Australia is Canberra, and it is inland."
        self.assertEqual(strip_for_speech(sentence), sentence)

    def test_empty_input_never_raises(self):
        self.assertEqual(strip_for_speech(""), "")
        self.assertEqual(strip_for_speech(None), "")


if __name__ == "__main__":
    unittest.main()
