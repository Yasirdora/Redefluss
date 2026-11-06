import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from german_tutor.anki import SENTENCE_MODEL, WORD_MODEL, AnkiConnect, AnkiError


class FakeAnkiConnect(BaseHTTPRequestHandler):
    """Answers the AnkiConnect actions the client uses, the way the add-on does (version 6)."""
    requests: list = []
    models = ["Basic"]
    fronts: set = set()
    cards = [{"cardId": 1, "note": 101, "interval": 25, "lapses": 0},
             {"cardId": 2, "note": 101, "interval": 3, "lapses": 1},
             {"cardId": 3, "note": 102, "interval": 1, "lapses": 4}]

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(request)
        action, params = request["action"], request["params"]
        result, error = None, None
        if request.get("version") != 6:
            error = "unsupported version"
        elif action == "modelNames":
            result = self.models
        elif action == "createModel":
            self.models.append(params["modelName"])
        elif action == "canAddNotes":
            result = [n["fields"]["Front"] not in self.fronts for n in params["notes"]]
        elif action == "addNotes":
            result = list(range(500, 500 + len(params["notes"])))
            self.fronts.update(n["fields"]["Front"] for n in params["notes"])
        elif action == "findCards":
            result = [1, 2, 3]
        elif action == "cardsInfo":
            result = self.cards
        elif action == "fail":
            error = "something went wrong"
        body = json.dumps({"result": result, "error": error}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AnkiConnectTest(unittest.TestCase):
    def setUp(self):
        FakeAnkiConnect.requests = []
        FakeAnkiConnect.models = ["Basic"]
        FakeAnkiConnect.fronts = {"das Haus"}
        self.server = HTTPServer(("127.0.0.1", 0), FakeAnkiConnect)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.anki = AnkiConnect(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def actions(self):
        return [r["action"] for r in FakeAnkiConnect.requests]

    def test_only_this_computer(self):
        with self.assertRaisesRegex(ValueError, "on this computer"):
            AnkiConnect("http://192.0.2.1:8765")

    def test_notes_are_added_once_with_the_deck_and_note_types_set_up(self):
        ids = self.anki.add_notes(WORD_MODEL, [("der Termin", "appointment", ["german-tutor"]),
                                               ("das Haus", "house", ["german-tutor"])])
        self.assertEqual(ids, [500, None])  # das Haus is already in the deck
        self.assertEqual(self.actions(), ["createDeck", "modelNames", "createModel", "createModel", "canAddNotes",
                                          "addNotes"])
        created = [r["params"] for r in FakeAnkiConnect.requests if r["action"] == "createModel"]
        self.assertEqual({m["modelName"]: len(m["cardTemplates"]) for m in created},
                         {WORD_MODEL: 2, SENTENCE_MODEL: 1})  # words both ways, sentences one way
        added = FakeAnkiConnect.requests[-1]["params"]["notes"]
        self.assertEqual(added[0]["deckName"], "Redefluss")
        self.assertFalse(added[0]["options"]["allowDuplicate"])
        self.anki.add_notes(SENTENCE_MODEL, [("Deswegen ___ nach Berlin.", "Deswegen fahre ich nach Berlin.", [])])
        self.assertEqual(self.actions().count("createModel"), 2)  # set up once

    def test_card_stats_take_the_longest_interval_and_add_up_lapses(self):
        stats = self.anki.card_stats([101, 102])
        self.assertEqual(stats, {101: {"interval": 25, "lapses": 1}, 102: {"interval": 1, "lapses": 4}})
        self.assertEqual(FakeAnkiConnect.requests[0]["params"]["query"], "nid:101,102")

    def test_errors_and_a_closed_anki_raise_anki_error(self):
        with self.assertRaisesRegex(AnkiError, "something went wrong"):
            self.anki.call("fail")
        with socket.socket() as free:  # a port nothing listens on, like Anki when it's closed
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        with self.assertRaisesRegex(AnkiError, "Is Anki open"):
            AnkiConnect(f"http://127.0.0.1:{port}", timeout=1).call("version")

if __name__ == "__main__":
    unittest.main()
