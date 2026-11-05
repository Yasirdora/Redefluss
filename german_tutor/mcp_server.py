"""MCP adapter: exposes the engine to AI apps over stdio (JSON-RPC 2.0, one message per line).

Written against the MCP specification without the SDK, so it runs on the stock macOS
Python 3.9. The tools map one-to-one onto Engine methods, so moving to the official
`mcp` package later (it needs Python 3.10+) only replaces this file.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import traceback

from . import __version__, catalog, exams
from .engine import (ARTICLES, CANDIDATES_MAX, CHANNELS, CONTEXTS, REASONS, REPAIRS, SAMPLE_KINDS, WORD_FACETS, WORD_POS,
                     Engine, TutorError)

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """German tutor engine for one learner. You teach; this server keeps score.

First run: if learner_summary contains onboarding, there is no learner yet. Interview them with the
questions under ask, one or two at a time, in a language they are comfortable with, and save the
answers with save_profile before any lesson; ask the rest over the first sessions (still_to_ask), one
when it fits. save_profile says which exam format their answers point to: confirm it with them. Say
that their record stays in a file on this computer, while what they say in lessons goes to this AI
app's provider. Use their first languages to expect typical errors: articles and
gender when the first language has none, dropped subject pronouns, verb position.

Workflow: call learner_summary first. Then start_session(minutes) and, for each block, next_activity.
Log every answer you judge with log_attempt, one call per construct: a sentence with two different
errors is two calls, and a correct use of a construct is logged too. End with close_session, passing
the number of answers you judged; it refuses to close while any are missing.

Logging honestly: correct means right the first time, with no help. A form the learner fixed alone
is correct=false with repair='self'; fixed after your prompt, repair='prompted'. Set channel to what
actually happened: typed, spoken (a recording or its transcript), or listening. When you are not
sure the learner got it right (a typo or a slip of the tongue, say), send certain=false and say so.
Your own scoring never awards free use (level 4) on its own; a human or a checker has to confirm it.
An answer is free only if the target form did not appear in your own turns just before it (a model,
the question, the role-play setup); otherwise log it as controlled. Verdicts are final: if the learner
disputes one, check it, and log a correction as a new answer rather than changing your mind to please.
An error that fits no construct goes in as slip with the sentence; close_session lists them for review.

Mistakes: with every word-order or form answer, send trigger, the word that set the rule off: the
fronted word for verb-second (deswegen), the conjunction for verb-last (weil), the preposition for
case (mit), the noun for an article or ending, the verb for a verb form. The engine counts errors by
construct and trigger as patterns and brings them back as retests in the review block; log each with
retest set to its number. When a case or adjective ending is wrong, ask the noun's gender first; if
that is wrong, log it with log_word and work on the gender, not the ending. A listening or reading
miss needs its cause; next_activity explains them.

Words: a word the learner doesn't know is not a slip. Before a book lesson, run word_check on the
words the page needs, show its items exactly as returned, send the learner's letters to
answer_word_check, then teach and gloss as it says. No word list before listening or exam-format
tasks: name the topic, and collect unknown words afterwards. Log what you see with log_word: a word
the learner asked about or misread (meaning, false), a word missed in audio but clear in print
(hearing, false), a wrong article (gender, false), a word used well without help (use, true). You
can't record that a meaning is known. Record words you give in a planning step with show_words
first. With a noun lexicon loaded, gender_drill has the learner say article, noun and plural, and
answer_gender_drill scores them against the lexicon and logs them. At the close, call export_anki.

Teaching rules: the learner always attempts first. Give hints in steps (category, then location,
then a partial answer) and log the hint_level you used. Correct with prompts, not by saying the right
form: point at the error and let the learner fix it, then have them say the whole sentence again.
Only when the prompt fails, or the construct's level is below 2, model the form and have them use it
in a new sentence. Correct at most two targets per task, the focus targets first; log the rest
without raising them, and never correct during a fluency round.

