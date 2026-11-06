import json
import socket
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from german_tutor.checker import CheckerError, LanguageTool, verb_position
from german_tutor.engine import Engine, TutorError


class VerbPositionTest(unittest.TestCase):
    def found(self, text):
        return [(f["construct"], f["trigger"]) for f in verb_position(text)]

    def test_learner_errors_are_found_with_construct_and_trigger(self):
        for text, finding in (
                ("Deswegen ich fahre nach Berlin.", ("v2_word_order", "deswegen")),
                ("Weil ich krank bin, ich bleibe zu Hause.", ("v2_word_order", "weil")),
                ("Ich bleibe hier, weil ich habe keine Zeit.", ("verb_final_subclause", "weil")),
                ("Ich frage, ob wir haben morgen frei.", ("verb_final_subclause", "ob")),
                ("Ich lerne, damit ich kann die Prüfung bestehen.", ("verb_final_subclause", "damit"))):
            self.assertEqual(self.found(text), [finding], text)

    def test_correct_german_raises_no_alarm(self):
        for text in ("Deswegen fahre ich nach Berlin.", "Weil ich krank bin, bleibe ich zu Hause.",
                     "Ich bleibe hier, weil ich keine Zeit habe.", "Ich weiß, dass ich das haben will.",
                     "Ich bin müde, weil ich viel gearbeitet habe und nicht schlafen konnte.",
                     "Damit kann ich gut arbeiten.", "Als Kind habe ich viel gelesen.",
                     "Seitdem bin ich hier.", "Also ich finde das gut.", "Wenn ich Zeit habe, komme ich.",
                     "Er sagt, dass er habe arbeiten müssen.", "Während der Woche ist es ruhig."):
            self.assertEqual(self.found(text), [], text)


class FakeLanguageTool(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.seen.append((self.path, form))
        text = form["text"][0]
        at = text.find("Firmas")
        matches = [] if at < 0 else [{"message": "Possible spelling mistake.", "offset": at, "length": 6,
                                      "replacements": [{"value": "Firmen"}, {"value": "Firma"}],
                                      "rule": {"id": "GERMAN_SPELLER_RULE", "category": {"id": "TYPOS"}}}]
        body = json.dumps({"matches": matches}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LanguageToolTest(unittest.TestCase):
    def setUp(self):
        FakeLanguageTool.seen = []
        self.server = HTTPServer(("127.0.0.1", 0), FakeLanguageTool)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tool = LanguageTool(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_matches_come_back_with_the_words_and_suggestions(self):
        [match] = self.tool.check("Zwei Firmas suchen Leute.")
        self.assertEqual((match["text"], match["suggestions"], match["category"]), ("Firmas", ["Firmen", "Firma"], "TYPOS"))
        path, form = FakeLanguageTool.seen[0]
        self.assertEqual((path, form["language"]), ("/v2/check", ["de-DE"]))

    def test_never_an_outside_server_and_a_missing_one_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "never goes to an outside service"):
            LanguageTool("https://api.languagetool.org")
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        with self.assertRaisesRegex(CheckerError, "No LanguageTool server"):
            LanguageTool(f"http://127.0.0.1:{port}", timeout=1).check("Hallo.")

    def test_check_text_combines_both_and_works_without_languagetool(self):
        engine = Engine(":memory:", languagetool=self.tool)
        result = engine.check_text("Deswegen ich arbeite bei zwei Firmas.")
        self.assertEqual(result["verb_position"][0]["construct"], "v2_word_order")
        self.assertEqual(result["languagetool"][0]["text"], "Firmas")
        self.assertIn("never log an answer as right", result["instructions"])
        engine.languagetool = LanguageTool("http://127.0.0.1:9", timeout=1)
        self.assertIn("not running", engine.check_text("Deswegen ich arbeite.")["languagetool"])
        engine.languagetool = None
        self.assertEqual(len(engine.check_text("Deswegen ich arbeite.")["verb_position"]), 1)
        with self.assertRaisesRegex(TutorError, "exactly as they typed"):
            engine.check_text("  ")


if __name__ == "__main__":
    unittest.main()
