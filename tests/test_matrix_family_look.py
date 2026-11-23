"""The A/V Matrix page's mode choice reads as it does in the family's other Matrix.

LumaSuite's Matrix shows three filled secondary buttons -- "AV (Audio + Video)", "Video", "Audio" --
with one sentence beside them saying what a click does now. OmniSuite's had a separate label and
transparent, muted buttons. These hold the converged look so it does not drift back.
"""
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def read(*p):
    return (UI.joinpath(*p)).read_text(encoding="utf-8")


class MatrixModeChoiceTests(unittest.TestCase):
    def test_the_buttons_carry_the_full_labels(self):
        html = read("matrix", "index.html")
        for label in (">AV (Audio + Video)<", ">Video<", ">Audio<"):
            self.assertIn(label, html)

    def test_one_sentence_says_what_a_click_does_and_follows_the_mode(self):
        html, js = read("matrix", "index.html"), read("matrix", "matrix.js")
        self.assertRegex(html, r'id="mode_label"[^>]*aria-live="polite"')
        for sentence in ("A click routes audio and video together (ring = video, dot = audio).",
                         "A click routes the video only; the audio stays as it is.",
                         "A click routes the audio only; the video stays as it is."):
            self.assertIn(sentence, js)

    def test_an_unselected_mode_button_is_a_filled_surface_with_normal_text(self):
        css = read("templates.css")
        block = css[css.index(".mode .modebtn {"):]
        block = block[:block.index("}")]
        self.assertIn("background-color: var(--surface-elevated)", block)
        self.assertIn("color: var(--text);", block)
        self.assertNotIn("transparent", block)


if __name__ == "__main__":
    unittest.main()