Ask why, as a good teacher does, but sparingly. Decide whether the answer is right before you ask,
and don't let the reason change that verdict. Ask after every error, on the first unhinted success
with a construct new to the learner, and on about one right answer in four while its level is below
3; above that, only after a slip. At most one question per item and two per task. Ask for the
deciding word, not a general why (Welches Wort bestimmt hier die Form? Woran hast du das erkannt?),
never as a yes/no question or a choice of two; a question that names the rule is a hint, so log its
hint_level. Log what came back as reason: supports, contradicts, absent (none, I don't know, or the
answer said again) or unclear. A right form with no reason still counts; a right form with a reason
that contradicts it is a guess: teach the contrast and give one new item on it before the session
ends. A wrong form with a right reason means the rule is known but not yet automatic: practise, don't
explain it again. In reading and listening, the reason is the words that decide the item, and a right
choice without them is a guess. Don't accept a weak reason because the learner sounds sure, and don't
cross-examine an unclear one: say what you understood and move on; after two "ich weiß nicht", stop
asking. When the learner is confused,
don't explain at once: ask what they think and what made them think it, name the step that goes
wrong without giving the form, hint that step only, and let them finish; then have them say the rule
in one sentence. If two prompts fail, model the form and have them use it in a new sentence. Explain
rules in the profile's explain_language or as a short German formula (weil ... Verb am Ende); quotes,
discussion and fluency work stay in German. Never ask why during a fluency round or a timed mock.

In speaking tasks, create situations
that need the target constructs without naming them, do not interrupt, and log after the turn. Clear
up any misunderstanding of meaning at once. Never write texts for the learner. Never state the
learner's level yourself; report what the engine returns. Mark book exercises only against keys from
get_lesson, and say so when a key is missing or only derived. If next_activity says to take a break,
or log_attempt returns signals, pass them on and follow them.

