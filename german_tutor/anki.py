"""An optional link to Anki through the AnkiConnect add-on, on this computer only.

AnkiConnect is an Anki add-on that answers JSON requests on 127.0.0.1:8765 while Anki is open. Redefluss uses
it to add the day's new cards straight to a deck, and to read back how those cards are going (interval and
lapses), so a word Anki has settled isn't checked again and a word that keeps failing gets taught another
way. Only local addresses are accepted: nothing goes to another computer.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

DEFAULT_URL = "http://127.0.0.1:8765"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
API_VERSION = 6
DECK = "Redefluss"
WORD_MODEL = "Redefluss word"
SENTENCE_MODEL = "Redefluss sentence"
CSS = ".card { font-family: -apple-system, sans-serif; font-size: 24px; text-align: center; }"
FRONT, BACK = "{{Front}}", "{{FrontSide}}<hr id=answer>{{Back}}"
MODELS = {
    # A word is practised both ways, German to English and back, article included.
    WORD_MODEL: [{"Name": "German to English", "Front": FRONT, "Back": BACK},
                 {"Name": "English to German", "Front": "{{Back}}", "Back": "{{FrontSide}}<hr id=answer>{{Front}}"}],
    # A sentence card is one way only: the gapped sentence, then the corrected one.
    SENTENCE_MODEL: [{"Name": "Sentence", "Front": FRONT, "Back": BACK}],
}


class AnkiError(Exception):
    """Anki can't be reached, or AnkiConnect refused a request."""


class AnkiConnect:
    def __init__(self, url: str | None = None, deck: str = DECK, timeout: float = 3.0):
        url = url or os.environ.get("GERMAN_TUTOR_ANKI_URL") or DEFAULT_URL
        if urlparse(url).hostname not in LOCAL_HOSTS:
            raise ValueError(f"AnkiConnect must run on this computer ({', '.join(LOCAL_HOSTS)}), not {url}.")
        self.url, self.deck, self.timeout = url, deck, timeout

    def call(self, action: str, **params):
        body = json.dumps({"action": action, "version": API_VERSION, "params": params}).encode("utf-8")
        request = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AnkiError(f"Anki isn't reachable at {self.url}. Is Anki open, with the AnkiConnect add-on "
                            f"installed? ({e})") from e
        if not isinstance(reply, dict) or reply.get("error"):
            raise AnkiError(f"AnkiConnect refused {action}: {reply.get('error') if isinstance(reply, dict) else reply}")
        return reply.get("result")

    def setup(self) -> None:
        """Create the deck and the two note types if they aren't there yet."""
        self.call("createDeck", deck=self.deck)
        existing = set(self.call("modelNames") or [])
        for name, templates in MODELS.items():
            if name not in existing:
                self.call("createModel", modelName=name, inOrderFields=["Front", "Back"], css=CSS,
                          isCloze=False, cardTemplates=templates)

    def add_notes(self, model: str, notes: list[tuple[str, str, list[str]]]) -> list[int | None]:
        """Add (front, back, tags) notes; a note already in the deck gets None instead of a note id."""
        if not notes:
            return []
        self.setup()
        payload = [{"deckName": self.deck, "modelName": model, "fields": {"Front": front, "Back": back},
                    "tags": tags, "options": {"allowDuplicate": False, "duplicateScope": "deck"}}
                   for front, back, tags in notes]
        addable = self.call("canAddNotes", notes=payload)
        new = [note for note, ok in zip(payload, addable) if ok]
        ids = iter(self.call("addNotes", notes=new) if new else [])
        return [next(ids) if ok else None for ok in addable]

    def card_stats(self, note_ids: list[int]) -> dict[int, dict]:
        """For each note: the longest interval of its cards in days, and their lapses added up."""
        if not note_ids:
            return {}
        cards = self.call("findCards", query="nid:" + ",".join(str(n) for n in note_ids)) or []
        stats: dict[int, dict] = {}
        for card in self.call("cardsInfo", cards=cards) or []:
            entry = stats.setdefault(card["note"], {"interval": 0, "lapses": 0})
            entry["interval"] = max(entry["interval"], card.get("interval") or 0)
            entry["lapses"] += card.get("lapses") or 0
        return stats


def from_environment() -> AnkiConnect | None:
    """The link the server uses: on by default, off with GERMAN_TUTOR_ANKI=off."""
    if os.environ.get("GERMAN_TUTOR_ANKI", "").lower() in ("off", "0", "no"):
        return None
    return AnkiConnect()
