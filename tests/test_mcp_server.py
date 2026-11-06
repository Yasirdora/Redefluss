import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from german_tutor.engine import Engine, TutorError
from german_tutor.mcp_server import INSTRUCTIONS, TOOLS, rule_file, speak

SERVER = Path(__file__).resolve().parent.parent / "server.py"


class McpServerTest(unittest.TestCase):
    """Talks to the real server process over stdio, the way an AI app does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The Anki link stays off, so a test never adds cards to a real Anki on this computer.
        env = {**os.environ, "GERMAN_TUTOR_DB": str(Path(self.tmp.name) / "test.db"), "GERMAN_TUTOR_ANKI": "off"}
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=env)
        self.next_id = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.tmp.cleanup()

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        return None if notify else json.loads(self.proc.stdout.readline())

    def call(self, tool, /, **arguments):
        result = self.send("tools/call", {"name": tool, "arguments": arguments})["result"]
        text = result["content"][0]["text"]
        return result["isError"], (text if result["isError"] else json.loads(text))

    def initialize(self):
        reply = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "0"}})
        self.send("notifications/initialized", notify=True)
        return reply

    def test_handshake_and_tool_list(self):
        result = self.initialize()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("close_session", result["instructions"])
        names = {t["name"] for t in self.send("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"learner_summary", "start_session", "next_activity", "log_attempt",
                                 "close_session", "get_lesson", "log_measure", "log_sample", "set_unit_status",
                                 "word_check", "answer_word_check", "show_words", "log_word", "gender_drill",
                                 "answer_gender_drill", "export_anki", "sync_anki",
                                 "human_brief", "save_profile", "speak"})

    def test_unknown_protocol_version_gets_the_latest(self):
        result = self.send("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-11-25")

    def test_a_new_learner_is_interviewed_first(self):
        self.initialize()
        self.assertIn("onboarding", self.call("learner_summary")[1])
        is_error, message = self.call("start_session", minutes=60)
        self.assertTrue(is_error)
        self.assertIn("no learner yet", message)
        is_error, saved = self.call("save_profile", name="Sam", first_languages=["Turkish"], address="du")
        self.assertFalse(is_error, saved)
        self.assertNotIn("onboarding", self.call("learner_summary")[1])

    def test_a_session_round_trip(self):
        self.initialize()
        self.call("save_profile", name="Sam")
        is_error, started = self.call("start_session", minutes=60)
        self.assertFalse(is_error)
        sid = started["session_id"]
        self.assertEqual(self.call("next_activity", session_id=sid)[1]["type"], "review")
        is_error, logged = self.call("log_attempt", session_id=sid, construct="kein_negation",
                                     context="controlled", correct=True)
        self.assertFalse(is_error)
        self.assertEqual(logged["construct"], "kein_negation")
        is_error, logged = self.call("log_attempt", session_id=sid, construct="v2_word_order", context="free",
                                     correct=False, channel="spoken", repair="self", certain=True,
                                     trigger="Deswegen")
        self.assertFalse(is_error)
        is_error, message = self.call("close_session", session_id=sid, attempts_expected=3)
        self.assertTrue(is_error)
        self.assertIn("Only 2 of 3", message)
        closed = self.call("close_session", session_id=sid, attempts_expected=2, fatigue=3)[1]
        self.assertEqual((closed["closed"], closed["repairs"]["self"]), (True, 1))

    def test_a_word_check_round_trip(self):
        self.initialize()
        self.call("save_profile", name="Sam")
        sid = self.call("start_session", minutes=60)[1]["session_id"]
        words = [{"lemma": "Termin", "pos": "noun", "article": "der", "gloss": "appointment", "needed": True},
                 {"lemma": "verschieben", "pos": "verb", "gloss": "to postpone", "needed": True},
                 {"lemma": "Aussage", "pos": "noun", "article": "die", "gloss": "statement", "needed": True},
                 {"lemma": "Bewerbung", "pos": "noun", "article": "die", "gloss": "application", "needed": True}]
        is_error, check = self.call("word_check", session_id=sid, words=words, source="book:B1-U3")
        self.assertFalse(is_error, check)
        self.assertEqual(len(check["items"]), 7)  # 4 meanings, 3 genders
        is_error, result = self.call("answer_word_check", check_id=check["check_id"], answers=["E"] * 4 + ["D"] * 3)
        self.assertFalse(is_error, result)
        self.assertEqual(result["right"], "0 of 4")
        self.assertEqual(len(result["teach"]), 2)

    def test_tool_errors_come_back_as_results(self):
        self.initialize()
        is_error, message = self.call("log_attempt", session_id=99, construct="v2_word_order",
                                      context="free", correct=True)
        self.assertTrue(is_error)
        self.assertIn("No session 99", message)
        is_error, message = self.call("start_session", minutes=60, bogus=1)
        self.assertTrue(is_error)

    def test_protocol_errors(self):
        self.initialize()
        self.assertEqual(self.send("does/not/exist")["error"]["code"], -32601)
        self.assertEqual(self.send("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(self.send("ping")["result"], {})


class SchemaTest(unittest.TestCase):
    def test_tool_schemas_match_the_python_signatures(self):
        for tool in TOOLS:
            fn = speak if tool["name"] == "speak" else getattr(Engine, tool["name"])
            params = {n: p for n, p in inspect.signature(fn).parameters.items() if n != "self"}
            schema = tool["inputSchema"]
            self.assertEqual(set(schema["properties"]), set(params), tool["name"])
            self.assertEqual(set(schema["required"]),
                             {n for n, p in params.items() if p.default is inspect.Parameter.empty}, tool["name"])


class RuleFileTest(unittest.TestCase):
    def test_rule_file_is_an_always_on_rule_carrying_the_instructions(self):
        text = rule_file()
        self.assertTrue(text.startswith("---\ntrigger: always_on\n"))  # Antigravity drops rules without it
        self.assertIn(INSTRUCTIONS, text)


class SpeakValidationTest(unittest.TestCase):
    """Only the checks that run before any audio is played."""

    def test_rejects_empty_text_and_bad_rate(self):
        with self.assertRaises(TutorError):
            speak("   ")
        with self.assertRaises(TutorError):
            speak("Hallo", rate=500)


if __name__ == "__main__":
    unittest.main()