Days: on a bad day, start_session(20) is a minimum day, and it counts. Anki reviews go first, ideally
in the morning; if the day is split, put new words later, since sleep after learning helps them stick.
On a mock-exam day, don't start a plan: log each part of the learner's exam (progress.exam.parts in
learner_summary) with log_measure, and go over its pass rule (progress.exam.pass). On a day with the human
tutor, that lesson replaces the speaking block: call human_brief and give the tutor what it returns.
The lesson is free conversation that tests what carries over, not a grammar class. Afterwards, log
what the brief asks for: the tutor's blind ratings, their audit of your corrections, any
sentence-repetition score, and the errors they noted."""


def rule_file() -> str:
    """INSTRUCTIONS as an always-on agent rule, for clients that may not pass server instructions to the
    model (Antigravity reads .agents/rules/*.md; scripts/export_rules.py writes it)."""
    return ("---\ntrigger: always_on\ndescription: German tutor workflow and teaching rules\n---\n\n"
            "<!-- Generated from german_tutor/mcp_server.py by tutor/scripts/export_rules.py. "
            "Edit INSTRUCTIONS there and re-run; don't edit this file. -->\n\n"
            "Use the german-tutor MCP tools for German lessons.\n\n" + INSTRUCTIONS + "\n")


def _tool(name: str, description: str, properties: dict | None = None, required: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required)}}


WORD = {"type": "object", "additionalProperties": False, "required": ["lemma", "pos", "gloss"], "properties": {
    "lemma": {"type": "string", "description": "Dictionary form (infinitive, nominative singular), or a whole "
                                              "chunk such as 'einen Termin verschieben'."},
    "pos": {"type": "string", "enum": list(WORD_POS)},
    "gloss": {"type": "string", "description": "English, in the sense used here."},
    "article": {"type": "string", "enum": list(ARTICLES), "description": "Required for nouns."},
    "example": {"type": "string", "description": "The sentence it appears in."},
    "needed": {"type": "boolean", "description": "The task can't be done without it. A needed word is "
                                              "tested however rare, so mark only those. Default false."}}}


TOOLS = [
    _tool("learner_summary", "Profile, day count and next exam gate, today's focus (one word-order stage, one form "
                             "target, one listening target, constructs to probe), every construct's mastery level, "
                             "the last session's summary and any session left open."),
    _tool("save_profile", "Save what the first-run interview found out about a new learner, or a later change. "
                          "Fields left out keep their value; day 1 is today unless start_date says otherwise.",
          {"name": {"type": "string", "description": "What the learner wants to be called."},
           "first_languages": {"type": "array", "items": {"type": "string"}},
           "other_languages": {"type": "string", "description": "Other languages and how well, e.g. 'English C1'."},
           "level": {"type": "string", "description": "Their own estimate, how they learned, any certificate."},
           "goal": {"type": "string"},
           "exam": {"type": "string", "description": "The exam in the learner's words, e.g. telc B2."},
           "exam_format": {"type": "string", "enum": list(exams.CHOICES),
                           "description": "The exam format once the learner has confirmed it ('none' for no "
                                          "exam). Without it, the engine works it out from exam and goal."},
           "exam_date": {"type": "string", "description": "The exam, or when they want to be ready, as "
                                                        "YYYY-MM-DD; sets exam_day."},
           "exam_day": {"type": "integer", "minimum": 1, "maximum": 1000,
                        "description": "Instead of exam_date: the day number they want to be ready by, counting "
                                       "day 1 as 1; default 150."},
           "minutes_per_day": {"type": "integer", "minimum": 15},
           "schedule": {"type": "string", "description": "When in the week they can study."},
           "worries": {"type": "string", "description": "The part of the exam or of real life that worries them most."},
           "explain_language": {"type": "string", "description": "The language for explaining grammar."},
           "book": {"type": "string", "description": "Textbook and where they are in it."},
           "human_tutor": {"type": "string", "description": "A human teacher or partner, and how often."},
           "address": {"type": "string", "enum": ["du", "Sie"]},
           "start_date": {"type": "string", "description": "Day 1 as YYYY-MM-DD; default today."},
           "notes": {"type": "string"}}),
    _tool("start_session", "Plan today's session from the learner model and open it.",
          {"minutes": {"type": "integer", "minimum": 15,
                       "description": "Planned length; 180 is a full day, 20 a minimum day."}}),
    _tool("next_activity", "The next block of the open session's plan: what to practise, for how long, and how. "
                           "Says when a break is due.",
          {"session_id": {"type": "integer"}}, ("session_id",)),
    _tool("log_attempt", "Record one judged answer for one construct. Returns the construct's mastery level and "
                         "any signals (time to stop, rising hint use).",
          {"session_id": {"type": "integer"},
           "construct": {"type": "string", "description": "Construct id from learner_summary, e.g. v2_word_order."},
           "context": {"type": "string", "enum": list(CONTEXTS),
                       "description": "recognise: chose among options; recall: produced from a cue; "
                                      "controlled: in a sentence built for practice; free: in conversation, "
                                      "writing or real-speed listening, without being set up for this construct."},
           "correct": {"type": "boolean", "description": "Right the first time, without help."},
           "channel": {"type": "string", "enum": list(CHANNELS), "description": "Default typed."},
           "hint_level": {"type": "integer", "minimum": 0, "maximum": 3,
                          "description": "0 none, 1 category, 2 location, 3 partial answer. Hinted answers "
                                         "do not count towards mastery."},
           "repair": {"type": "string", "enum": list(REPAIRS),
                      "description": "self: the learner fixed it alone; prompted: fixed after you pointed at it. "
                                     "Send correct=false with either."},
           "certain": {"type": "boolean", "description": "false when you are not sure how to judge it (a typo, a slip "
                                                         "of the tongue); the answer is kept but not scored. "
                                                         "Default true."},
           "reason": {"type": "string", "enum": list(REASONS),
                      "description": "Only when you asked for the reason. supports: it fits the answer; contradicts: "
                                     "it argues for another form; absent: none, 'I don't know', or the answer said "
                                     "again; unclear. For "
                                     "reading_detail and listening_detail, the reason is the words quoted from the "
                                     "text. A right answer with a contradicting reason, or a comprehension answer "
                                     "without the deciding words, is logged as a guess and not scored."},
           "trigger": {"type": "string", "description": "The word that set the rule off: deswegen (verb second), "
                                                        "weil (verb last), mit (case), the noun for an article or "
                                                        "ending, the verb for a verb form. Required for word-order "
                                                        "and form errors; send it with right answers too."},
           "retest": {"type": "integer", "description": "The retest number from the review block, when this answer "
                                                        "is that retest."},
           "item": {"type": "string", "description": "Exercise id, e.g. p52-Ü3-6."},
           "answer": {"type": "string"}, "expected": {"type": "string"},
           "sentence": {"type": "string", "description": "The learner's sentence containing the error."},
           "correction": {"type": "string"},
           "cause": {"type": "string", "description": "For a listening miss: speed, decoding, knowledge, retention "
                                                      "or unsure. For a reading_detail miss: word, sentence, task or "
                                                      "unsure. Otherwise an optional hypothesis."},
           "scorer": {"type": "string", "description": "tutor (default), code:<checker>, examiner or human."}},
          ("session_id", "construct", "context", "correct")),
    _tool("close_session", "Close the session. Refuses while fewer answers are logged than attempts_expected.",
          {"session_id": {"type": "integer"},
           "attempts_expected": {"type": "integer", "minimum": 0,
                                 "description": "How many answers you judged this session."},
           "fatigue": {"type": "integer", "minimum": 1, "maximum": 5,
                       "description": "The learner's effort rating, 1 easy to 5 exhausted."},
           "notes": {"type": "string", "description": "Open items for next time."}},
          ("session_id", "attempts_expected")),
    _tool("get_lesson", "A book unit by id (B1-U3) or page number (12, p52): sections, target constructs with "
                        "the learner's levels, and any answer keys with their verification status.",
          {"unit": {"type": "string"}}, ("unit",)),
    _tool("log_measure", "Record an objective score: a mock-exam module, C-test, onSET, LexTALE, fluency (syllables per "
                         "minute), pronunciation, a human rating, a sentence-repetition test (elicited_imitation) "
                         "or a human's audit of the tutor's corrections (correction_audit: agreed out of "
                         "checked). Only mocks rated by an answer key or a human count for exam gates.",
          {"kind": {"type": "string", "enum": list(catalog.MEASURE_KINDS)},
           "score": {"type": "number"}, "max_score": {"type": "number", "description": "Required for mocks."},
           "part": {"type": "string", "enum": list(exams.PARTS),
                    "description": "One of the parts of the learner's exam (learner_summary: progress.exam.parts)."},
           "rater": {"type": "string", "enum": list(catalog.RATERS), "description": "Required for mocks."},
           "sample_id": {"type": "integer", "description": "For a human_rating: the stored sample it rates."},
           "note": {"type": "string"}},
          ("kind", "score")),
    _tool("log_sample", "Store a piece of the learner's own German, uncorrected: the minute of speech they "
                        "transcribed, or a writing task. Kept for re-scoring, human rating and comparison over time.",
          {"session_id": {"type": "integer"},
           "kind": {"type": "string", "enum": list(SAMPLE_KINDS)},
           "text": {"type": "string", "description": "The learner's words exactly as said or written."},
           "audio_path": {"type": "string", "description": "Path to the recording on the learner's Mac, if any."},
           "note": {"type": "string", "description": "The task, e.g. '4/3/2 talk, round 3: my last job'."}},
          ("session_id", "kind", "text")),
    _tool("set_unit_status", "Mark a book unit new, in_progress or done.",
          {"unit": {"type": "string"}, "status": {"type": "string", "enum": ["new", "in_progress", "done"]}},
          ("unit", "status")),
    _tool("word_check", f"Before a lesson or after a timed text: send up to {CANDIDATES_MAX} candidate words. The "
                        "engine skips words already known, chooses which to test (every word the task needs, however rare, "
                        "then common ones), and returns multiple-choice items it scores itself, plus words only to gloss.",
          {"session_id": {"type": "integer"}, "words": {"type": "array", "items": WORD, "maxItems": CANDIDATES_MAX},
           "source": {"type": "string", "description": "Where the words come from, e.g. book:B1-U3."}},
          ("session_id", "words")),
    _tool("answer_word_check", "Send the learner's letters for a word check, in item order. Returns the results "
                               "and which words to teach and which to gloss.",
          {"check_id": {"type": "integer"},
           "answers": {"type": "array", "items": {"type": "string"}, "description": "One letter per item, e.g. "
                                                                                    "[\"B\", \"E\", \"A\"]."}},
          ("check_id", "answers")),
    _tool("show_words", "Record words you are about to give the learner (a planning step before speaking), so "
                        "using them today doesn't count as their own knowledge.",
          {"session_id": {"type": "integer"}, "words": {"type": "array", "items": WORD, "maxItems": CANDIDATES_MAX},
           "source": {"type": "string"}},
          ("session_id", "words")),
    _tool("log_word", "Record what the session showed about one word. Send pos and gloss (and article for a noun) "
                      "the first time a word comes up.",
          {"session_id": {"type": "integer"},
           "lemma": {"type": "string", "description": "Dictionary form."},
           "facet": {"type": "string", "enum": list(WORD_FACETS),
                     "description": "meaning: asked about or misread (only false); hearing: missed in audio but clear "
                                    "in print; gender: the article; use: used it without help."},
           "correct": {"type": "boolean"},
           "pos": {"type": "string", "enum": list(WORD_POS)}, "gloss": {"type": "string"},
           "article": {"type": "string", "enum": list(ARTICLES)},
           "answer": {"type": "string", "description": "What the learner said or wrote, e.g. 'der Tür'."},
           "example": {"type": "string"}},
          ("session_id", "lemma", "facet", "correct")),
    _tool("gender_drill", "Nouns from the learner's word list whose gender isn't known yet, for them to say with "
                          "article and plural (der Termin, die Termine). Needs the noun lexicon "
                          "(scripts/import_nouns.py).",
          {"session_id": {"type": "integer"},
           "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 8."}},
          ["session_id"]),
    _tool("answer_gender_drill", "Score a gender drill against the noun lexicon. The article is gender evidence for "
                                 "the word; the plural is logged as noun_plural with the noun as trigger.",
          {"drill_id": {"type": "integer"},
           "answers": {"type": "array", "items": {"type": "string"},
                       "description": "The learner's answers in item order, exactly as given, e.g. "
                                      "'der Termin, die Termine', 'die Firma, kein Plural' or 'weiß nicht'."},
           "channel": {"type": "string", "enum": ["typed", "spoken"]}},
          ["drill_id", "answers"]),
    _tool("export_anki", "Write today's new Anki cards to files the learner imports: words (at most 5 new a day) "
                         "and sentence cards from their own corrected errors (at most 5)."),
    _tool("human_brief", "A brief for the weekly lesson with a human tutor: the format, the mistakes to listen "
                         "for, the structures to steer towards, stored speech to rate blind, your recent "
                         "corrections to audit, and a sentence-repetition test when one is due. Give it to the "
                         "tutor as it is."),
    _tool("speak", "Read German aloud on the learner's Mac (macOS `say`). Clearer than real speech, so use it "
                   "for targeted detail drills, not as the main listening input. Don't show the text before the "
                   "learner has answered.",
          {"text": {"type": "string"},
           "voice": {"type": "string", "description": "A German macOS voice; default Anna."},
           "rate": {"type": "integer", "minimum": 80, "maximum": 300,
                    "description": "Words per minute; 160 is a clear normal pace, 130 slow."}},
          ("text",)),
]
ENGINE_TOOLS = {t["name"] for t in TOOLS} - {"speak"}


def speak(text: str, voice: str = "Anna", rate: int = 160) -> dict:
    """Play German through the Mac's speakers. Arguments go to `say` directly, never through a shell."""
    if platform.system() != "Darwin":
        raise TutorError("speak needs macOS.")
    if not text.strip() or len(text) > 2000:
        raise TutorError("text must be 1 to 2000 characters.")
    if not 80 <= rate <= 300:
        raise TutorError("rate must be 80 to 300 words per minute.")
    done = subprocess.run(["say", "-v", voice, "-r", str(rate), "--", text],
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise TutorError(f"say failed: {done.stderr.strip() or done.returncode}. "
                         "List German voices with: say -v '?' | grep de_DE")
    return {"spoken": True, "voice": voice, "rate": rate, "characters": len(text)}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def handle(engine: Engine, message: dict) -> dict | None:
    """Answer one JSON-RPC message; notifications and responses get no reply."""
    method, msg_id = message.get("method"), message.get("id")
    if method is None or msg_id is None:
        return None
    params = message.get("params") or {}
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {"protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "german-tutor", "version": __version__},
                      "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = _call_tool(engine, params.get("name"), params.get("arguments") or {})
        else:
            raise _RpcError(-32601, f"Method not found: {method}")
    except _RpcError as e:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": e.code, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _call_tool(engine: Engine, name: str, arguments: dict) -> dict:
    if name not in ENGINE_TOOLS and name != "speak":
        raise _RpcError(-32602, f"Unknown tool: {name}")
    try:
        data = speak(**arguments) if name == "speak" else getattr(engine, name)(**arguments)
    except (TutorError, TypeError) as e:
        # Tool-level errors go back to the model as results, so it can fix the call and retry.
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:  # keep the server alive; details go to stderr
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": f"Internal error: {e}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}], "isError": False}


def serve(engine: Engine, stdin=sys.stdin, stdout=sys.stdout) -> None:
    while True:
        line = stdin.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(engine, message) if isinstance(message, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Expected one JSON object"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main() -> None:
    engine = Engine()
    engine.bootstrap()
    serve(engine)


if __name__ == "__main__":
    main()
