"""A second judge for typed German, used as a veto only.

Two checks look at a text the learner typed:
- LanguageTool, when a LanguageTool server runs on this computer. The engine never calls the public
  LanguageTool service, because the learner's text would then go to a third party.
- A verb-position check for the word-order errors B1 learners make most and spell-checkers often miss:
  the subject before the verb after a fronted word (Deswegen ich fahre) or a fronted clause (Weil ich krank
  bin, ich bleibe zu Hause), and a finite auxiliary or modal before the end of a weil or dass clause
  (weil ich habe keine Zeit). It looks only for these patterns, so it misses many errors; it is written to
  avoid false alarms.

A finding is a lead for the tutor to check. A clean result proves nothing, so it never confirms free use.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

DEFAULT_URL = "http://localhost:8081"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

PRONOUNS = {"ich", "du", "er", "sie", "es", "wir", "ihr", "man"}
# Words that, first in a main clause, push the verb into second place. 'also' is left out: in speech it is
# often a filler before a normal sentence.
FRONTED = {"deshalb", "deswegen", "darum", "daher", "dann", "danach", "heute", "morgen", "gestern", "jetzt",
           "leider", "trotzdem", "außerdem", "zuerst", "später", "oft", "manchmal", "natürlich", "vielleicht",
           "normalerweise", "sonst", "dennoch", "hier", "dort", "meistens", "bald", "endlich", "plötzlich",
           "eigentlich", "schließlich", "inzwischen", "früher", "damals", "zusätzlich"}
# Subordinating conjunctions. The second set doubles as adverbs or prepositions (Damit kann ich arbeiten,
# als Kind), so those count only right after a comma.
SUBORDINATORS = {"weil", "dass", "obwohl", "ob", "wenn", "falls", "nachdem", "bevor", "sodass", "sobald",
                 "solange"}
AFTER_COMMA = {"damit", "als", "da", "bis", "während", "seitdem", "seit"}
FINITE = set("""
    habe hast hat haben habt hatte hattest hatten hattet hätte hättest hätten hättet
    bin bist ist sind seid war warst waren wart wäre wärst wären wärt
    werde wirst wird werden werdet wurde wurdest wurden würde würdest würden würdet
    kann kannst können könnt konnte konntest konnten konntet könnte könntest könnten
    muss musst müssen müsst musste musstest mussten müsste müssten
    will willst wollen wollt wollte wolltest wollten
    soll sollst sollen sollt sollte solltest sollten
    darf darfst dürfen dürft durfte durften dürfte dürften
    möchte möchtest möchten möchtet mag magst mögen
""".split())
CLAUSE_BREAKS = {"und", "oder", "aber", "sondern", "denn"}
TOKEN = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*|[,;:.!?]")


class CheckerError(Exception):
    """The LanguageTool server can't be reached, or answered with something unexpected."""


def verb_position(text: str) -> list[dict]:
    """Likely verb-position errors in a typed text, each with the construct and trigger to log it under."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        tokens = TOKEN.findall(sentence)
        words = [t.lower() for t in tokens]
        if len(words) >= 3 and words[0] in FRONTED and words[1] in PRONOUNS:
            found.append(_finding("v2_word_order", tokens[0], sentence,
                                  f"After '{tokens[0]}' at the start, the verb comes second, before the subject: "
                                  f"{tokens[0]} [verb] {tokens[1]} …"))
        if words and words[0] in SUBORDINATORS and "," in words:
            after = words.index(",") + 1
            if after < len(words) and words[after] in PRONOUNS:
                found.append(_finding("v2_word_order", tokens[0], sentence,
                                      f"After the fronted '{tokens[0]}' clause, the main clause starts with its "
                                      f"verb: …, [verb] {tokens[after]} …"))
        for start, word in enumerate(words):
            if not (word in SUBORDINATORS or (word in AFTER_COMMA and start > 0 and words[start - 1] == ",")):
                continue
            clause = []
            for w in words[start + 1:]:
                if w in {",", ";", ":", ".", "!", "?"} or w in CLAUSE_BREAKS:
                    break
                clause.append(w)
            finite = [w for w in clause if w in FINITE]
            if len(clause) >= 2 and finite and clause[-1] not in FINITE:
                found.append(_finding("verb_final_subclause", tokens[start], sentence,
                                      f"In a '{tokens[start]}' clause the conjugated verb goes to the end: "
                                      f"'{finite[0]}' should be the clause's last word."))
    return found


def _finding(construct: str, trigger: str, sentence: str, message: str) -> dict:
    return {"construct": construct, "trigger": trigger.lower(), "sentence": sentence, "message": message}


class LanguageTool:
    """A LanguageTool server on this computer (http://localhost:8081 by default)."""

    def __init__(self, url: str | None = None, timeout: float = 10.0):
        url = (url or os.environ.get("GERMAN_TUTOR_LANGUAGETOOL_URL") or DEFAULT_URL).rstrip("/")
        if urlparse(url).hostname not in LOCAL_HOSTS:
            raise ValueError(f"LanguageTool must run on this computer ({', '.join(LOCAL_HOSTS)}), not {url}: the "
                             "learner's text never goes to an outside service.")
        self.url, self.timeout = url, timeout

    def check(self, text: str) -> list[dict]:
        body = urllib.parse.urlencode({"language": "de-DE", "text": text}).encode("utf-8")
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + "/v2/check", body),
                                        timeout=self.timeout) as response:
                matches = json.loads(response.read().decode("utf-8"))["matches"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise CheckerError(f"No LanguageTool server answered at {self.url} ({e}).") from e
        return [{"text": text[m["offset"]:m["offset"] + m["length"]], "message": m["message"],
                 "suggestions": [r["value"] for r in m.get("replacements", [])[:3]],
                 "rule": m.get("rule", {}).get("id"),
                 "category": m.get("rule", {}).get("category", {}).get("id")} for m in matches]


def from_environment() -> LanguageTool | None:
    """The server's LanguageTool link: on by default, off with GERMAN_TUTOR_LANGUAGETOOL=off."""
    if os.environ.get("GERMAN_TUTOR_LANGUAGETOOL", "").lower() in ("off", "0", "no"):
        return None
    return LanguageTool()
