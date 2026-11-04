"""The tutor engine: the model teaches, this code keeps score.

Revised after external reviews by other models:
- Mastery needs first-pass evidence from at least two sessions.
- Free use needs a second judge besides the tutor model, and only counts when it is unprimed.
- Focus follows the order in which German word order is acquired.
- Fatigue is handled with breaks, not an accuracy detector.

Revised again (0.3.0) after a review of the vocabulary and error model:
- A word model with four facets (meaning, hearing, gender, use). The engine builds and scores the word
  check itself, and a word shown in a session proves nothing that day.
- Errors carry the word that triggered them; a construct plus a trigger is a pattern, and patterns come
  back as spaced retests. Only a retest passed without help moves a pattern towards resolved.
- Failure rates are also reported per channel and per trigger, and speaking pulls the worst spoken target.

And (0.4.0) after two research reviews:
- Word-order stages are a diagnostic, not a gate: the next stage is paired as soon as it fails too.
- Speaking repeats one task with a revision step between rounds; corrections are prompts, not recasts.
- More speaking and less book time; one reading day a week, two if reading is the weakest mock module.
- A short day (under 45 minutes) is a minimum day: reviews, retests and one speaking task.
- The weekly human lesson is the transfer test: human_brief gives blind samples to rate, the tutor's
  recent corrections to audit, and a sentence-repetition test every two weeks.

And (0.5.0), to work for any learner: a first-run interview (save_profile) comes before the first session.

Revised (0.6.0) after reviews of teacher-style questioning: the tutor asks for reasons on a sample, not
after every answer, and logs what it hears as reason. A right form with no reason still counts, since
explaining a rule and using it are different skills; a reason that contradicts the form makes it a
guess. On reading and listening items the quoted words are the skill, and a right choice without them
is a guess. The quote rule fades to exam format in the last three weeks. The interview asks seven
questions first and the rest over the first sessions.

Every public method returns plain dicts. Refusals raise TutorError with how to fix them.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import __version__, catalog, exams
from .db import connect, db_path

BOOK_DIR = Path(__file__).resolve().parent.parent / "data" / "book"
MEASURES_DIR = Path(__file__).resolve().parent.parent / "data" / "measures"

CONTEXTS = {"recognise": 1, "recall": 2, "controlled": 3, "free": 4}
CHANNELS = ("typed", "spoken", "listening")
SAMPLE_KINDS = ("speech", "writing")
REPAIRS = ("none", "self", "prompted")
# The learner's reason for an answer, when the tutor asked for one [J]. A right form with no reason still
# counts: explaining a rule and using it are different skills (Wylie et al. 2010-2011). A reason that
# contradicts the form makes it a guess. On comprehension items the quoted words are the skill, so a right
# choice counts only with words that decide it.
REASONS = ("supports", "contradicts", "absent", "unclear")
EVIDENCE_CONSTRUCTS = ("reading_detail", "listening_detail")
LEVEL_NAMES = {0: "no evidence yet", 1: "recognise", 2: "recall", 3: "controlled use",
               4: "free use", 5: "retained and fluent"}

# Mastery ladder [J]. A rung counts when the last WINDOW scored answers in that context hold
# at least MIN_EVIDENCE answers from at least MIN_SESSIONS sessions, ACHIEVED_PCT of them right.
WINDOW = 8
MIN_EVIDENCE = 5
MIN_SESSIONS = 2
ACHIEVED_PCT = 80
RETENTION_DAYS = 7
REVIEW_AFTER_DAYS = {0: 1, 1: 1, 2: 2, 3: 4, 4: 7, 5: 21}
# Focus [J]: failure rate over the last 14 days. With fewer than MIN_OPPORTUNITIES answers a
# construct is probed, not drilled.
ERROR_WINDOW_DAYS = 14
MIN_OPPORTUNITIES = 3
MAX_PROBES = 5
# Breaks [J]: 5 minutes after every 50 minutes of work, a stop when the planned minutes and their
# breaks are used up, and an effort check when hint use rises within a block.
BREAK_AFTER_MIN = 50
BREAK_MIN = 5
HINT_WINDOW = 8
HINT_RISE_PCT = 25
# Timed writing in the learner's exam format twice a week, by day number mod 7 [J]. The writing block
# takes the focus block's minutes, then minutes from speaking, input and the book lesson, each keeping at
# least half; a task that doesn't fit gives way to a shorter one, and on a minimum day there is none.
WRITING_DAYS = (4, 0)
WRITING_REVIEW_MIN = 10
WRITING_DONORS = ("focus", "speaking", "input", "lesson")
# One part of the exam's speaking test replaces the everyday speaking task once a week, and exams with
# language elements (Sprachbausteine) get one part of them in the focus block once a week.
EXAM_SPEAKING_DAY = 3
LANGUAGE_ELEMENTS_DAY = 5
# The input block's weekly rotation, by day number mod 7 [J]; other days are narrow listening.
# EXTRA_READING_DAY becomes a reading day too while reading (lesen) is the weakest mock module.
INPUT_ROTATION = {2: "reading", 6: "exam_listening"}
EXTRA_READING_DAY = 5
# Narrow listening keeps the same speakers for weeks; once a week, voices the learner hasn't heard, since
# the exam brings unknown speakers and accents. Every listening ends with one listen without the transcript,
# so the last pass is by ear and not by reading.
UNFAMILIAR_SPEAKERS_DAY = 3
LAST_LISTEN = (" Last, play the recording once more without the transcript, so the ear does the work alone, and ask "
               "what the learner catches now that they missed at first.")
GATE_RATERS = ("answer_key", "human")
# A session shorter than this is a minimum day [J]: reviews, retests and one speaking task, so a bad day
# still counts. Below MIN_SESSION_MIN there is no useful plan.
SHORT_DAY_MIN = 45
MIN_SESSION_MIN = 15
# How often the progress checks between mocks are due [J]: a C-test or onSET monthly, sentence
# repetition (elicited imitation) every two weeks in the human lesson.
CHECKS_EVERY_DAYS = {"c_test": 30, "elicited_imitation": 14}
# First run: what the tutor asks a new learner. A short interview first, and the rest over the first
# sessions when it fits, so teaching starts soon [J]. The code uses only start_date and exam_day (from
# exam_date); the rest tells the tutor model whom it teaches (first languages predict typical errors).
FIRST_QUESTIONS = {
    "name": "What should I call you?",
    "first_languages": "Which language or languages did you grow up with?",
    "level": "How good is your German now (A2, B1, B2…), how did you learn it, and do you have a certificate?",
    "goal": "What do you need German for (work, study, daily life), and is there an exam (Goethe, telc, DTB…)?",
    "exam_date": "When is the exam, or by when do you want to be ready? (a date; about 150 days from now if "
                 "there is none yet)",
    "minutes_per_day": "How many minutes a day can you really study on most days?",
    "address": "Shall I say du or Sie to you?",
}
LATER_QUESTIONS = {
    "explain_language": "In which language should I explain grammar: German, English or another?",
    "worries": "Which part worries you most: speaking, listening, writing or reading?",
    "schedule": "When in the week can you study (days and times)?",
    "other_languages": "Which other languages do you speak, and how well?",
    "book": "Do you work through a textbook? Which one, and where are you in it?",
    "human_tutor": "Do you have a human teacher or speaking partner, and how often?",
}
# In the last weeks before the exam, comprehension practice keeps to exam format: the learner quotes the
# deciding words only for wrong items [J]. The exam never asks for a quote, and quoting doubles the time.
EVIDENCE_FADE_DAYS = 21
ADDRESS_FORMS = ("du", "Sie")
EXAM_QUESTION = ("Which exam exactly: Goethe-Zertifikat B2, telc Deutsch B2, the DTB B2 at the end of a BAMF job "
                 "course, or none yet?")

# The weekly human lesson [J]: blind ratings of stored speech, and an audit of the tutor model's corrections.
SAMPLES_TO_RATE = 3
AUDIT_SIZE = 10
# Error patterns [J]: a construct plus the word that set it off. An error schedules a retest
# RETEST_DAYS[0] days later; each retest passed without help schedules the next one, RETEST_DAYS[stage]
# days on, and passing the last resolves the pattern. Another error resets it. Gaps that grow to more than
# a week test what lasts, not what is still fresh from the last session.
PATTERN_TRACKS = ("word_order", "form")
RETEST_DAYS = (1, 4, 10)
RETESTS_PER_SESSION = 6
# A listening or reading miss is classified, so it can be counted.
LISTENING_CAUSES = ("speed", "decoding", "knowledge", "retention", "unsure")
READING_CAUSES = ("word", "sentence", "task", "unsure")
# Words [J]. Each facet is known after correct unprimed evidence on KNOWN_DAYS different days since
# its last miss. The tutor model may record misses and uses, but never that a meaning is known.
WORD_FACETS = ("meaning", "hearing", "gender", "use")
WORD_POS = ("noun", "verb", "adjective", "other", "chunk")
ARTICLES = ("der", "die", "das")
WORD_FIELDS = ("lemma", "pos", "gloss", "article", "example", "needed")
KNOWN_DAYS = 2
CANDIDATES_MAX = 15
CHECK_SIZE = 6
COMMON_RANK = 10000  # in a word-form frequency list, which ranks lemmas lower than a lemma list would
TEACH_MAX = 2
ANKI_PER_DAY = 5
LETTERS = "ABCDE"
DONT_KNOW = "weiß nicht"


def _tip(exam: dict, part: str) -> str:
    """An exam tip for the tutor to pass on, or nothing when the format has none for this part."""
    tip = exam.get("tips", {}).get(part)
    return f" Exam tip to pass on: {tip}" if tip else ""


class TutorError(ValueError):
    """A request the engine refuses; the message says how to fix it."""


class Engine:
    def __init__(self, db: str | Path | None = None, clock: Callable[[], datetime] = datetime.now,
                 export_dir: str | Path | None = None):
        path = db_path(db)
        self.conn = connect(path)
        self.clock = clock
        # Anki files go next to the database unless told otherwise; an in-memory database has no folder.
        self.export_dir = Path(export_dir) if export_dir else (
            None if path == ":memory:" else Path(path).parent / "anki")

    def now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def today(self) -> date:
        return self.clock().date()

    # Setup -----------------------------------------------------------------

    def bootstrap(self, book_dir: str | Path = BOOK_DIR) -> None:
        """Load the construct catalog and every book unit; safe to run on each start."""
        with self.conn:
            for c in catalog.CONSTRUCTS:
                self.conn.execute(
                    """INSERT INTO constructs (id, track, stage, kind, name, rule, example, impact, relevance,
                                               treatable)
                       VALUES (:id, :track, :stage, :kind, :name, :rule, :example, :impact, :relevance,
                               :treatable)
                       ON CONFLICT (id) DO UPDATE SET track = excluded.track, stage = excluded.stage,
                           kind = excluded.kind, name = excluded.name, rule = excluded.rule,
                           example = excluded.example, impact = excluded.impact,
                           relevance = excluded.relevance, treatable = excluded.treatable""", c)
        for path in sorted(Path(book_dir).glob("*.json")):
            self.add_unit(json.loads(path.read_text(encoding="utf-8")))

    def set_profile(self, values: dict) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)",
                                  [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])

    def save_profile(self, name: str | None = None, first_languages: list | None = None,
                     other_languages: str | None = None, level: str | None = None, goal: str | None = None,
                     exam: str | None = None, exam_format: str | None = None, exam_date: str | None = None,
                     exam_day: int | None = None,
                     minutes_per_day: int | None = None,
                     schedule: str | None = None, worries: str | None = None,
                     explain_language: str | None = None, book: str | None = None,
                     human_tutor: str | None = None, address: str | None = None,
                     start_date: str | None = None, notes: str | None = None) -> dict:
        """Store what the first-run interview found out (or a later change). Fields left out keep their value;
        day 1 is today unless start_date says otherwise. An exam_date sets exam_day."""
        given = {k: v for k, v in locals().items() if k != "self" and v is not None}
        if isinstance(given.get("first_languages"), str):
            given["first_languages"] = [given["first_languages"]]
        if "exam_day" in given and "exam_date" not in given and not 1 <= given["exam_day"] <= 1000:
            raise TutorError("exam_day is the number of days until the learner wants to be ready, 1 to 1000.")
        if "minutes_per_day" in given and given["minutes_per_day"] < MIN_SESSION_MIN:
            raise TutorError(f"minutes_per_day must be at least {MIN_SESSION_MIN}.")
        if "exam_format" in given and given["exam_format"] not in exams.CHOICES:
            raise TutorError(f"exam_format must be one of: {', '.join(exams.CHOICES)}.")
        if "address" in given and given["address"] not in ADDRESS_FORMS:
            raise TutorError("address must be du or Sie.")
        if "start_date" in given:
            try:
                date.fromisoformat(given["start_date"])
            except ValueError:
                raise TutorError("start_date must be a date like 2026-01-31.") from None
        elif "start_date" not in self.profile():
            given["start_date"] = self.today().isoformat()
        if "exam_date" in given:
            if "exam_day" in given:
                raise TutorError("Send exam_date or exam_day, not both.")
            start = given.get("start_date") or self.profile()["start_date"]
            try:
                given["exam_day"] = (date.fromisoformat(given["exam_date"]) - date.fromisoformat(start)).days + 1
            except ValueError:
                raise TutorError("exam_date must be a date like 2026-01-31.") from None
            if not 1 <= given["exam_day"] <= 1000:
                raise TutorError("exam_date must be after day 1 and within 1000 days of it.")
        given.setdefault("exam_day", self.profile().get("exam_day", 150))
        self.set_profile(given)
        return {"profile": self.profile(), "exam": self._exam_choice(),
                "next": "Start with start_session(minutes). In this first session, also take a one-minute "
                        "speaking sample on a familiar topic (store it with log_sample('speech')) and do one "
                        "exam-style reading item, to show the rule of quoting the words that decide it. If the "
                        "learner is near the exam's level and has a date, do one full mock of the exam (its "
                        "official model test) by the end of the first week, over two sittings if needed: it is a "
                        "baseline, logged per module with log_measure, and never changes the mastery levels. Say "
                        "that your marks for writing and speaking are only an estimate. The first sessions probe "
                        "each construct before any drill. Ask the questions under still_to_ask in "
                        "learner_summary over the first sessions, one when it fits."}

    def profile(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.conn.execute("SELECT key, value FROM profile")}

    def exam(self) -> dict:
        """The format of the learner's exam (data/exams), or general B2 practice when there is none or it's unclear."""
        chosen = self._exam_id()
        return exams.get(None if chosen == exams.NO_EXAM else chosen)

    def _exam_id(self) -> str | None:
        """exam_format if the learner confirmed one ('none' included), else the exam their answers name."""
        profile = self.profile()
        return profile.get("exam_format") or exams.detect(profile.get("exam"), profile.get("goal"))

    def _exam_choice(self) -> dict:
        chosen, exam = self._exam_id(), self.exam()
        result = {"id": exam["id"], "name": exam["name"]}
        if chosen is None:
            result["check"] = (f"It isn't clear which exam this is. Ask: {EXAM_QUESTION} Save the answer as "
                               "exam_format; until then, practice is general B2.")
        elif "exam_format" not in self.profile():
            result["check"] = (f"From the learner's answers this is {exam['name']}. Confirm it with them, and save "
                               "exam_format if it's another.")
        return result

    def add_unit(self, unit: dict) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO units (id, title, position, data) VALUES (?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET title = excluded.title,
                       position = excluded.position, data = excluded.data""",
                (unit["unit"], unit["title"], unit.get("position", 0), json.dumps(unit, ensure_ascii=False)))

    def set_frequency(self, words: list[str]) -> int:
        """Replace the frequency list used to rank word-check candidates; most frequent first."""
        ranks: dict[str, int] = {}
        for word in words:
            word = word.strip().lower()
            if word and word not in ranks:
                ranks[word] = len(ranks) + 1
        with self.conn:
            self.conn.execute("DELETE FROM frequency")
            self.conn.executemany("INSERT INTO frequency (word, rank) VALUES (?, ?)", ranks.items())
        return len(ranks)

    # Reading ---------------------------------------------------------------

    def learner_summary(self) -> dict:
        """Everything the model needs at the start of a session."""
        evidence: dict[str, dict] = {}
        for r in self.conn.execute(
                """SELECT construct_id, context, COUNT(*) AS n, SUM(correct) AS ok FROM attempts
                   WHERE hint_level = 0 AND certain = 1 GROUP BY construct_id, context"""):
            evidence.setdefault(r["construct_id"], {})[r["context"]] = f"{r['ok']}/{r['n']}"
        constructs = []
        for r in self.conn.execute("SELECT * FROM constructs ORDER BY track, stage, id"):
            entry = {"id": r["id"], "name": r["name"], "track": r["track"], "level": r["level"],
                     "level_name": LEVEL_NAMES[r["level"]], "first_pass_right": evidence.get(r["id"], {}),
                     "next_review": r["next_review"]}
            if r["level_note"]:
                entry["note"] = r["level_note"]
            constructs.append(entry)
        last = self.conn.execute(
            "SELECT id, summary FROM sessions WHERE closed_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        profile = self.profile()
        if "start_date" not in profile:
            onboarding = {"onboarding": {
                "ask": FIRST_QUESTIONS,
                "how": "There is no learner yet. Before any lesson, have a short conversation: ask these one or "
                       "two at a time, in German or in a language the learner is comfortable with, then call "
                       "save_profile with the answers. Say that their record stays in a file on this computer, while what they say in lessons goes "
                       "to this AI app's provider. The "
                       "questions under later can wait for the first sessions.",
                "later": LATER_QUESTIONS}}
        else:
            unasked = {k: q for k, q in {**FIRST_QUESTIONS, **LATER_QUESTIONS}.items()
                       if k not in profile and not (k == "exam_date" and "exam_day" in profile)}
            if self._exam_id() is None:
                unasked["exam_format"] = EXAM_QUESTION
            onboarding = {"still_to_ask": unasked} if unasked else {}
        return {
            **onboarding,
            "profile": profile,
            "progress": self.progress(),
            "focus": self.focus(),
            "patterns": self._patterns(),
            "words": self._word_overview(),
            "constructs": constructs,
            "last_session": {"id": last["id"], **json.loads(last["summary"])} if last else None,
            "open_sessions": [r["id"] for r in self.conn.execute("SELECT id FROM sessions WHERE closed_at IS NULL")],
        }

    def progress(self) -> dict | None:
        """Day count, the next exam gate and the latest mock score per module."""
        profile = self.profile()
        if "start_date" not in profile:
            return None
        day = (self.today() - date.fromisoformat(profile["start_date"])).days + 1
        exam = self.exam()
        gate = next((g for g in exam["gates"] if g["day"] >= day), None)
        latest_mock = {r["part"]: {"score": f"{r['score']:g}/{r['max_score']:g}", "rater": r["rater"],
                                   "counts_for_gates": r["rater"] in GATE_RATERS}
                       for r in self.conn.execute(
                           "SELECT part, rater, score, max_score FROM measures WHERE kind = 'mock' ORDER BY at, id")}
        return {"day": day, "exam_day": profile.get("exam_day", 150),
                "exam": {"id": exam["id"], "name": exam["name"], "pass": exam["pass"], "retake": exam["retake"],
                         "parts": {part: p["name"] for part, p in exam["parts"].items()}},
                "next_gate": {**gate, "in_days": gate["day"] - day} if gate else None,
                "latest_mock": latest_mock,
                "checks_due": [kind for kind in CHECKS_EVERY_DAYS if self._check_due(kind)]}

    def focus(self) -> dict:
        """What to work on now: one word-order stage, one form, listening, reading and spoken target, probes.

        Word order follows the acquisition sequence as a diagnostic, not a gate. The earliest stage
        below free use that has failed becomes the target, and earlier stages that have not failed yet
        are probed. The next stage, if it fails too, is paired with it as one contrast (main-clause V2
        against verb-last); any later stage waits. Form and listening
        targets are ranked by failure rate; an answer that needed a hint counts as a failure here.
        Constructs with too few answers are probed. The spoken target is the word-order or form
        construct failing most in speech, which written drills don't fix.
        """
        since = (self.clock() - timedelta(days=ERROR_WINDOW_DAYS)).isoformat(timespec="seconds")
        stats = {r["construct_id"]: r for r in self.conn.execute(
            """SELECT construct_id, COUNT(*) AS n, SUM(correct = 0 OR hint_level > 0) AS f,
                      SUM(context = 'free' AND primed = 0) AS free_n,
                      SUM(context = 'free' AND primed = 0 AND (correct = 0 OR hint_level > 0)) AS free_f
               FROM attempts WHERE certain = 1 AND at >= ? GROUP BY construct_id""", (since,))}
        constructs = self.conn.execute("SELECT * FROM constructs WHERE treatable = 1 ORDER BY stage, id").fetchall()
        result: dict = {"word_order": None, "form": None, "listening": None, "reading": None, "spoken": None,
                        "probes": [], "waiting": []}
        target_stage = None
        for c in constructs:
            if c["track"] != "word_order" or c["level"] >= 4:
                continue
            s = stats.get(c["id"])
            failing = s is not None and s["f"] > 0
            target = result["word_order"]
            if target is None:
                if failing:
                    result["word_order"], target_stage = self._target(c, s), c["stage"]
                else:
                    result["probes"].append(c["id"])
            elif failing and c["stage"] == target_stage + 1:
                target["paired_with"] = self._target(c, s)
            elif failing:
                result["waiting"].append({"id": c["id"], "waits_for": target["id"]})
        other_probes = []
        for track in ("form", "listening", "reading"):
            candidates = []
            for c in constructs:
                s = stats.get(c["id"])
                if c["track"] != track or s is None or not s["f"]:
                    continue
                if s["n"] < MIN_OPPORTUNITIES:
                    other_probes.append(c)
                else:
                    candidates.append(self._target(c, s))
            if candidates:
                result[track] = sorted(candidates, key=lambda t: (-t["priority"], t["id"]))[0]
        other_probes.sort(key=lambda c: (-c["impact"] * c["relevance"], c["id"]))
        result["probes"] = (result["probes"] + [c["id"] for c in other_probes])[:MAX_PROBES]
        spoken = {r["construct_id"]: r for r in self.conn.execute(
            """SELECT construct_id, COUNT(*) AS n, SUM(correct = 0 OR hint_level > 0) AS f FROM attempts
               WHERE certain = 1 AND channel = 'spoken' AND at >= ? GROUP BY construct_id""", (since,))}
        candidates = []
        for c in constructs:
            s = spoken.get(c["id"])
            if c["track"] in PATTERN_TRACKS and s is not None and s["f"] and s["n"] >= MIN_OPPORTUNITIES:
                candidates.append(self._target(c, s, scope="speech"))
        if candidates:
            result["spoken"] = sorted(candidates, key=lambda t: (-t["priority"], t["id"]))[0]
        return result

    def get_lesson(self, unit: str) -> dict:
        """A book unit by id ("B1-U3") or by any of its pages ("12", "p52")."""
        wanted = str(unit).strip().lower()
        page = wanted.lstrip("p")
        rows = self.conn.execute("SELECT id, status, data FROM units ORDER BY position").fetchall()
        for r in rows:
            data = json.loads(r["data"])
            pages = {str(p) for numbers in data.get("pages", {}).values() for p in numbers}
            if r["id"].lower() == wanted or page in pages:
                targets = data.get("targets", [])
                known = {c["id"]: dict(c) for c in self._constructs_by_id(targets)}
                return {**data, "status": r["status"],
                        "targets": [known.get(t, {"id": t, "name": "not in catalog", "level": None})
                                    for t in targets]}
        loaded = ", ".join(r["id"] for r in rows) or "none"
        raise TutorError(f"No unit matches {unit!r} (loaded: {loaded}). Work from a photo of the page, "
                         "and add its extraction to data/book/ to track it.")

    # Session flow ------------------------------------------------------------

    def start_session(self, minutes: int = 180) -> dict:
        if "start_date" not in self.profile():
            raise TutorError("There is no learner yet. Ask the questions under onboarding in learner_summary, "
                             "then call save_profile.")
        if minutes < MIN_SESSION_MIN:
            raise TutorError(f"Plan at least {MIN_SESSION_MIN} minutes. On a bad day, 20 minutes is a minimum day.")
        still_open = self.conn.execute("SELECT id FROM sessions WHERE closed_at IS NULL").fetchone()
        if still_open:
            raise TutorError(f"Session {still_open['id']} is still open. Log its remaining answers and call "
                             "close_session before starting a new one.")
        plan = self._plan(minutes)
        now = self.now()
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO sessions (started_at, minutes_planned, plan, block_started_at, last_break_at,
                                         engine_version)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now, minutes, json.dumps(plan, ensure_ascii=False), now, now, __version__))
        return {"session_id": cur.lastrowid, "minutes": minutes,
                "plan": [{"step": i, "type": b["type"], "minutes": b["minutes"], "title": b["title"]}
                         for i, b in enumerate(plan, 1)]}

    def next_activity(self, session_id: int) -> dict:
        """The next block of today's plan, with what to practise and how."""
        session = self._open_session(session_id)
        plan = json.loads(session["plan"])
        pos = session["plan_pos"]
        if pos >= len(plan):
            return {"done": True, "message": "The plan is finished. Log any remaining answers, then call close_session."}
        block = {"step": pos + 1, "of": len(plan), **plan[pos]}
        if block["type"] == "review":
            block["retests"] = self._due_retests()
        if block["type"] == "speaking":
            block["reuse_phrases"] = self._last_speaking_phrases(session_id)
        now = self.now()
        due_break = self.clock() - datetime.fromisoformat(session["last_break_at"]) >= timedelta(minutes=BREAK_AFTER_MIN)
        if due_break:
            block["break_first"] = f"Take a {BREAK_MIN}-minute break before this block."
        with self.conn:
            self.conn.execute("UPDATE sessions SET plan_pos = ?, block_started_at = ?, last_break_at = ? WHERE id = ?",
                              (pos + 1, now, now if due_break else session["last_break_at"], session_id))
            if block.get("unit"):
                self.conn.execute("UPDATE units SET status = 'in_progress' WHERE id = ? AND status = 'new'",
                                  (block["unit"],))
        return block

    def log_attempt(self, session_id: int, construct: str, context: str, correct: bool,
                    channel: str = "typed", hint_level: int = 0, repair: str = "none", certain: bool = True,
                    trigger: str | None = None, retest: int | None = None, item: str | None = None,
                    answer: str | None = None, expected: str | None = None, sentence: str | None = None,
                    correction: str | None = None, cause: str | None = None, scorer: str = "tutor",
                    reason: str | None = None) -> dict:
        """Record one judged answer for one construct, and update its mastery level.

        An error on a word-order or form construct needs its trigger, and joins that pattern.
        With `retest`, the answer is a retest of that pattern. With `reason`, a right answer the learner
        justified wrongly (or, on reading and listening items, couldn't back with the words) is a guess:
        logged, never scored.
        """
        session = self._open_session(session_id)
        c = self._construct(construct)
        before = c["level"]
        if context not in CONTEXTS:
            raise TutorError(f"context must be one of: {', '.join(CONTEXTS)}.")
        if channel not in CHANNELS:
            raise TutorError(f"channel must be one of: {', '.join(CHANNELS)}.")
        if repair not in REPAIRS:
            raise TutorError(f"repair must be one of: {', '.join(REPAIRS)}.")
        if not 0 <= hint_level <= 3:
            raise TutorError("hint_level is 0 (none), 1 (category), 2 (location) or 3 (partial answer).")
        if reason is not None and reason not in REASONS:
            raise TutorError(f"reason must be one of: {', '.join(REASONS)}.")
        guess = bool(correct) and (reason == "contradicts" or (construct in EVIDENCE_CONSTRUCTS
                                                                and reason in ("absent", "contradicts")))
        if guess:
            certain = False
        if correct and repair != "none":
            raise TutorError("A repaired answer was not right first time: send correct=false with repair='self' "
                             "or 'prompted'.")
        trigger = " ".join((trigger or "").lower().split()) or None
        pattern = self._retest_pattern(retest, construct) if retest is not None else None
        if pattern is not None:
            trigger = trigger or pattern["trigger"]
        is_error = certain and not correct
        patterned = bool(c["treatable"]) and c["track"] in PATTERN_TRACKS
        if is_error and patterned and not trigger:
            raise TutorError("Send trigger: the word that set the rule off, e.g. deswegen (verb second after it), "
                             "weil (verb last), mit (the case after it), the noun for an article or ending, the "
                             "verb for a verb form.")
        causes = self._miss_causes(construct, channel)
        if is_error and causes and cause not in causes:
            raise TutorError(f"For a {construct} miss, cause is one of: {', '.join(causes)}.")
        primed = self.conn.execute(
            "SELECT 1 FROM attempts WHERE session_id = ? AND construct_id = ? AND context != 'free' LIMIT 1",
            (session_id, construct)).fetchone() is not None
        now = self.now()
        with self.conn:
            attempt_id = self.conn.execute(
                """INSERT INTO attempts (session_id, at, construct_id, item, context, channel, correct, repair,
                                         certain, primed, hint_level, answer, expected, scorer, trigger, retest_of,
                                         reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, now, construct, item, context, channel, int(bool(correct)), repair,
                 int(bool(certain)), int(primed), hint_level, answer, expected, scorer, trigger,
                 pattern["id"] if pattern is not None else None, reason)).lastrowid
            if is_error:
                pattern_id = self._note_error_pattern(construct, trigger, channel, now) if patterned else None
                self.conn.execute(
                    """INSERT INTO errors (attempt_id, construct_id, at, sentence, correction, cause, pattern_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (attempt_id, construct, now, sentence or answer, correction or expected, cause, pattern_id))
            if pattern is not None and correct:
                retest_result = self._pass_retest(pattern, counts=certain and hint_level == 0 and not primed)
            elif pattern is not None:
                retest_result = {"pattern": pattern["id"], "passed": False,
                                 "message": "Not passed. It comes back next session." if certain else
                                            "Unresolved answers don't count. The retest stays due."}
            level, note = self._update_level(construct)
        result = {"attempt_id": attempt_id, "construct": construct, "level_before": before, "level": level,
                  "level_name": LEVEL_NAMES[level]}
        if note:
            result["note"] = note
        if guess:
            result["guess"] = ("Right, but the reason contradicts it: logged as a guess, not scored. Teach the "
                               "contrast (which word decides it) and give one new item on it before the session "
                               "ends." if reason == "contradicts" else
                               "Right, but not backed by the words in the text: logged as a guess, not scored.")
        elif not correct and reason == "supports":
            result["reason_note"] = (
                "They found the right place and misread it: hint about that sentence."
                if construct in EVIDENCE_CONSTRUCTS else
                "The reason is right but the form isn't: the rule is known and not yet automatic. Practise it "
                "in new sentences rather than explaining it again.")
        if pattern is not None:
            result["retest"] = retest_result
        signals = self._signals(session)
        if signals:
            result["signals"] = signals
        return result

    def close_session(self, session_id: int, attempts_expected: int, fatigue: int | None = None,
                      notes: str | None = None) -> dict:
        """Refuses to close while fewer answers are logged than the tutor judged.

        This catches forgotten logging; it cannot prove that nothing went unreported.
        """
        session = self._open_session(session_id)
        if fatigue is not None and not 1 <= fatigue <= 5:
            raise TutorError("fatigue is 1 (fresh) to 5 (exhausted).")
        rows = self.conn.execute(
            """SELECT a.construct_id, a.correct, a.hint_level, a.repair, a.channel, a.certain, c.name, c.level
               FROM attempts a JOIN constructs c ON c.id = a.construct_id
               WHERE a.session_id = ? ORDER BY a.id""", (session_id,)).fetchall()
        if len(rows) < attempts_expected:
            raise TutorError(f"Only {len(rows)} of {attempts_expected} answers are logged. Log the other "
                             f"{attempts_expected - len(rows)} with log_attempt, then close again.")
        by_construct: dict[str, dict] = {}
        for r in rows:
            entry = by_construct.setdefault(r["construct_id"], {"construct": r["construct_id"], "name": r["name"],
                                                                 "right": 0, "wrong": 0, "unresolved": 0,
                                                                 "level": r["level"]})
            entry["unresolved" if not r["certain"] else "right" if r["correct"] else "wrong"] += 1
        scored = [r["correct"] for r in rows if r["certain"] and r["hint_level"] == 0]
        # Targets of the blocks reached but never logged: the tutor may be avoiding what it can't judge.
        reached = json.loads(session["plan"])[:session["plan_pos"]]
        planned = dict.fromkeys(c for b in reached for c in b.get("constructs", []))
        focus = self.focus()
        summary = {
            "date": self.today().isoformat(),
            "attempts": len(rows),
            "first_pass_accuracy": f"{round(100 * sum(scored) / len(scored))}%" if scored else None,
            "repairs": {kind: sum(r["repair"] == kind for r in rows) for kind in ("self", "prompted")},
            "channels": {ch: sum(r["channel"] == ch for r in rows) for ch in CHANNELS},
            "unresolved": sum(not r["certain"] for r in rows),
            "by_construct": list(by_construct.values()),
            "not_elicited": [c for c in planned if c not in by_construct],
            "slips": [r["sentence"] for r in self.conn.execute(
                """SELECT e.sentence FROM errors e JOIN attempts a ON a.id = e.attempt_id
                   WHERE a.session_id = ? AND e.construct_id = 'slip' ORDER BY e.id""", (session_id,))],
            "samples": self.conn.execute("SELECT COUNT(*) FROM samples WHERE session_id = ?",
                                         (session_id,)).fetchone()[0],
            "retests": self._retest_summary(session_id),
            "reasons": {kind: n for kind, n in self.conn.execute(
                "SELECT reason, COUNT(*) FROM attempts WHERE session_id = ? AND reason IS NOT NULL GROUP BY reason",
                (session_id,))},
            "words": self._session_words(session_id),
            "focus_next": {track: (focus[track] or {}).get("id")
                           for track in ("word_order", "form", "listening", "reading", "spoken")},
            "probes_next": focus["probes"],
            "notes": notes,
        }
        with self.conn:
            self.conn.execute("UPDATE sessions SET closed_at = ?, fatigue = ?, summary = ? WHERE id = ?",
                              (self.now(), fatigue, json.dumps(summary, ensure_ascii=False), session_id))
        return {"session_id": session_id, "closed": True, **summary}

    def log_sample(self, session_id: int, kind: str, text: str, audio_path: str | None = None,
                   note: str | None = None) -> dict:
        """Keep a piece of the learner's own German, so it can be re-scored, rated and compared later."""
        self._open_session(session_id)
        if kind not in SAMPLE_KINDS:
            raise TutorError(f"kind must be one of: {', '.join(SAMPLE_KINDS)}.")
        if not text.strip():
            raise TutorError("text is the learner's words exactly as said or written, uncorrected.")
        with self.conn:
            sample_id = self.conn.execute(
                "INSERT INTO samples (session_id, at, kind, text, audio_path, note) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, self.now(), kind, text, audio_path, note)).lastrowid
        return {"sample_id": sample_id, "words": len(text.split())}

    def log_measure(self, kind: str, score: float, max_score: float | None = None, part: str | None = None,
                    rater: str | None = None, sample_id: int | None = None, note: str | None = None) -> dict:
        """Record an objective measure: a mock-exam module, C-test, onSET, LexTALE, fluency, a human rating, ..."""
        if kind not in catalog.MEASURE_KINDS:
            raise TutorError(f"kind must be one of: {', '.join(catalog.MEASURE_KINDS)}.")
        if part is not None:
            part = part.strip().lower().replace("ö", "oe")
            parts = self.exam()["parts"]
            if part not in parts:
                raise TutorError(f"part must be one of the {self.exam()['name']} parts: {', '.join(parts)}.")
        if rater is not None and rater not in catalog.RATERS:
            raise TutorError(f"rater must be one of: {', '.join(catalog.RATERS)}.")
        if kind == "mock" and (part is None or rater is None or max_score is None):
            raise TutorError(f"A mock score needs part ({', '.join(self.exam()['parts'])}), max_score and rater "
                             "(answer_key, human or model). Only answer_key and human ratings count for gates.")
        if score < 0 or (max_score is not None and (max_score <= 0 or score > max_score)):
            raise TutorError("score must be at least 0 and, with max_score, no more than it.")
        if sample_id is not None and not self.conn.execute("SELECT 1 FROM samples WHERE id = ?",
                                                           (sample_id,)).fetchone():
            raise TutorError(f"No sample {sample_id}. Store the text with log_sample first.")
        with self.conn:
            measure_id = self.conn.execute(
                """INSERT INTO measures (at, kind, part, rater, sample_id, score, max_score, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.now(), kind, part, rater, sample_id, score, max_score, note)).lastrowid
        return {"measure_id": measure_id, "progress": self.progress()}

    def set_unit_status(self, unit: str, status: str) -> dict:
        if status not in ("new", "in_progress", "done"):
            raise TutorError("status must be new, in_progress or done.")
        with self.conn:
            changed = self.conn.execute("UPDATE units SET status = ? WHERE id = ?", (status, unit)).rowcount
        if not changed:
            raise TutorError(f"No unit {unit!r}.")
        return {"unit": unit, "status": status}

    # Human lesson --------------------------------------------------------------

    def human_brief(self) -> dict:
        """A brief for the weekly human lesson, which tests transfer rather than teaching: what to listen for,
        what to push the learner into, stored speech to rate blind, the tutor model's recent corrections to
        audit, and every two weeks a sentence-repetition test."""
        focus = self.focus()
        pair = (focus["word_order"] or {}).get("paired_with")
        names = {r["id"]: r["name"] for r in self.conn.execute("SELECT id, name FROM constructs")}
        speech = self.conn.execute("SELECT id, text, audio_path FROM samples WHERE kind = 'speech' ORDER BY at, id"
                                   ).fetchall()
        # The oldest, a middle and the newest sample, shuffled, so the rater can't tell which is recent.
        picked = speech if len(speech) <= SAMPLES_TO_RATE else [speech[0], speech[len(speech) // 2], speech[-1]]
        picked = list(picked)
        random.Random(self.today().isoformat()).shuffle(picked)
        brief = {
            "format": "About 60 minutes. 5 to warm up; 40 of unscripted conversation, including one task the "
                      "learner can't pause or prepare (a phone call to move an appointment, a complaint, a "
                      "disagreement with a colleague) with an unexpected turn; 15 for the ratings, the audit and "
                      "feedback. Don't teach grammar: listen, steer the learner into the structures below without "
                      "naming them, write down errors with the exact sentence, and correct by asking the learner to "
                      "fix it rather than by saying the right form.",
            "listen_for": [{"rule": names[p["construct"]], "after": p["trigger"], "last_time": p["last_sentence"],
                            "errors": p["errors"], "days": p["days"]}
                           for p in self._listen_for()] or
                          "No recurring mistakes yet: write down every error with the exact sentence.",
            "push_into": list(dict.fromkeys(t["name"] for t in (focus["spoken"], focus["word_order"], pair,
                                                                 focus["form"]) if t)),
            "samples_to_rate": [{"label": f"S{n}", "sample_id": r["id"], "text": r["text"],
                                 **({"audio": r["audio_path"]} if r["audio_path"] else {})}
                                for n, r in enumerate(picked, 1)] or
                               "No stored speech yet: record two minutes of the conversation, and store the "
                               "learner's transcript with log_sample('speech') for next time.",
            "rating": "Rate each sample without being told when it was recorded: comprehensibility, fluency and "
                      "accuracy, 1 to 5 each. Log the sum with log_measure('human_rating', score, 15, "
                      "rater='human', sample_id=..., note='comprehensibility 4, fluency 3, accuracy 3').",
            "corrections_to_audit": [dict(r) for r in self.conn.execute(
                """SELECT e.id AS error_id, c.name AS rule, e.sentence AS learner, e.correction AS tutor_correction
                   FROM errors e JOIN attempts a ON a.id = e.attempt_id JOIN constructs c ON c.id = e.construct_id
                   WHERE a.scorer = 'tutor' AND c.track IN ('word_order', 'form') AND e.sentence IS NOT NULL
                   ORDER BY e.at DESC, e.id DESC LIMIT ?""", (AUDIT_SIZE,))],
            "audit": "For each correction, say whether it is right and was needed (spoken German allows some "
                     "things writing doesn't). Log log_measure('correction_audit', agreed, checked, rater='human'), "
                     "with the error_ids of wrong corrections in the note.",
            "after_the_lesson": "Log each error the human noted with log_attempt (context free, channel spoken, "
                                "scorer human, with its trigger), and each correct use they confirmed the same way: "
                                "human-judged free use is what can lift a construct to level 4.",
        }
        if self._check_due("elicited_imitation"):
            brief["sentence_repetition"] = self._sentence_repetition()
        return brief

    def _listen_for(self) -> list[dict]:
        """The most recurrent open patterns, one per rule, so the human listens for three different things."""
        picked: dict[str, dict] = {}
        for p in self._patterns(limit=100)["top"]:
            picked.setdefault(p["construct"], p)
            if len(picked) == SAMPLES_TO_RATE:
                break
        return list(picked.values())

    def _sentence_repetition(self) -> dict:
        data = json.loads((MEASURES_DIR / "elicited_imitation.json").read_text(encoding="utf-8"))
        done = self.conn.execute("SELECT COUNT(*) FROM measures WHERE kind = 'elicited_imitation'").fetchone()[0]
        form = sorted(data["forms"])[done % len(data["forms"])]
        sentences = data["forms"][form]
        top = 2 * len(sentences)
        return {"form": form, "protocol": data["protocol"], "scoring": data["scoring"], "note": data["note"],
                "sentences": sentences,
                "log": f"log_measure('elicited_imitation', score, {top}, rater='human', note='form {form}')"}

    # Words -------------------------------------------------------------------

    def word_check(self, session_id: int, words: list, source: str | None = None) -> dict:
        """Test up to CHECK_SIZE of the tutor's candidate words by multiple choice; the engine keeps the key.

        Words already known are skipped. Words the task needs are always candidates, whatever their rank:
        a subtitle list ranks job words such as Bewerbung low, and the learner needs them anyway. Needed
        common words come first, then the other needed words in the tutor's order, then common words the
        task doesn't need. Other words are only glossed: deliberate study pays off for frequent words
        (Nation). Nouns get a gender item as well.
        """
        self._open_session(session_id)
        candidates = self._parse_words(words)
        now = self.now()
        tested, glossed, known = [], [], []
        with self.conn:
            for order, w in enumerate(candidates):
                word = self._upsert_word(w, source, now)
                status = self._word_status(word)
                if status["meaning"] == "known":
                    known.append(self._display(word))
                    continue
                rank = self._rank(word["lemma"])
                common = rank is not None and rank <= COMMON_RANK
                if not (w["needed"] or common):
                    glossed.append((word, w["gloss"]))
                    continue
                tier = 0 if w["needed"] and common else 1 if w["needed"] else 2
                tested.append(((tier, rank if tier != 1 else 0, order), word, w, status))
            tested.sort(key=lambda t: t[0])
            glossed += [(word, w["gloss"]) for _, word, w, _ in tested[CHECK_SIZE:]]
            chosen = tested[:CHECK_SIZE]
            rng = random.Random(f"{session_id}:{now}:{len(candidates)}")
            pool = [(w["pos"], w["gloss"]) for w in candidates] + [
                (r["pos"], r["gloss"]) for r in self.conn.execute("SELECT pos, gloss FROM words ORDER BY id")]
            questions = []
            for _, word, w, _ in chosen:
                options = self._options(rng, w["gloss"], word["pos"], pool)
                questions.append({"word_id": word["id"], "facet": "meaning", "ask": word["lemma"], "gloss": w["gloss"],
                                  "needed": w["needed"], "options": options,
                                  "key": LETTERS[options.index(w["gloss"])]})
            for _, word, w, status in chosen:
                if word["pos"] == "noun" and status["gender"] != "known":
                    options = [*ARTICLES, DONT_KNOW]
                    questions.append({"word_id": word["id"], "facet": "gender",
                                      "ask": f"der, die or das: {word['lemma']}?", "needed": w["needed"],
                                      "options": options, "key": LETTERS[options.index(word["article"])]})
            items = {"questions": questions, "glossed": [word["id"] for word, _ in glossed]}
            check_id = self.conn.execute(
                """INSERT INTO word_checks (session_id, at, purpose, source, items, answered_at)
                   VALUES (?, ?, 'check', ?, ?, ?)""",
                (session_id, now, source, json.dumps(items, ensure_ascii=False), None if questions else now)).lastrowid
        return {
            "check_id": check_id,
            "items": [{"n": n, "ask": q["ask"], "options": dict(zip(LETTERS, q["options"]))}
                      for n, q in enumerate(questions, 1)],
            "gloss_only": [{"word": self._display(word), "gloss": gloss} for word, gloss in glossed],
            "already_known": known,
            "instructions": (
                "Show the items exactly as written: no sentence, no hint, no translation. The learner answers each "
                f"straight away, or with '{DONT_KNOW}' rather than guess. Send their letters, in item order, with "
                "answer_word_check. Show the gloss_only words beside the text." if questions else
                "Nothing to test: show the gloss_only words beside the text and start the task."),
        }

    def answer_word_check(self, check_id: int, answers: list) -> dict:
        """Score a word check from the learner's letters, record the evidence, and say what to teach."""
        row = self.conn.execute("SELECT * FROM word_checks WHERE id = ? AND purpose = 'check'",
                                (check_id,)).fetchone()
        if row is None:
            raise TutorError(f"No word check {check_id}. Start one with word_check.")
        self._open_session(row["session_id"])
        if row["answered_at"]:
            raise TutorError(f"Word check {check_id} is already answered.")
        questions = json.loads(row["items"])["questions"]
        letters = [str(a).strip().upper() for a in answers]
        if len(letters) != len(questions):
            raise TutorError(f"Send {len(questions)} letters, one per item, in item order.")
        for n, (q, letter) in enumerate(zip(questions, letters), 1):
            if letter not in LETTERS[:len(q["options"])]:
                raise TutorError(f"Item {n}: the answer is one letter, {LETTERS[0]} to {LETTERS[len(q['options']) - 1]}.")
        seen = self._seen_words(row["session_id"])
        now = self.now()
        results: dict[int, dict] = {}
        scored = right = 0
        with self.conn:
            for q, letter in zip(questions, letters):
                chosen = q["options"][LETTERS.index(letter)]
                correct = letter == q["key"]
                entry = results.setdefault(q["word_id"], {"needed": q["needed"]})
                outcome = "right" if correct else "didn't know" if chosen == DONT_KNOW else "wrong"
                if q["facet"] == "meaning":
                    entry.update(meaning=outcome, gloss=q["gloss"])
                elif entry.get("meaning") != "right":
                    continue  # a gender guess on a word whose meaning is unknown says nothing
                else:
                    entry["gender"] = outcome
                scored += 1
                right += correct
                self._add_evidence(q["word_id"], row["session_id"], now, q["facet"], correct,
                                   q["word_id"] in seen, "code:word_check", chosen)
            self.conn.execute("UPDATE word_checks SET answered_at = ? WHERE id = ?", (now, check_id))
            report, teach, gloss, queued = [], [], [], []
            for word_id, entry in results.items():
                word = self._word(word_id)
                shown = f"{self._display(word)} = {entry['gloss']}"
                report.append({"word": self._display(word), "gloss": entry["gloss"], "meaning": entry["meaning"],
                               **({"gender": entry["gender"]} if "gender" in entry else {})})
                if entry["meaning"] != "right":
                    (teach if entry["needed"] and len(teach) < TEACH_MAX else gloss).append(shown)
                if (entry["meaning"] != "right" or entry.get("gender", "right") != "right") and \
                        self._queue_for_anki(word, entry["needed"], now):
                    queued.append(self._display(word))
        return {
            "right": f"{right} of {scored}",
            "results": report,
            "teach": teach,
            "gloss": gloss,
            "queued_for_anki": queued,
            "instructions": (
                "Show the results as one short list (der Termin = appointment). Teach each word in `teach` in about "
                "30 seconds: article and noun together, its sentence from the text, then the learner uses it once "
                "in a sentence of their own. Gloss the words in `gloss` in one line, then start the task. Every word "
                "in this check now counts as shown for the rest of the session."),
        }

    def show_words(self, session_id: int, words: list, source: str | None = None) -> dict:
        """Record words given to the learner (a planning step, a gloss), so using them today counts as primed."""
        self._open_session(session_id)
        candidates = self._parse_words(words)
        now = self.now()
        with self.conn:
            shown = [self._upsert_word(w, source, now) for w in candidates]
            self.conn.execute(
                """INSERT INTO word_checks (session_id, at, purpose, source, items, answered_at)
                   VALUES (?, ?, 'preview', ?, ?, ?)""",
                (session_id, now, source, json.dumps({"questions": [], "glossed": [w["id"] for w in shown]}), now))
        return {"shown": [self._display(w) for w in shown],
                "note": "These words count as given for the rest of this session: using them today is not "
                        "evidence of the learner's own knowledge."}

    def log_word(self, session_id: int, lemma: str, facet: str, correct: bool, pos: str | None = None,
                 gloss: str | None = None, article: str | None = None, answer: str | None = None,
                 example: str | None = None) -> dict:
        """Record what a session showed about one word: a meaning asked about, a word missed in audio but
        clear in print, a wrong article, or a word used without help."""
        self._open_session(session_id)
        if facet not in WORD_FACETS:
            raise TutorError(f"facet must be one of: {', '.join(WORD_FACETS)}.")
        if facet == "meaning" and correct:
            raise TutorError("A tutor can't record that a meaning is known: understanding a text doesn't prove each "
                             "word. Test it with word_check, or log facet 'use' when the learner used the word "
                             "themselves.")
        now = self.now()
        with self.conn:
            if pos is not None or gloss is not None:
                word = self._upsert_word(self._parse_words([{"lemma": lemma, "pos": pos, "gloss": gloss,
                                                             "article": article, "example": example}])[0], None, now)
            else:
                word = self._find_word(lemma)
            if facet == "gender" and word["pos"] != "noun":
                raise TutorError(f"{lemma!r} is not a noun: gender is for nouns.")
            primed = word["id"] in self._seen_words(session_id)
            self._add_evidence(word["id"], session_id, now, facet, correct, primed, "tutor", answer)
            queued = not correct and facet in ("meaning", "gender") and self._queue_for_anki(word, False, now)
        result = {"word": self._display(word), "facet": facet, "status": self._word_status(word),
                  "queued_for_anki": queued}
        if primed:
            result["note"] = "This word was shown or corrected earlier in the session, so this doesn't count yet."
        return result

    def export_anki(self) -> dict:
        """Write the words queued for Anki to a file the learner imports; at most ANKI_PER_DAY a day."""
        if self.export_dir is None:
            raise TutorError("No export folder: this engine runs on an in-memory database.")
        done_today = self.conn.execute("SELECT COUNT(*) FROM words WHERE anki_exported_at >= ?",
                                       (self.today().isoformat(),)).fetchone()[0]
        waiting = self.conn.execute(
            """SELECT * FROM words WHERE anki_queued_at IS NOT NULL AND anki_exported_at IS NULL
               ORDER BY anki_queued_at, id""").fetchall()
        batch = waiting[:max(0, ANKI_PER_DAY - done_today)]
        if not batch:
            return {"exported": 0, "left_in_queue": len(waiting),
                    "message": f"Today's {ANKI_PER_DAY} new cards are already exported; the rest wait for tomorrow."
                    if waiting else "No words are waiting for Anki."}
        lines = ["#separator:tab", "#html:false", "#tags column:3"]
        for w in batch:
            back = w["gloss"] + (f" | {w['example']}" if w["example"] else "")
            tags = "german-tutor" + (" " + "_".join(w["source"].split()) if w["source"] else "")
            lines.append("\t".join(" ".join(str(field).split()) for field in (self._display(w), back, tags)))
        self.export_dir.mkdir(parents=True, exist_ok=True)
        path = self.export_dir / f"german-tutor-{self.clock():%Y-%m-%d-%H%M%S}.tsv"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        now = self.now()
        with self.conn:
            self.conn.executemany("UPDATE words SET anki_exported_at = ? WHERE id = ?", [(now, w["id"]) for w in batch])
        return {"exported": len(batch), "file": str(path), "left_in_queue": len(waiting) - len(batch),
                "instructions": "Ask the learner to import the file in Anki (File > Import) into their German deck "
                                "with the note type 'Basic (and reversed card)', so each word is practised from "
                                "German and from English, article included."}

    # Internals ---------------------------------------------------------------

    def _open_session(self, session_id: int) -> sqlite3.Row:
        session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise TutorError(f"No session {session_id}. Call start_session first.")
        if session["closed_at"]:
            raise TutorError(f"Session {session_id} is closed. Start a new one.")
        return session

    def _construct(self, construct_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM constructs WHERE id = ?", (construct_id,)).fetchone()
        if row is None:
            ids = ", ".join(r["id"] for r in self.conn.execute("SELECT id FROM constructs ORDER BY id"))
            raise TutorError(f"Unknown construct {construct_id!r}. Use one of: {ids}.")
        return row

    def _constructs_by_id(self, ids: list[str]) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self.conn.execute(f"SELECT id, name, level FROM constructs WHERE id IN ({marks})", ids).fetchall()

    def _examples(self, construct_id: str, limit: int = 3) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            """SELECT e.sentence, e.correction, a.trigger, e.cause FROM errors e JOIN attempts a ON a.id = e.attempt_id
               WHERE e.construct_id = ? ORDER BY e.at DESC, e.id DESC LIMIT ?""", (construct_id, limit))]

    def _target(self, c: sqlite3.Row, s: sqlite3.Row, scope: str | None = None) -> dict:
        """A focus target, ranked by its smoothed failure rate in unprimed free use when there is enough,
        or over `scope` when given."""
        if scope is None and s["free_n"] >= MIN_OPPORTUNITIES:
            n, f, where = s["free_n"], s["free_f"], "free use"
        else:
            n, f, where = s["n"], s["f"], scope or "all practice"
        rate = (f + 1) / (n + 2)  # smoothing keeps 1-of-1 from outranking 4-of-10
        return {"id": c["id"], "name": c["name"], "rule": c["rule"], "level": c["level"],
                "failed": f"{f} of {n} ({where}, last {ERROR_WINDOW_DAYS} days)",
                "priority": round(rate * c["impact"] * c["relevance"], 2),
                "by_channel": self._rates(c["id"], "channel"),
                "triggers": self._rates(c["id"], "trigger"),
                "examples": self._examples(c["id"])}

    def _rates(self, construct_id: str, column: str) -> dict:
        """Failures per channel or per trigger over the focus window, worst first."""
        assert column in ("channel", "trigger")
        since = (self.clock() - timedelta(days=ERROR_WINDOW_DAYS)).isoformat(timespec="seconds")
        return {r["k"]: f"{r['f']} of {r['n']} failed" for r in self.conn.execute(
            f"""SELECT {column} AS k, COUNT(*) AS n, SUM(correct = 0 OR hint_level > 0) AS f FROM attempts
                WHERE construct_id = ? AND certain = 1 AND at >= ? AND {column} IS NOT NULL
                GROUP BY {column} ORDER BY 1.0 * f / n DESC, n DESC, k""", (construct_id, since))}

    @staticmethod
    def _miss_causes(construct_id: str, channel: str) -> tuple:
        if construct_id == "reading_detail":
            return READING_CAUSES
        if construct_id in ("listening_detail", "numbers_times") and channel == "listening":
            return LISTENING_CAUSES
        return ()

    # Error patterns ----------------------------------------------------------

    def _note_error_pattern(self, construct_id: str, trigger: str, channel: str, now: str) -> int:
        """An error joins its pattern (or starts one) and resets it: the next retest is due in RETEST_DAYS[0]."""
        due = (self.today() + timedelta(days=RETEST_DAYS[0])).isoformat()
        row = self.conn.execute("SELECT id FROM patterns WHERE construct_id = ? AND trigger = ?",
                                (construct_id, trigger)).fetchone()
        if row:
            self.conn.execute("""UPDATE patterns SET last_at = ?, last_channel = ?, stage = 0, due = ?,
                                     resolved_at = NULL WHERE id = ?""", (now, channel, due, row["id"]))
            return row["id"]
        return self.conn.execute(
            """INSERT INTO patterns (construct_id, trigger, first_at, last_at, last_channel, due)
               VALUES (?, ?, ?, ?, ?, ?)""", (construct_id, trigger, now, now, channel, due)).lastrowid

    def _retest_pattern(self, pattern_id: int, construct_id: str) -> sqlite3.Row:
        p = self.conn.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,)).fetchone()
        if p is None:
            raise TutorError(f"No pattern {pattern_id}. Retests come from next_activity's review block.")
        if p["construct_id"] != construct_id:
            raise TutorError(f"Pattern {pattern_id} is {p['construct_id']} after {p['trigger']!r}: log the retest "
                             "under that construct.")
        if p["resolved_at"]:
            raise TutorError(f"Pattern {pattern_id} is already resolved. Log this as an ordinary answer.")
        if p["due"] > self.today().isoformat():
            raise TutorError(f"Pattern {pattern_id} isn't due until {p['due']}: retests are spaced. Log this as an "
                             "ordinary answer.")
        return p

    def _pass_retest(self, pattern: sqlite3.Row, counts: bool) -> dict:
        if not counts:
            return {"pattern": pattern["id"], "passed": False,
                    "message": "Right, but with a hint, unsure, or after this construct was drilled today, so it "
                               "doesn't count. The retest stays due."}
        stage = pattern["stage"] + 1
        if stage >= len(RETEST_DAYS):
            self.conn.execute("UPDATE patterns SET stage = ?, due = NULL, resolved_at = ? WHERE id = ?",
                              (stage, self.now(), pattern["id"]))
            return {"pattern": pattern["id"], "passed": True, "resolved": True}
        due = (self.today() + timedelta(days=RETEST_DAYS[stage])).isoformat()
        self.conn.execute("UPDATE patterns SET stage = ?, due = ? WHERE id = ?", (stage, due, pattern["id"]))
        return {"pattern": pattern["id"], "passed": True, "next_retest": due}

    def _pattern_rows(self, where: str, params: tuple, order: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"""SELECT p.*, c.name, c.impact * c.relevance AS weight, COUNT(e.id) AS errors,
                       COUNT(DISTINCT substr(e.at, 1, 10)) AS days
                FROM patterns p JOIN constructs c ON c.id = p.construct_id JOIN errors e ON e.pattern_id = p.id
                WHERE {where} GROUP BY p.id ORDER BY {order}""", params).fetchall()

    def _last_error(self, pattern_id: int) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT sentence, correction FROM errors WHERE pattern_id = ? ORDER BY at DESC, id DESC LIMIT 1",
            (pattern_id,)).fetchone()

    def _due_retests(self) -> list[dict]:
        """Due patterns, recurring ones first, at most one per construct so one retest doesn't prime the next."""
        picked, constructs = [], set()
        for r in self._pattern_rows("p.resolved_at IS NULL AND p.due <= ?", (self.today().isoformat(),),
                                    "days DESC, weight DESC, p.due, p.id"):
            if r["construct_id"] in constructs:
                continue
            constructs.add(r["construct_id"])
            last = self._last_error(r["id"])
            picked.append({"retest": r["id"], "construct": r["construct_id"], "name": r["name"],
                           "trigger": r["trigger"], "channel": r["last_channel"],
                           "seen": f"{r['errors']} error(s) on {r['days']} day(s)",
                           "their_sentence": last["sentence"], "correction": last["correction"],
                           "retest_number": f"{r['stage'] + 1} of {len(RETEST_DAYS)}"})
            if len(picked) == RETESTS_PER_SESSION:
                break
        return picked

    def _patterns(self, limit: int = 5) -> dict:
        """The open patterns that recur most, for the tutor's picture of the learner."""
        top = []
        for r in self._pattern_rows("p.resolved_at IS NULL", (), "days DESC, errors DESC, p.last_at DESC")[:limit]:
            last = self._last_error(r["id"])
            channels = [c[0] for c in self.conn.execute(
                """SELECT DISTINCT a.channel FROM errors e JOIN attempts a ON a.id = e.attempt_id
                   WHERE e.pattern_id = ? ORDER BY a.channel""", (r["id"],))]
            top.append({"construct": r["construct_id"], "trigger": r["trigger"], "errors": r["errors"],
                        "days": r["days"], "channels": channels, "last_sentence": last["sentence"],
                        "correction": last["correction"], "next_retest": r["due"], "retests_passed": r["stage"]})
        counts = self.conn.execute("SELECT SUM(resolved_at IS NULL), SUM(resolved_at IS NOT NULL) FROM patterns"
                                   ).fetchone()
        return {"open": counts[0] or 0, "resolved": counts[1] or 0, "top": top}

    def _retest_summary(self, session_id: int) -> dict:
        rows = self.conn.execute(
            """SELECT correct, certain, hint_level, primed FROM attempts
               WHERE session_id = ? AND retest_of IS NOT NULL""", (session_id,)).fetchall()
        return {"tried": len(rows),
                "passed": sum(1 for r in rows if r["correct"] and r["certain"] and not r["hint_level"]
                              and not r["primed"]),
                "failed": sum(1 for r in rows if r["certain"] and not r["correct"])}

    # Words -------------------------------------------------------------------

    def _parse_words(self, words: list) -> list[dict]:
        if not isinstance(words, list) or not words:
            raise TutorError("words is a list of objects: lemma, pos, gloss, and article for nouns.")
        if len(words) > CANDIDATES_MAX:
            raise TutorError(f"Send at most {CANDIDATES_MAX} words.")
        parsed = []
        for w in words:
            if not isinstance(w, dict):
                raise TutorError("Each word is an object: lemma, pos, gloss, and article for nouns.")
            unknown = set(w) - set(WORD_FIELDS)
            if unknown:
                raise TutorError(f"Unknown word fields: {', '.join(sorted(unknown))}. Use: {', '.join(WORD_FIELDS)}.")
            pos = w.get("pos")
            if pos not in WORD_POS:
                raise TutorError(f"pos must be one of: {', '.join(WORD_POS)}.")
            lemma, article = self._split_article(str(w.get("lemma") or ""), pos, w.get("article"))
            gloss = " ".join(str(w.get("gloss") or "").split())
            if not lemma or not gloss:
                raise TutorError("Each word needs its lemma (dictionary form) and an English gloss for the sense "
                                 "used here.")
            if pos == "noun" and article not in ARTICLES:
                raise TutorError(f"The noun {lemma!r} needs its article: der, die or das.")
            parsed.append({"lemma": lemma, "pos": pos, "gloss": gloss, "article": article if pos == "noun" else None,
                           "example": w.get("example"), "needed": bool(w.get("needed", False))})
        return parsed

    @staticmethod
    def _split_article(lemma: str, pos: str | None, article: str | None) -> tuple[str, str | None]:
        """'die Aussage' -> ('Aussage', 'die'); nouns are capitalised, verbs and adjectives are not."""
        lemma = " ".join(lemma.split())
        article = article.strip().lower() if isinstance(article, str) else None
        if pos == "noun":
            first, _, rest = lemma.partition(" ")
            if first.lower() in ARTICLES and rest:
                article, lemma = article or first.lower(), rest
            lemma = lemma[:1].upper() + lemma[1:]
        elif pos in ("verb", "adjective"):
            lemma = lemma[:1].lower() + lemma[1:]
        return lemma, article

    def _upsert_word(self, w: dict, source: str | None, now: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM words WHERE lemma = ? AND pos = ?", (w["lemma"], w["pos"])).fetchone()
        if row is None:
            word_id = self.conn.execute(
                """INSERT INTO words (lemma, pos, article, gloss, example, source, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (w["lemma"], w["pos"], w["article"], w["gloss"], w.get("example"), source, now)).lastrowid
            return self._word(word_id)
        if w["article"] and not row["article"]:
            self.conn.execute("UPDATE words SET article = ? WHERE id = ?", (w["article"], row["id"]))
        return self._word(row["id"])

    def _word(self, word_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()

    def _find_word(self, lemma: str) -> sqlite3.Row:
        lemma = " ".join(lemma.split())
        first, _, rest = lemma.partition(" ")
        if first.lower() in ARTICLES and rest:
            lemma = rest
        rows = self.conn.execute("SELECT * FROM words WHERE lemma = ?", (lemma,)).fetchall() or \
            self.conn.execute("SELECT * FROM words WHERE lower(lemma) = lower(?)", (lemma,)).fetchall()
        if not rows:
            raise TutorError(f"{lemma!r} isn't in the word list yet: send pos and gloss (and article for a noun) to "
                             "add it, in its dictionary form.")
        if len(rows) > 1:
            raise TutorError(f"{lemma!r} is in the word list as {', '.join(r['pos'] for r in rows)}: send pos too.")
        return rows[0]

    @staticmethod
    def _display(word: sqlite3.Row) -> str:
        return f"{word['article']} {word['lemma']}" if word["article"] else word["lemma"]

    def _rank(self, lemma: str) -> int | None:
        """The lemma's place in the frequency list, or None for chunks and words it doesn't have."""
        key = lemma.lower()
        key = key[5:] if key.startswith("sich ") else key
        if " " in key:
            return None
        row = self.conn.execute("SELECT rank FROM frequency WHERE word = ?", (key,)).fetchone()
        return row["rank"] if row else None

    @staticmethod
    def _options(rng: random.Random, gloss: str, pos: str, pool: list[tuple[str, str]]) -> list[str]:
        """The right gloss among up to three others, same part of speech first, then 'weiß nicht'."""
        others = [(p, g) for p, g in pool if g.lower() != gloss.lower()]
        same = list(dict.fromkeys(g for p, g in others if p == pos))
        rest = [g for g in dict.fromkeys(g for p, g in others if p != pos) if g not in same]
        rng.shuffle(same)
        rng.shuffle(rest)
        distractors = (same + rest)[:3]
        if len(distractors) < 2:
            raise TutorError("Not enough other words to build answer choices: send at least four words.")
        options = distractors + [gloss]
        rng.shuffle(options)
        return options + [DONT_KNOW]

    def _add_evidence(self, word_id: int, session_id: int, at: str, facet: str, correct: bool, primed: bool,
                      scorer: str, answer: str | None) -> None:
        self.conn.execute(
            """INSERT INTO word_evidence (word_id, session_id, at, facet, correct, primed, scorer, answer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (word_id, session_id, at, facet, int(bool(correct)), int(bool(primed)), scorer, answer))

    def _seen_words(self, session_id: int) -> set[int]:
        """Words shown in this session: glossed, previewed, checked and answered, or corrected after a miss."""
        seen: set[int] = set()
        for r in self.conn.execute("SELECT items, answered_at FROM word_checks WHERE session_id = ?", (session_id,)):
            items = json.loads(r["items"])
            seen.update(items["glossed"])
            if r["answered_at"]:
                seen.update(q["word_id"] for q in items["questions"])
        seen.update(r[0] for r in self.conn.execute(
            "SELECT word_id FROM word_evidence WHERE session_id = ? AND correct = 0", (session_id,)))
        return seen

    def _word_status(self, word: sqlite3.Row) -> dict:
        """Each facet: untested, unknown (the latest evidence is a miss), learning, or known (right on
        KNOWN_DAYS different days since the last miss). Primed evidence doesn't count."""
        rows = self.conn.execute(
            "SELECT facet, correct, at FROM word_evidence WHERE word_id = ? AND primed = 0 ORDER BY at, id",
            (word["id"],)).fetchall()
        status = {}
        for facet in WORD_FACETS:
            if facet == "gender" and word["pos"] != "noun":
                continue
            facet_rows = [r for r in rows if r["facet"] == facet]
            last_miss = max((i for i, r in enumerate(facet_rows) if not r["correct"]), default=-1)
            if not facet_rows:
                status[facet] = "untested"
            elif last_miss == len(facet_rows) - 1:
                status[facet] = "unknown"
            else:
                days = {r["at"][:10] for r in facet_rows[last_miss + 1:]}
                status[facet] = "known" if len(days) >= KNOWN_DAYS else "learning"
        return status

    def _queue_for_anki(self, word: sqlite3.Row, needed: bool, now: str) -> bool:
        """A missed word earns a card if the task needed it, it is common, or it was missed on two days."""
        if self._word(word["id"])["anki_queued_at"]:
            return False
        rank = self._rank(word["lemma"])
        miss_days = self.conn.execute(
            """SELECT COUNT(DISTINCT substr(at, 1, 10)) FROM word_evidence
               WHERE word_id = ? AND correct = 0 AND facet IN ('meaning', 'gender')""", (word["id"],)).fetchone()[0]
        if needed or (rank is not None and rank <= COMMON_RANK) or miss_days >= 2:
            self.conn.execute("UPDATE words SET anki_queued_at = ? WHERE id = ?", (now, word["id"]))
            return True
        return False

    def _word_overview(self) -> dict:
        meaning = dict.fromkeys(("known", "learning", "unknown", "untested"), 0)
        nouns = gender_known = 0
        for w in self.conn.execute("SELECT * FROM words").fetchall():
            status = self._word_status(w)
            meaning[status["meaning"]] += 1
            if w["pos"] == "noun":
                nouns += 1
                gender_known += status["gender"] == "known"
        recent = self.conn.execute(
            """SELECT w.*, GROUP_CONCAT(DISTINCT e.facet) AS facets, MAX(e.at) AS last FROM word_evidence e
               JOIN words w ON w.id = e.word_id WHERE e.correct = 0 GROUP BY w.id ORDER BY last DESC LIMIT 10""")
        return {"meaning": meaning, "nouns_with_gender_known": f"{gender_known} of {nouns}",
                "recent_misses": [f"{self._display(r)} = {r['gloss']} ({r['facets']})" for r in recent],
                "anki_queue": self.conn.execute(
                    "SELECT COUNT(*) FROM words WHERE anki_queued_at IS NOT NULL AND anki_exported_at IS NULL"
                ).fetchone()[0]}

    def _last_speaking_phrases(self, session_id: int) -> list[str]:
        """Expressions given in the last earlier session's speaking plan, to need again in a new situation."""
        rows = self.conn.execute(
            """SELECT items FROM word_checks WHERE purpose = 'preview' AND source = 'speaking' AND session_id = (
                   SELECT MAX(session_id) FROM word_checks
                   WHERE purpose = 'preview' AND source = 'speaking' AND session_id < ?)""", (session_id,))
        ids = dict.fromkeys(i for r in rows for i in json.loads(r["items"])["glossed"])
        return [self._display(self._word(i)) for i in ids]

    def _session_words(self, session_id: int) -> dict:
        rows = self.conn.execute(
            """SELECT w.*, e.facet, e.correct, e.scorer FROM word_evidence e JOIN words w ON w.id = e.word_id
               WHERE e.session_id = ? ORDER BY e.id""", (session_id,)).fetchall()
        checked = [r for r in rows if r["scorer"] == "code:word_check"]
        return {"checked": f"{sum(r['correct'] for r in checked)} of {len(checked)} right" if checked else None,
                "missed": list(dict.fromkeys(f"{self._display(r)} ({r['facet']})" for r in rows if not r["correct"]))}

    def _evidence(self, construct_id: str, context: str) -> list[sqlite3.Row]:
        unprimed = "AND primed = 0" if context == "free" else ""
        return self.conn.execute(
            f"""SELECT correct, repair, session_id, scorer FROM attempts
                WHERE construct_id = ? AND context = ? AND hint_level = 0 AND certain = 1 {unprimed}
                ORDER BY at DESC, id DESC LIMIT ?""", (construct_id, context, WINDOW)).fetchall()

    @staticmethod
    def _meets_bar(rows: list[sqlite3.Row], allow_self_repair: bool) -> bool:
        if len(rows) < MIN_EVIDENCE or len({r["session_id"] for r in rows}) < MIN_SESSIONS:
            return False
        right = sum(1 for r in rows if r["correct"] or (allow_self_repair and r["repair"] == "self"))
        return 100 * right >= ACHIEVED_PCT * len(rows)

    def _update_level(self, construct_id: str) -> tuple[int, str | None]:
        """The highest rung with enough recent evidence.

        A self-repair shows the learner knows the form, so it counts for rungs 1-3. It does not
        count for free use, which is about using the form without stopping.
        """
        level, note = 0, None
        for context, rank in CONTEXTS.items():
            if context != "free" and self._meets_bar(self._evidence(construct_id, context), allow_self_repair=True):
                level = rank
        free = self._evidence(construct_id, "free")
        if self._meets_bar(free, allow_self_repair=False):
            if any(r["scorer"] != "tutor" and r["correct"] for r in free):
                level = 5 if self._retained(construct_id) else 4
            else:
                level = max(level, 3)
                note = ("Free use meets the bar on tutor scoring only; a human or a checker must confirm it "
                        "before level 4.")
        next_review = (self.today() + timedelta(days=REVIEW_AFTER_DAYS[level])).isoformat()
        self.conn.execute("UPDATE constructs SET level = ?, level_note = ?, next_review = ? WHERE id = ?",
                          (level, note, next_review, construct_id))
        return level, note

    def _retained(self, construct_id: str) -> bool:
        """Level 5: unprimed free use, judged by anyone but the tutor model, 7+ days after the first."""
        first = self.conn.execute(
            """SELECT MIN(at) FROM attempts WHERE construct_id = ? AND context = 'free' AND primed = 0
               AND correct = 1 AND certain = 1 AND hint_level = 0""", (construct_id,)).fetchone()[0]
        if not first:
            return False
        cutoff = (datetime.fromisoformat(first) + timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
        return self.conn.execute(
            """SELECT 1 FROM attempts WHERE construct_id = ? AND context = 'free' AND primed = 0 AND correct = 1
               AND certain = 1 AND hint_level = 0 AND scorer != 'tutor' AND at >= ? LIMIT 1""",
            (construct_id, cutoff)).fetchone() is not None

    def _signals(self, session: sqlite3.Row) -> list[str]:
        signals = []
        planned = session["minutes_planned"]
        limit = planned + BREAK_MIN * (planned // BREAK_AFTER_MIN)
        if self.clock() - datetime.fromisoformat(session["started_at"]) >= timedelta(minutes=limit):
            signals.append("The planned time and its breaks are used up: finish this item and close the session.")
        hinted = [r["hint_level"] > 0 for r in self.conn.execute(
            "SELECT hint_level FROM attempts WHERE session_id = ? AND at >= ? ORDER BY at, id",
            (session["id"], session["block_started_at"]))]
        if len(hinted) >= 2 * HINT_WINDOW:
            rise = sum(hinted[-HINT_WINDOW:]) - sum(hinted[:HINT_WINDOW])
            if rise * 100 >= HINT_RISE_PCT * HINT_WINDOW:
                signals.append("Hint use is rising in this block. Ask the learner to rate their effort from 1 to "
                               "5; at 4 or 5, take a break or switch to easy, familiar input.")
        return signals

    def _plan(self, minutes: int) -> list[dict]:
        """The day template, scaled to `minutes` and filled from the learner model."""
        focus = self.focus()
        due = [r["id"] for r in self.conn.execute(
            "SELECT id FROM constructs WHERE level > 0 AND next_review <= ? ORDER BY level, id",
            (self.today().isoformat(),))]
        unit = self.conn.execute(
            """SELECT id, title, data FROM units WHERE status != 'done'
               ORDER BY status = 'in_progress' DESC, position LIMIT 1""").fetchone()
        task = None
        if minutes < SHORT_DAY_MIN:
            review = (minutes - 5) // 2
            template = [("review", review), ("speaking", minutes - 5 - review), ("close", 5)]
        else:
            template = [(kind, round(base * minutes / 180)) for kind, base in catalog.DAY_180]
            template, task = self._fit_writing(template)
        plan = [self._block(kind, max(block_minutes, 5), focus, due, unit, task)
                for kind, block_minutes in template if kind == "close" or block_minutes >= 5]
        return self._rebalance(plan)

    def _fit_writing(self, template: list[tuple[str, int]]) -> tuple[list[tuple[str, int]], dict | None]:
        """On a writing day, put the exam's writing task in place of the focus block, if it fits.

        The tasks take turns; when today's doesn't fit the day, a shorter one is tried.
        """
        day = (self.progress() or {}).get("day")
        if not day or day % 7 not in WRITING_DAYS:
            return template, None
        tasks = self.exam()["writing"]
        turn = (day // 7) * len(WRITING_DAYS) + WRITING_DAYS.index(day % 7)
        today = tasks[turn % len(tasks)]
        minutes = dict(template)
        spare = {k: minutes[k] if k == "focus" else minutes[k] // 2 for k in WRITING_DONORS if k in minutes}
        for task in [today] + sorted((t for t in tasks if t is not today), key=lambda t: t["minutes"]):
            need = task["minutes"] + WRITING_REVIEW_MIN
            if need <= sum(spare.values()):
                break
        else:
            return template, None
        taken, left = {}, need
        for kind in WRITING_DONORS:
            taken[kind] = min(spare.get(kind, 0), left)
            left -= taken[kind]
        fitted = []
        for kind, block_minutes in template:
            if kind == "focus":
                fitted.append(("writing", need))
                if block_minutes - taken["focus"] >= 5:
                    fitted.append(("focus", block_minutes - taken["focus"]))
            else:
                fitted.append((kind, block_minutes - taken.get(kind, 0)))
        return fitted, task

    def _block(self, kind: str, minutes: int, focus: dict, due: list[str], unit: sqlite3.Row | None,
               task: dict | None = None) -> dict:
        data = json.loads(unit["data"]) if unit else {}
        exam = self.exam()
        day = (self.progress() or {}).get("day") or 0
        word_order, form, listening = focus["word_order"], focus["form"], focus["listening"]
        block: dict = {"type": kind, "minutes": minutes}
        word_check = (f"First a word check: choose up to {CANDIDATES_MAX} words the page's texts and instructions "
                      "need, instruction words such as Aussage included, and call word_check. It tests the ones "
                      "worth testing and glosses the rest; answer_word_check says what to teach. ")
        if kind == "review":
            block.update(title="Reviews", constructs=due, instructions=(
                "Anki first, ideally in the morning, since sleep after learning helps new words stick: words with "
                "their gender, and spoken chunks answered aloud within about two seconds. "
                + ("Then two recall prompts for each due construct, from the learner's own sentences. " if due else "")
                + "Then the retests in `retests`: the learner's own errors coming back. For each, give a fresh "
                  "prompt that needs the same rule with the same trigger, with familiar words, in the channel it "
                  "went wrong in (spoken: the learner says it), with no hint and no rule talk. Show their old "
                  "sentence only after they answer. Log each with log_attempt, context controlled, retest set to "
                  "its number."))
        elif kind == "lesson" and unit:
            block.update(title=f"Book: {unit['title']}", unit=unit["id"], constructs=data.get("targets", []),
                         instructions=f"Call get_lesson('{unit['id']}'). " + word_check + "Then a 3-item pretest "
                                      "without help, the rule with one clear model, controlled practice from the "
                                      "book's exercises, then a task that needs it. Log comprehension items "
                                      "(matching, true or false) as reading_detail. Mark answers only against keys "
                                      "from get_lesson, and say so when a key is missing or only derived.")
        elif kind == "lesson":
            block.update(title="Book", constructs=[],
                         instructions="No unit is loaded. Ask which page to work on and teach it from a photo. "
                                      + word_check + "With no key from get_lesson, say that your answers are your "
                                      "own, and log any answer you aren't sure of with certain=false.")
        elif kind == "speaking":
            pair = (word_order or {}).get("paired_with")
            targets = list(dict.fromkeys([t["id"] for t in (focus["spoken"], word_order, pair) if t]
                                         + focus["probes"]))
            theme = data.get("theme")
            exam_part = None
            if day % 7 == EXAM_SPEAKING_DAY and exam["speaking"]:
                exam_part = exam["speaking"][(day // 7) % len(exam["speaking"])]
                block["exam_part"] = exam_part
            block.update(title="Speaking", constructs=targets, instructions=(
                (f"Exam format today ({exam['name']}): the task is this part of the speaking test: {exam_part}. "
                 + (exam["speaking_rule"] + " " if exam["speaking_rule"] else "")
                 + "You play the partner or the examiner; the three rounds stay. " if exam_part else "") +
                f"One everyday task{' on: ' + theme if theme else ''}, done three times: a role-play with an "
                "unexpected turn, or a talk on familiar content. If `reuse_phrases` lists expressions from the last "
                "session, set today's task in a new situation that needs them. "
                "Round 1: a minute of planning, in which you may give up to 5 expressions the task needs, recorded "
                "first with show_words (source 'speaking'). Then the learner does the task without interruption. "
                "Create situations that need these constructs without naming them. In a discussion, let the "
                "learner's first turn run (about 90 seconds), then ask one follow-up at a time from what the "
                "exam rewards: a reason (Warum denkst du das?), the other side (Was würde jemand dagegen sagen? "
                "Und was entgegnest du?), an example (Hast du ein Beispiel?), a summary (Wofür bist du am "
                "Ende?). It is conversation, not correction. Switch between du and Sie roles now and then: "
                "register is marked too. Only round 1 is free use: log "
                "each use or miss after the turn, as free, with its trigger. "
                "Revision: take at most two errors, from these constructs where possible. For each, prompt rather "
                "than recast: point at it (where does the verb go after deswegen?) and let the learner fix it, then "
                "have them say the whole sentence again. They also add three expressions they could have used. "
                "Round 2: the same task again. Round 3: the same task in less time, with a new twist, and no "
                "corrections: this round is for fluency. Log rounds 2 and 3 as controlled. "
                "The learner records round 1; afterwards they transcribe one minute of it themselves, marking "
                "pauses inside a clause with '...'. Store that transcript, uncorrected, with log_sample('speech'), "
                "log what it shows with channel 'spoken' (typed answers stay 'typed'), log words they used well "
                "without help with log_word (facet use), and log syllables per minute with log_measure('fluency'), "
                "with the number of mid-clause pauses and self-repairs in its note."))
        elif kind == "input":
            latest = self._latest_mocks()
            rotation = dict(INPUT_ROTATION)
            if len(latest) >= 2 and min(latest, key=latest.get) == "lesen":
                rotation[EXTRA_READING_DAY] = "reading"
            mode = rotation.get(day % 7, "listening") if day else "listening"
            exam_weeks = bool(day) and self.profile().get("exam_day", 150) - day < EVIDENCE_FADE_DAYS
            drill_numbers = self._construct("numbers_times")["level"] < 3
            targets = list(dict.fromkeys(
                ([listening["id"]] if listening and mode != "reading" else [])
                + (["reading_detail"] if mode == "reading" else [])
                + (["numbers_times"] if drill_numbers else [])
                + (["clarification_repair"] if mode != "reading" else [])))
            numbers = ("Start with 5 minutes of numbers, times and dates (numbers_times): read them with speak(), "
                       "changing voice and rate, and have the learner write each one down. Synthetic speech is "
                       "fine here because the content is what is drilled. " if drill_numbers else "")
            protocol = ("Before playing, say in one sentence what the recording is about (who, where, why), with no "
                        "word list, and what to listen for: one detail per pass at first, up to three with notes "
                        "later. Listen once and act on it (write the time, the name, what to bring). Never ask "
                        "while the audio plays. After each wrong answer" + (
                            "" if exam_weeks else " and about one right answer in three") + ", the learner says "
                        "which words decided it; replay that short stretch once and let them confirm or change it, "
                        "and log reason (skip this in a timed mock). For each miss, "
                        "replay just that part once, audio only: caught now is speed. Still missed, read the "
                        "transcript: clear in print is decoding, so check whether a reduction caused it (ham wa = "
                        "haben wir, aufm = auf dem), have the learner say the full form back, and log the word with "
                        "log_word (facet hearing). Unclear in print too is knowledge: log unknown words with "
                        "log_word (facet meaning). Clear in parts but lost over the whole is retention; if you "
                        "can't tell, unsure. Log each detail as listening_detail, or numbers_times for numbers, "
                        "times and dates, with that cause. Practise asking instead of guessing "
                        "(clarification_repair).")
            main = {
                "listening": ((
                    "Today, speakers the learner hasn't heard before: another podcast or channel, a regional accent "
                    "(Austrian, Swiss, Bavarian, the Ruhr) or several voices talking at once, at natural speed. Say "
                    "that more misses are expected and are the point. ") if day % 7 == UNFAMILIAR_SPEAKERS_DAY else
                    "Recorded real speech: the book's audio, or one podcast or channel with the same speakers for a "
                    "few weeks. ") + protocol + " Then listen once more while reading the transcript, and have the "
                             "learner write one 15-30 second stretch down word by word and check it against the "
                             "transcript." + LAST_LISTEN + " Use speak() only for drills where the content matters "
                             "more than the voice.",
                "exam_listening": exam["listening"] + _tip(exam, "hoeren") + " Then go through the misses: "
                                  + protocol + LAST_LISTEN,
                "reading": exam["reading"] + _tip(exam, "lesen") + " No word check first: this is exam "
                           "practice. Log each item "
                           "as reading_detail; a miss's cause is word (an unknown word blocked it), sentence (the "
                           "words were known but the sentence was misread), task (the statement or instruction "
                           "was misunderstood) or unsure. " + (
                               "These are the exam weeks: go through the wrong items only, where the learner quotes "
                               "the words that decide each one and you log reason, and keep the rest in exam "
                               "format. " if exam_weeks else
                               "Then go through every wrong item and about one right item in three: the learner "
                               "quotes the words in the text that decide it, and you log reason (supports, "
                               "contradicts, absent). A right choice with the wrong words is a guess; a wrong choice "
                               "with the right words means they found the place and misread it, so hint about that "
                               "sentence. Ask one open question about the whole text (Was will der Autor hier "
                               "sagen?), not one per item. ") + "Afterwards, run word_check on the words that blocked "
                           "the learner and others from the text they may not know, so the useful ones reach Anki.",
            }[mode]
            block.update(title={"listening": "Listening", "exam_listening": "Exam-format listening",
                                "reading": "Reading"}[mode], mode=mode, constructs=targets, instructions=numbers + main)
        elif kind == "focus":
            pair = (word_order or {}).get("paired_with")
            targets = [t for t in (word_order, pair, form) if t]
            parts = []
            if word_order:
                what = (f"{word_order['id']} and {pair['id']} as one decision, where the verb parts go, with the "
                        "same content in both patterns" if pair else word_order["id"])
                if min(t["level"] for t in (word_order, pair) if t) >= 3:
                    parts.append(f"{what}: timed, meaningful repetitions, about 3 seconds per answer, no rule talk.")
                else:
                    parts.append(f"{what}: accuracy first. The rule with one model, then untimed, meaningful "
                                 "practice; timed repetitions start only once it reaches controlled use (level 3).")
            if form:
                parts.append(f"{form['id']}: repair one of the learner's own sentences in `examples`, make two "
                             "meaningful variations, then reuse it in a short new task.")
            if parts:
                parts.append("Build each drill around the failing triggers in `triggers`, with triggers that work "
                             "as contrast.")
            block.update(title="Focus", constructs=[t["id"] for t in targets],
                         examples={t["id"]: t["examples"] for t in targets},
                         triggers={t["id"]: t["triggers"] for t in targets},
                         instructions=" ".join(parts) if parts else
                         "No focus targets yet: run a short free conversation and log what you hear.")
            if exam.get("language_elements") and day % 7 == LANGUAGE_ELEMENTS_DAY:
                block["instructions"] += (" Then exam practice: " + exam["language_elements"] + " Log each gap that "
                                          "tests a construct you track (an ending, a preposition's case, a "
                                          "connector) with log_attempt, context recognise.")
        elif kind == "writing":
            pair = (word_order or {}).get("paired_with")
            targets = [t["id"] for t in (word_order, pair, form) if t]
            points = task["points"] or 20
            block.update(title="Timed writing", exam=exam["name"], task=task["name"], constructs=targets,
                         instructions=(
                f"{task['name']} ({exam['name']}). {task['task']} Set a fresh task the way the exam does, with "
                "its situation and content points (an official practice task if the learner has one). Then "
                f"{task['minutes']} minutes of writing as in the exam: no help, no dictionary, no translator. Store "
                "the text with log_sample('writing') before any feedback. Then go through it in this order. "
                "Content first: the learner shows where each content point is covered. " + exam["writing_rule"]
                + " Then the language: log each word-order and form error with log_attempt (context free, channel "
                "typed, with its trigger) and each correct use of these constructs, work on at most two errors "
                "with prompts, and have the learner rewrite one paragraph. Last, estimate the score by the exam's "
                f"criteria ({task['criteria']}) and log it with log_measure('writing_task', score, {points}, "
                "part='schreiben', rater='model', note='<the score per criterion>')"
                + ("" if task["points"] else ", 5 points per criterion") + ". Say that your mark is an estimate."))
        elif kind == "mission":
            block.update(title="Real-world mission", constructs=[], instructions=(
                "One real conversation in German today, away from the desk: not only a purchase, but a few turns "
                "with a person (a neighbour, a shop assistant, an office, a phone call). Plan the opening sentence "
                "and two follow-up questions now. If the other person switches to English, the learner asks to "
                "stay in German: Können wir bitte auf Deutsch weitermachen? Ich lerne gerade Deutsch. Next "
                "session, ask what happened, what was hard, and log any error they remember with its trigger."))
        else:
            block.update(title="Close", constructs=[], instructions=(
                "Log anything not yet logged. Call export_anki and give the learner the file to import, if there is "
                "one. Ask for a one-sentence teach-back of today's main point, then call close_session with the "
                "learner's effort rating from 1 to 5."))
        return block

    def _rebalance(self, plan: list[dict]) -> list[dict]:
        """Move up to 10 minutes from the strongest mock module's block to the weakest's.

        Recomputed from the template every session, so moves never accumulate.
        """
        latest = self._latest_mocks()
        if len(latest) < 2:
            return plan
        blocks = {b["type"]: b for b in plan}
        parts = self.exam()["parts"]

        def trains(part: str) -> dict | None:
            kind = parts[part]["block"] if part in parts else None
            return blocks.get(kind) or (blocks.get("focus") if kind == "writing" else None)

        give = trains(max(latest, key=latest.get))
        weakest = min(latest, key=latest.get)
        take = trains(weakest)
        # Speaking never gives minutes away: fluency is the learner's main goal.
        if give and take and give is not take and give["type"] != "speaking":
            moved = min(10, give["minutes"] - 5)
            if moved > 0:
                give["minutes"] -= moved
                take["minutes"] += moved
                take["rebalanced"] = f"+{moved} min because {weakest} is the weakest mock module"
        return plan

    def _latest_mocks(self) -> dict[str, float]:
        """Each module's latest mock score as a fraction, from answer-key and human ratings only."""
        latest: dict[str, float] = {}
        for r in self.conn.execute(
                f"""SELECT part, score, max_score FROM measures WHERE kind = 'mock'
                    AND rater IN ({','.join('?' * len(GATE_RATERS))}) ORDER BY at, id""", GATE_RATERS):
            latest[r["part"]] = r["score"] / r["max_score"]
        return latest

    def _check_due(self, kind: str) -> bool:
        kinds = ("c_test", "onset") if kind == "c_test" else (kind,)
        last = self.conn.execute(f"SELECT MAX(at) FROM measures WHERE kind IN ({','.join('?' * len(kinds))})",
                                 kinds).fetchone()[0]
        return last is None or self.clock() - datetime.fromisoformat(last) >= timedelta(days=CHECKS_EVERY_DAYS[kind])
