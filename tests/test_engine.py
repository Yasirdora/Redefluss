import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from german_tutor.db import connect
from german_tutor.engine import FIRST_QUESTIONS, LATER_QUESTIONS, Engine, TutorError

BOOK = Path(__file__).resolve().parent / "fixtures" / "book"  # an invented unit, not a real textbook


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta) -> None:
        self.at += timedelta(**delta)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(datetime(2026, 11, 5, 10, 0))
        self.engine = Engine(":memory:", clock=self.clock)
        self.engine.bootstrap(BOOK)
        self.engine.set_profile({"start_date": "2026-11-05", "exam_day": 150, "exam": "Goethe-Zertifikat B2"})
        self.session = self.engine.start_session(180)["session_id"]

    def log(self, construct="v2_word_order", context="controlled", correct=True, **extra):
        self.clock.advance(seconds=30)
        extra.setdefault("trigger", "deswegen")
        return self.engine.log_attempt(self.session, construct, context, correct, **extra)

    def new_session(self, minutes=180, **advance):
        logged = self.engine.conn.execute("SELECT COUNT(*) FROM attempts WHERE session_id = ?",
                                          (self.session,)).fetchone()[0]
        self.engine.close_session(self.session, logged)
        self.clock.advance(**(advance or {"days": 1}))
        started = self.engine.start_session(minutes)
        self.session = started["session_id"]
        return started

    def level(self, construct="v2_word_order"):
        return self.engine.conn.execute("SELECT level FROM constructs WHERE id = ?", (construct,)).fetchone()[0]


class MasteryLadderTest(EngineTest):
    def test_one_session_is_not_enough_evidence(self):
        for _ in range(6):
            self.log()
        self.assertEqual(self.level(), 0)
        self.new_session()
        self.log()
        self.assertEqual(self.level(), 3)

    def test_hinted_and_uncertain_answers_do_not_count(self):
        for _ in range(3):
            self.log(hint_level=1)
            self.log(certain=False)
        self.new_session()
        for _ in range(3):
            self.log(hint_level=2)
        self.assertEqual(self.level(), 0)

    def test_self_repair_counts_for_controlled_use_but_not_free_use(self):
        for _ in range(3):
            self.log("kein_negation", "controlled")
            self.log("perfekt", "free", scorer="human")
        self.new_session()
        for _ in range(2):
            self.log("kein_negation", "controlled", correct=False, repair="self")
            self.log("perfekt", "free", correct=False, repair="self", scorer="human")
        self.assertEqual(self.level("kein_negation"), 3)  # 5 of 5 with repairs
        self.assertEqual(self.level("perfekt"), 0)  # 3 of 5 right first time

    def test_a_repaired_answer_must_be_sent_as_wrong(self):
        with self.assertRaisesRegex(TutorError, "correct=false"):
            self.log(correct=True, repair="prompted")

    def test_free_use_needs_a_judge_besides_the_tutor(self):
        for _ in range(3):
            self.log(context="free")
        self.new_session()
        result = None
        for _ in range(2):
            result = self.log(context="free")
        self.assertEqual(result["level"], 3)
        self.assertIn("human or a checker", result["note"])
        self.assertEqual(self.log(context="free", scorer="human")["level"], 4)

    def test_free_use_after_drilling_the_same_construct_is_primed(self):
        for _ in range(2):
            self.log(context="controlled")
            for _ in range(3):
                self.log(context="free", scorer="human")
            self.new_session()
        primed = self.engine.conn.execute("SELECT SUM(primed) FROM attempts WHERE context = 'free'").fetchone()[0]
        self.assertEqual(primed, 6)
        self.assertEqual(self.level(), 0)  # without priming this would be free use

    def test_level_5_needs_a_non_tutor_judge_a_week_later(self):
        for _ in range(3):
            self.log(context="free", scorer="human")
        self.new_session()
        for _ in range(2):
            self.log(context="free", scorer="human")
        self.assertEqual(self.level(), 4)
        self.new_session(days=8)
        self.log(context="free")
        self.assertEqual(self.level(), 4)  # the tutor model cannot award level 5
        self.log(context="free", scorer="examiner")
        self.assertEqual(self.level(), 5)

    def test_certain_wrong_answers_are_logged_as_errors(self):
        self.log(correct=False, sentence="manchmal ich fühle", correction="Manchmal fühle ich")
        self.log(correct=False, certain=False, sentence="ich wirede da sein")
        rows = self.engine.conn.execute("SELECT construct_id, sentence, correction FROM errors").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("v2_word_order", "manchmal ich fühle", "Manchmal fühle ich")])

    def test_unknown_values_are_refused_with_the_valid_ones(self):
        with self.assertRaisesRegex(TutorError, "v2_word_order"):
            self.log(construct="word_order")
        with self.assertRaisesRegex(TutorError, "typed, spoken, listening"):
            self.log(channel="audio")
        with self.assertRaisesRegex(TutorError, "none, self, prompted"):
            self.log(correct=False, repair="later")


class SessionTest(EngineTest):
    def test_close_refuses_until_every_answer_is_logged(self):
        self.log()
        with self.assertRaisesRegex(TutorError, "Only 1 of 3"):
            self.engine.close_session(self.session, 3)
        self.log(correct=False, repair="self", channel="spoken")
        self.log(certain=False)
        summary = self.engine.close_session(self.session, 3, fatigue=2)
        self.assertTrue(summary["closed"])
        self.assertEqual(summary["first_pass_accuracy"], "50%")
        self.assertEqual(summary["repairs"], {"self": 1, "prompted": 0})
        self.assertEqual(summary["channels"], {"typed": 2, "spoken": 1, "listening": 0})
        self.assertEqual(summary["unresolved"], 1)

    def test_cannot_start_while_a_session_is_open(self):
        with self.assertRaisesRegex(TutorError, "still open"):
            self.engine.start_session(60)
        self.engine.close_session(self.session, 0)
        self.engine.start_session(60)

    def test_logging_to_a_closed_session_fails(self):
        self.engine.close_session(self.session, 0)
        with self.assertRaisesRegex(TutorError, "closed"):
            self.log()

    def test_full_day_follows_the_template(self):
        plan = json.loads(self.engine.conn.execute("SELECT plan FROM sessions WHERE id = ?",
                                                   (self.session,)).fetchone()[0])
        self.assertEqual([(b["type"], b["minutes"]) for b in plan],
                         [("review", 20), ("lesson", 30), ("speaking", 50), ("input", 40), ("focus", 20),
                          ("mission", 15), ("close", 5)])

    def test_a_minimum_day_is_review_and_speaking(self):
        self.engine.close_session(self.session, 0)
        with self.assertRaisesRegex(TutorError, "minimum day"):
            self.engine.start_session(10)
        plan = self.engine.start_session(20)["plan"]
        self.assertEqual([(b["type"], b["minutes"]) for b in plan], [("review", 7), ("speaking", 8), ("close", 5)])

    def test_speaking_reuses_the_last_sessions_phrases(self):
        speaking = next(b for b in (self.engine.next_activity(self.session) for _ in range(3))
                        if b["type"] == "speaking")
        self.assertEqual(speaking["reuse_phrases"], [])
        self.engine.show_words(self.session, [
            {"lemma": "Termin", "pos": "noun", "article": "der", "gloss": "appointment"},
            {"lemma": "einen Termin verschieben", "pos": "chunk", "gloss": "to move an appointment"}],
            source="speaking")
        self.engine.show_words(self.session, [{"lemma": "Rechnung", "pos": "noun", "article": "die",
                                               "gloss": "bill"}], source="lesson")
        self.new_session()
        speaking = next(b for b in (self.engine.next_activity(self.session) for _ in range(3))
                        if b["type"] == "speaking")
        self.assertEqual(speaking["reuse_phrases"], ["der Termin", "einen Termin verschieben"])
        self.assertIn("reuse_phrases", speaking["instructions"])

    def test_next_activity_walks_the_plan_and_starts_the_unit(self):
        steps = [self.engine.next_activity(self.session) for _ in range(8)]
        self.assertEqual([s.get("type") for s in steps[:7]],
                         ["review", "lesson", "speaking", "input", "focus", "mission", "close"])
        self.assertTrue(steps[7]["done"])
        status = self.engine.conn.execute("SELECT status FROM units WHERE id = 'B1-U3'").fetchone()[0]
        self.assertEqual(status, "in_progress")

    def test_a_break_is_due_after_50_minutes(self):
        self.assertNotIn("break_first", self.engine.next_activity(self.session))
        self.clock.advance(minutes=51)
        self.assertIn("break_first", self.engine.next_activity(self.session))
        self.clock.advance(minutes=20)
        self.assertNotIn("break_first", self.engine.next_activity(self.session))

    def test_stop_signal_when_planned_time_and_breaks_are_used_up(self):
        self.clock.advance(minutes=180)
        self.assertNotIn("signals", self.log())  # three 5-minute breaks are still due
        self.clock.advance(minutes=15)
        self.assertIn("used up", self.log()["signals"][0])

    def test_rising_hint_use_asks_for_an_effort_rating(self):
        self.engine.next_activity(self.session)
        for _ in range(8):
            self.log()
        result = None
        for hint in (0, 0, 0, 0, 0, 0, 1, 1):
            result = self.log(hint_level=hint)
        self.assertIn("Ask the learner to rate their effort", result["signals"][0])


class FocusTest(EngineTest):
    def blocks_of_next_session(self, **advance):
        self.new_session(**advance)
        blocks = {}
        for _ in range(7):
            block = self.engine.next_activity(self.session)
            blocks[block["type"]] = block
        return blocks

    def test_word_order_follows_the_acquisition_order(self):
        self.log("verb_final_subclause", correct=False)
        self.log("v2_word_order", correct=False)
        focus = self.engine.focus()
        self.assertEqual(focus["word_order"]["id"], "v2_word_order")
        self.assertEqual(focus["probes"][0], "verb_bracket")  # earlier stage, not yet failed

    def test_the_next_stage_is_paired_as_soon_as_both_fail(self):
        self.log("verb_final_subclause", "free", correct=False)
        self.log("v2_word_order", "free", correct=False)
        focus = self.engine.focus()
        self.assertEqual(focus["word_order"]["paired_with"]["id"], "verb_final_subclause")
        self.assertEqual(focus["waiting"], [])
        blocks = self.blocks_of_next_session()
        self.assertEqual(blocks["focus"]["constructs"][:2], ["v2_word_order", "verb_final_subclause"])
        self.assertIn("as one decision", blocks["focus"]["instructions"])
        self.assertIn("accuracy first", blocks["focus"]["instructions"])

    def test_an_earlier_stage_that_fails_comes_first(self):
        self.log("v2_word_order", correct=False)
        self.log("verb_bracket", correct=False)
        focus = self.engine.focus()
        self.assertEqual(focus["word_order"]["id"], "verb_bracket")
        self.assertEqual(focus["word_order"]["paired_with"]["id"], "v2_word_order")

    def test_a_stage_two_steps_on_waits(self):
        self.log("verb_final_subclause", correct=False)
        self.log("verb_bracket", correct=False)
        focus = self.engine.focus()
        self.assertNotIn("paired_with", focus["word_order"])
        self.assertEqual(focus["waiting"], [{"id": "verb_final_subclause", "waits_for": "verb_bracket"}])

    def test_a_form_target_needs_three_answers(self):
        self.log("kein_negation", correct=False)
        focus = self.engine.focus()
        self.assertIsNone(focus["form"])
        self.assertIn("kein_negation", focus["probes"])
        self.log("kein_negation")
        self.log("kein_negation")
        self.assertEqual(self.engine.focus()["form"]["id"], "kein_negation")

    def test_failure_rate_is_smoothed(self):
        # Raw rates would pick kein (3 of 3) over verb_conjugation (9 of 10); smoothed, 4/5 < 10/12.
        for _ in range(3):
            self.log("kein_negation", correct=False)
        for correct in [False] * 9 + [True]:
            self.log("verb_conjugation", correct=correct)
        self.assertEqual(self.engine.focus()["form"]["id"], "verb_conjugation")

    def test_unprimed_free_use_decides_the_rate_when_there_is_enough(self):
        for _ in range(3):
            self.log("kein_negation", "free")
        for _ in range(5):
            self.log("kein_negation", "controlled", correct=False)
            self.log("kein_negation", "free")  # primed by the drill, so not counted as free use
        self.assertEqual(self.engine.focus()["form"]["failed"], "0 of 3 (free use, last 14 days)")

    def test_hinted_answers_count_as_failures(self):
        for _ in range(3):
            self.log("kein_negation", hint_level=1)
        self.assertEqual(self.engine.focus()["form"]["id"], "kein_negation")

    def test_slips_are_never_targeted(self):
        for _ in range(5):
            self.log("slip", correct=False)
        focus = self.engine.focus()
        self.assertIsNone(focus["form"])
        self.assertNotIn("slip", focus["probes"])

    def test_errors_older_than_14_days_drop_out(self):
        self.log(correct=False)
        self.clock.advance(days=15)
        self.assertIsNone(self.engine.focus()["word_order"])

    def test_focus_reaches_the_right_blocks(self):
        for _ in range(3):
            self.log("listening_detail", "free", correct=False, channel="listening", cause="decoding")
            self.log("kein_negation", "free", correct=False, sentence="ich verstehe nicht Deutsch")
        self.log(correct=False, sentence="deswegen ich habe")
        blocks = self.blocks_of_next_session(days=2)  # day 3: a listening day
        self.assertEqual(blocks["input"]["mode"], "listening")
        self.assertEqual(blocks["input"]["constructs"], ["listening_detail", "numbers_times", "clarification_repair"])
        self.assertEqual(blocks["focus"]["constructs"], ["v2_word_order", "kein_negation"])
        self.assertEqual(blocks["focus"]["examples"]["v2_word_order"][0]["sentence"], "deswegen ich habe")
        self.assertEqual(blocks["speaking"]["constructs"][:2], ["v2_word_order", "verb_bracket"])

    def test_learner_summary_shows_focus_and_notes(self):
        for _ in range(3):
            self.log(context="free")
        self.new_session()
        for _ in range(2):
            self.log(context="free")
        summary = self.engine.learner_summary()
        self.assertEqual(summary["focus"]["word_order"], None)
        v2 = next(c for c in summary["constructs"] if c["id"] == "v2_word_order")
        self.assertEqual((v2["level"], v2["first_pass_right"]), (3, {"free": "5/5"}))
        self.assertIn("note", v2)


class BlockTest(EngineTest):
    def input_block(self, day):
        self.clock.at = datetime(2026, 11, 5, 10, 0) + timedelta(days=day - 1)
        self.engine.close_session(self.session, 0)
        self.session = self.engine.start_session(180)["session_id"]
        return next(b for b in (self.engine.next_activity(self.session) for _ in range(4)) if b["type"] == "input")

    def test_input_rotates_through_listening_reading_and_exam_format(self):
        self.assertEqual([self.input_block(day)["mode"] for day in (1, 2, 5, 6)],
                         ["listening", "reading", "listening", "exam_listening"])

    def test_reading_gets_a_second_day_while_it_is_the_weakest_module(self):
        self.engine.log_measure("mock", 50, 100, part="lesen", rater="answer_key")
        self.assertEqual(self.input_block(5)["mode"], "listening")  # one module says nothing about weakest
        self.engine.log_measure("mock", 70, 100, part="hören", rater="answer_key")
        self.assertEqual(self.input_block(5)["mode"], "reading")
        self.engine.log_measure("mock", 40, 100, part="hören", rater="answer_key")
        self.assertEqual(self.input_block(5)["mode"], "listening")

    def test_numbers_drill_runs_until_controlled_use(self):
        self.assertIn("numbers_times", self.input_block(1)["instructions"].split(".")[0])
        self.engine.conn.execute("UPDATE constructs SET level = 3 WHERE id = 'numbers_times'")
        self.assertNotIn("numbers_times", self.input_block(3)["constructs"])

    def test_close_lists_unelicited_targets_and_slips(self):
        self.log("kein_negation", correct=False)
        self.log("kein_negation", correct=False)
        self.log("kein_negation", correct=False)
        self.engine.close_session(self.session, 3)
        self.session = self.engine.start_session(180)["session_id"]
        for _ in range(5):
            self.engine.next_activity(self.session)  # up to and including focus
        self.log("slip", correct=False, sentence="ich wirede da sein")
        summary = self.engine.close_session(self.session, 1)
        self.assertIn("kein_negation", summary["not_elicited"])
        self.assertEqual(summary["slips"], ["ich wirede da sein"])

    def test_samples_are_kept_and_can_be_rated(self):
        sample = self.engine.log_sample(self.session, "speech", "Ich habe gestern mit meinem Chef gesprochen.")
        self.assertEqual(sample["words"], 7)
        self.engine.log_measure("human_rating", 4, 5, rater="human", sample_id=sample["sample_id"])
        with self.assertRaisesRegex(TutorError, "No sample 99"):
            self.engine.log_measure("human_rating", 4, 5, rater="human", sample_id=99)
        with self.assertRaisesRegex(TutorError, "speech, writing"):
            self.engine.log_sample(self.session, "audio", "Hallo")
        self.assertEqual(self.engine.close_session(self.session, 0)["samples"], 1)


class RebalanceTest(EngineTest):
    def setUp(self):
        super().setUp()
        self.engine.close_session(self.session, 0)

    def plan(self):
        return {b["type"]: b["minutes"] for b in self.engine.start_session(180)["plan"]}

    def test_mock_scores_move_minutes_to_the_weakest_module(self):
        self.engine.log_measure("mock", 80, 100, part="Lesen", rater="answer_key")
        self.engine.log_measure("mock", 45, 100, part="Schreiben", rater="human")
        plan = self.plan()
        self.assertEqual((plan["input"], plan["focus"]), (30, 30))

    def test_model_rated_mocks_do_not_move_minutes(self):
        self.engine.log_measure("mock", 80, 100, part="lesen", rater="model")
        self.engine.log_measure("mock", 45, 100, part="schreiben", rater="model")
        plan = self.plan()
        self.assertEqual((plan["input"], plan["focus"]), (40, 20))

    def test_speaking_never_gives_minutes_away(self):
        self.engine.log_measure("mock", 90, 100, part="sprechen", rater="human")
        self.engine.log_measure("mock", 50, 100, part="hören", rater="answer_key")
        plan = self.plan()
        self.assertEqual((plan["speaking"], plan["input"]), (50, 40))

    def test_short_session_keeps_the_close_block(self):
        plan = self.engine.start_session(30)["plan"]
        self.assertEqual(plan[-1]["type"], "close")


class LessonAndProgressTest(EngineTest):
    def test_get_lesson_by_id_or_page(self):
        by_id = self.engine.get_lesson("B1-U3")
        self.assertEqual(self.engine.get_lesson("p53")["unit"], "B1-U3")
        self.assertEqual(self.engine.get_lesson("12")["unit"], "B1-U3")
        self.assertEqual(by_id["keys"]["p53-Ü3"]["answers"][5], "mir")
        self.assertIn("refl_case", [t["id"] for t in by_id["targets"]])

    def test_unknown_page_says_what_is_loaded(self):
        with self.assertRaisesRegex(TutorError, "B1-U3"):
            self.engine.get_lesson("p99")

    def test_progress_counts_days_and_finds_the_next_gate(self):
        self.clock.advance(days=89)
        progress = self.engine.progress()
        self.assertEqual(progress["day"], 90)
        self.assertEqual((progress["next_gate"]["day"], progress["next_gate"]["in_days"]), (90, 0))

    def test_measures_are_validated(self):
        with self.assertRaisesRegex(TutorError, "rater"):
            self.engine.log_measure("mock", 50, 100, part="lesen")
        with self.assertRaises(TutorError):
            self.engine.log_measure("mock", 120, 100, part="lesen", rater="human")
        with self.assertRaisesRegex(TutorError, "kind must be one of"):
            self.engine.log_measure("vocab_quiz", 70, 100)
        self.engine.log_measure("fluency", 142, note="syllables per minute, 4/3/2 talk, round 3")
        self.engine.log_measure("lextale", 71.25, 100, note="LexTALE-DE, week-0 baseline")
        self.engine.log_measure("elicited_imitation", 15, 24, rater="human", note="form A")
        self.engine.log_measure("correction_audit", 9, 10, rater="human")

    def test_checks_fall_due_again(self):
        self.assertEqual(self.engine.progress()["checks_due"], ["c_test", "elicited_imitation"])
        self.engine.log_measure("onset", 60, 100)  # onSET is the alternative to the C-test
        self.engine.log_measure("elicited_imitation", 15, 24, rater="human")
        self.assertEqual(self.engine.progress()["checks_due"], [])
        self.clock.advance(days=14)
        self.assertEqual(self.engine.progress()["checks_due"], ["elicited_imitation"])
        self.clock.advance(days=16)
        self.assertEqual(self.engine.progress()["checks_due"], ["c_test", "elicited_imitation"])


class HumanBriefTest(EngineTest):
    def test_an_empty_record_still_gives_a_usable_brief(self):
        brief = self.engine.human_brief()
        self.assertIn("No stored speech yet", brief["samples_to_rate"])
        self.assertIn("No recurring mistakes yet", brief["listen_for"])
        self.assertEqual(brief["corrections_to_audit"], [])
        test = brief["sentence_repetition"]
        self.assertEqual((test["form"], len(test["sentences"])), ("A", 12))
        self.assertIn("log_measure('elicited_imitation', score, 24", test["log"])

    def test_samples_are_rated_blind(self):
        ids = [self.engine.log_sample(self.session, "speech", f"Probe {n}")["sample_id"] for n in range(5)]
        self.clock.advance(seconds=30)
        self.engine.log_sample(self.session, "writing", "Ein Text")
        samples = self.engine.human_brief()["samples_to_rate"]
        self.assertEqual(sorted(s["sample_id"] for s in samples), sorted([ids[0], ids[2], ids[4]]))
        self.assertEqual([s["label"] for s in samples], ["S1", "S2", "S3"])
        self.assertEqual(set(samples[0]), {"label", "sample_id", "text"})  # no dates

    def test_the_audit_takes_the_latest_spoken_and_written_corrections(self):
        for n in range(12):
            self.log(correct=False, sentence=f"Deswegen ich habe {n}", correction=f"Deswegen habe ich {n}")
        self.log("listening_detail", correct=False, channel="listening", cause="decoding",
                 sentence="um zehn", correction="gegen zehn")
        self.log("slip", correct=False, sentence="4. A, b, c", correction="A")
        brief = self.engine.human_brief()
        audit = brief["corrections_to_audit"]
        self.assertEqual(len(audit), 10)
        self.assertEqual(audit[0]["learner"], "Deswegen ich habe 11")
        self.assertEqual(brief["listen_for"][0]["after"], "deswegen")
        self.assertEqual(brief["listen_for"][0]["rule"], "Verb second in main clauses")
        self.assertIn("Verb second in main clauses", brief["push_into"])

    def test_the_human_listens_for_three_different_rules(self):
        for trigger in ("deswegen", "dann", "heute", "morgen"):
            self.log(correct=False, trigger=trigger, sentence=f"{trigger} ich gehe")
        self.log("kein_negation", correct=False, trigger="Zeit", sentence="Ich habe nicht Zeit")
        rules = [p["rule"] for p in self.engine.human_brief()["listen_for"]]
        self.assertEqual(sorted(rules), ["Verb second in main clauses", "kein before nouns"])

    def test_sentence_repetition_alternates_forms_every_two_weeks(self):
        self.engine.log_measure("elicited_imitation", 14, 24, rater="human", note="form A")
        self.assertNotIn("sentence_repetition", self.engine.human_brief())
        self.clock.advance(days=14)
        self.assertEqual(self.engine.human_brief()["sentence_repetition"]["form"], "B")


class PatternTest(EngineTest):
    def review(self):
        return next(b for b in iter(lambda: self.engine.next_activity(self.session), None) if b["type"] == "review")

    def test_an_error_needs_its_trigger(self):
        with self.assertRaisesRegex(TutorError, "Send trigger"):
            self.log(correct=False, trigger=None)
        self.log(correct=True, trigger=None)  # optional on right answers
        self.log("slip", correct=False, trigger=None)  # slips aren't patterns

    def test_errors_with_the_same_trigger_are_one_pattern_that_comes_back_next_session(self):
        self.log(correct=False, trigger="Deswegen", sentence="deswegen ich habe")
        self.log(correct=False, trigger=" deswegen ", sentence="deswegen ich kann", channel="spoken")
        self.assertEqual(self.engine.conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0], 1)
        self.assertEqual(self.review()["retests"], [])  # not due today
        self.new_session()
        [retest] = self.review()["retests"]
        self.assertEqual((retest["trigger"], retest["channel"], retest["seen"], retest["their_sentence"]),
                         ("deswegen", "spoken", "2 error(s) on 1 day(s)", "deswegen ich kann"))

    def test_three_passed_retests_on_growing_gaps_resolve_a_pattern(self):
        self.log(correct=False)
        self.new_session()
        [retest] = self.review()["retests"]
        pattern = retest["retest"]
        self.assertEqual(retest["retest_number"], "1 of 3")
        self.assertEqual(self.log(retest=pattern)["retest"], {"pattern": pattern, "passed": True,
                                                              "next_retest": "2026-11-10"})
        with self.assertRaisesRegex(TutorError, "isn't due until 2026-11-10"):
            self.log(retest=pattern)
        self.new_session(days=4)
        self.assertEqual(self.log(retest=pattern)["retest"]["next_retest"], "2026-11-20")
        self.new_session(days=10)
        self.assertTrue(self.log(retest=pattern)["retest"]["resolved"])
        self.assertEqual(self.engine.learner_summary()["patterns"]["resolved"], 1)
        self.new_session()
        self.assertEqual(self.review()["retests"], [])

    def test_a_hinted_retest_does_not_count_and_a_failed_one_resets(self):
        self.log(correct=False)
        self.new_session()
        pattern = self.review()["retests"][0]["retest"]
        self.assertFalse(self.log(retest=pattern, hint_level=1)["retest"]["passed"])
        failed = self.log(retest=pattern, correct=False, trigger=None)  # the trigger comes from the pattern
        self.assertFalse(failed["retest"]["passed"])
        due, stage = self.engine.conn.execute("SELECT due, stage FROM patterns WHERE id = ?", (pattern,)).fetchone()
        self.assertEqual((due, stage), ("2026-11-07", 0))

    def test_a_retest_is_logged_under_its_own_construct(self):
        self.log(correct=False)
        self.new_session()
        pattern = self.review()["retests"][0]["retest"]
        with self.assertRaisesRegex(TutorError, "v2_word_order after 'deswegen'"):
            self.log("kein_negation", retest=pattern)

    def test_recurring_patterns_come_first_with_one_retest_per_construct(self):
        self.log(correct=False, trigger="deswegen")
        self.new_session()
        self.log(correct=False, trigger="deswegen")
        self.log(correct=False, trigger="manchmal")
        self.log("kein_negation", correct=False, trigger="Deutsch")
        self.new_session()
        retests = self.review()["retests"]
        self.assertEqual([(r["construct"], r["trigger"]) for r in retests],
                         [("v2_word_order", "deswegen"), ("kein_negation", "deutsch")])
        top = self.engine.learner_summary()["patterns"]["top"][0]
        self.assertEqual((top["trigger"], top["errors"], top["days"]), ("deswegen", 2, 2))

    def test_targets_show_triggers_and_channels_and_speech_gets_its_own_target(self):
        for _ in range(3):
            self.log("kein_negation", trigger="Arbeit")
            self.log("kein_negation", correct=False, channel="spoken", trigger="Deutsch")
        focus = self.engine.focus()
        self.assertEqual(focus["form"]["by_channel"], {"spoken": "3 of 3 failed", "typed": "0 of 3 failed"})
        self.assertEqual(focus["form"]["triggers"], {"deutsch": "3 of 3 failed", "arbeit": "0 of 3 failed"})
        self.assertEqual(focus["spoken"]["id"], "kein_negation")
        self.new_session()
        blocks = {b["type"]: b for b in (self.engine.next_activity(self.session) for _ in range(5))}
        self.assertEqual(blocks["speaking"]["constructs"][0], "kein_negation")
        self.assertEqual(blocks["focus"]["triggers"]["kein_negation"]["deutsch"], "3 of 3 failed")


class MissCauseTest(EngineTest):
    def test_listening_and_reading_misses_need_a_cause_from_the_list(self):
        with self.assertRaisesRegex(TutorError, "word, sentence, task, unsure"):
            self.log("reading_detail", correct=False)
        self.log("reading_detail", correct=False, cause="task")
        with self.assertRaisesRegex(TutorError, "speed, decoding"):
            self.log("listening_detail", "free", correct=False, channel="listening", cause="knowledge gap")
        self.log("listening_detail", "free", correct=False, channel="listening", cause="speed")
        self.log("numbers_times", correct=False, channel="spoken")  # saying a time: not a listening miss

    def test_reading_days_practise_reading_detail(self):
        self.engine.close_session(self.session, 0)
        self.clock.advance(days=1)  # day 2: a reading day
        self.session = self.engine.start_session(180)["session_id"]
        block = next(b for b in (self.engine.next_activity(self.session) for _ in range(4)) if b["type"] == "input")
        self.assertEqual(block["constructs"], ["reading_detail", "numbers_times"])
        self.assertIn("No word check first", block["instructions"])

    def test_listening_ends_by_ear_and_brings_new_voices_once_a_week(self):
        def listening(days):
            self.new_session(days=days)
            return next(b for b in iter(lambda: self.engine.next_activity(self.session), None)
                        if b["type"] == "input")["instructions"]

        self.assertIn("hasn't heard before", listening(2))  # day 3
        familiar = listening(1)  # day 4
        self.assertIn("the same speakers", familiar)
        self.assertGreater(familiar.index("without the transcript"), familiar.index("while reading the transcript"))
        self.assertIn("once more without the transcript", listening(2))  # day 6: exam-format listening


class WordTest(EngineTest):
    WORDS = [
        {"lemma": "die Aussage", "pos": "noun", "gloss": "statement", "needed": True},
        {"lemma": "termin", "pos": "noun", "article": "der", "gloss": "appointment", "needed": True},
        {"lemma": "verschieben", "pos": "verb", "gloss": "to postpone", "needed": True},
        {"lemma": "Spedition", "pos": "noun", "article": "die", "gloss": "haulage company"},
        {"lemma": "arbeiten", "pos": "verb", "gloss": "to work"},
        {"lemma": "Zitronenfalter", "pos": "noun", "article": "der", "gloss": "brimstone butterfly"},
    ]

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine.export_dir = Path(tmp.name)
        self.engine.set_frequency(["die", "arbeiten", "aussage", "termin", "verschieben"]
                                  + [f"w{i}" for i in range(20000)] + ["spedition"])

    def check(self, words=None):
        return self.engine.word_check(self.session, words or self.WORDS, source="book:B1-U3")

    def keys(self, check_id):
        items = self.engine.conn.execute("SELECT items FROM word_checks WHERE id = ?", (check_id,)).fetchone()[0]
        return [q["key"] for q in json.loads(items)["questions"]]

    def answer_all_right(self):
        check = self.check()
        return self.engine.answer_word_check(check["check_id"], self.keys(check["check_id"]))

    def test_the_check_tests_needed_then_common_words_and_glosses_rare_unneeded_ones(self):
        check = self.check()
        self.assertEqual([i["ask"] for i in check["items"]],
                         ["Aussage", "Termin", "verschieben", "arbeiten", "der, die or das: Aussage?",
                          "der, die or das: Termin?"])
        self.assertEqual([g["word"] for g in check["gloss_only"]], ["die Spedition", "der Zitronenfalter"])
        first = check["items"][0]["options"]
        self.assertIn("statement", first.values())
        self.assertEqual(list(first.values())[-1], "weiß nicht")
        self.assertNotIn("key", json.dumps(check))  # the engine keeps the answers

    def test_a_needed_word_is_tested_however_rare(self):
        check = self.check([
            {"lemma": "arbeiten", "pos": "verb", "gloss": "to work"},
            {"lemma": "Spedition", "pos": "noun", "article": "die", "gloss": "haulage company", "needed": True},
            {"lemma": "Probezeit", "pos": "noun", "article": "die", "gloss": "probation", "needed": True},
            {"lemma": "Termin", "pos": "noun", "article": "der", "gloss": "appointment", "needed": True}])
        self.assertEqual([i["ask"] for i in check["items"] if not i["ask"].startswith("der, die")],
                         ["Termin", "Spedition", "Probezeit", "arbeiten"])  # Probezeit isn't in the list at all
        self.assertEqual(check["gloss_only"], [])

    def test_needed_rare_words_come_before_common_unneeded_ones(self):
        rare = [{"lemma": f"w{15000 + i}", "pos": "other", "gloss": f"rare {i}", "needed": True} for i in range(6)]
        check = self.check(rare + [{"lemma": "arbeiten", "pos": "verb", "gloss": "to work"}])
        self.assertEqual([i["ask"] for i in check["items"]], [f"w{15000 + i}" for i in range(6)])
        self.assertEqual([g["word"] for g in check["gloss_only"]], ["arbeiten"])

    def test_the_engine_scores_the_letters_and_says_what_to_teach(self):
        check = self.check()
        letters = self.keys(check["check_id"])
        letters[0] = "E"  # weiß nicht for Aussage
        result = self.engine.answer_word_check(check["check_id"], letters)
        self.assertEqual(result["right"], "4 of 5")  # Aussage's gender isn't scored without its meaning
        self.assertEqual(result["results"][0], {"word": "die Aussage", "gloss": "statement",
                                                "meaning": "didn't know"})
        self.assertEqual(result["teach"], ["die Aussage = statement"])
        self.assertEqual(result["queued_for_anki"], ["die Aussage"])
        with self.assertRaisesRegex(TutorError, "already answered"):
            self.engine.answer_word_check(check["check_id"], letters)

    def test_answers_must_be_one_valid_letter_per_item(self):
        check = self.check()
        with self.assertRaisesRegex(TutorError, "Send 6 letters"):
            self.engine.answer_word_check(check["check_id"], ["A"])
        with self.assertRaisesRegex(TutorError, "Item 5: the answer is one letter, A to D"):
            self.engine.answer_word_check(check["check_id"], ["A", "A", "A", "A", "E", "A"])

    def test_a_word_is_known_after_two_days_and_a_miss_resets_it(self):
        self.answer_all_right()
        self.new_session()
        self.answer_all_right()
        self.new_session()
        self.assertEqual(self.check()["already_known"], ["die Aussage", "der Termin", "verschieben", "arbeiten"])
        status = self.engine.log_word(self.session, "die Aussage", "meaning", False)["status"]
        self.assertEqual(status, {"meaning": "unknown", "hearing": "untested", "gender": "known", "use": "untested"})

    def test_words_given_in_the_session_prove_nothing_that_day(self):
        self.engine.show_words(self.session, [{"lemma": "einen Termin verschieben", "pos": "chunk",
                                               "gloss": "to move an appointment"}])
        result = self.engine.log_word(self.session, "einen Termin verschieben", "use", True)
        self.assertIn("doesn't count", result["note"])
        self.assertEqual(result["status"]["use"], "untested")
        self.new_session()
        self.assertEqual(self.engine.log_word(self.session, "einen Termin verschieben", "use", True)
                         ["status"]["use"], "learning")

    def test_the_tutor_cannot_record_a_known_meaning(self):
        self.check()
        with self.assertRaisesRegex(TutorError, "can't record that a meaning is known"):
            self.engine.log_word(self.session, "Termin", "meaning", True)

    def test_log_word_adds_new_words_in_dictionary_form(self):
        with self.assertRaisesRegex(TutorError, "isn't in the word list"):
            self.engine.log_word(self.session, "Lebenslauf", "meaning", False)
        with self.assertRaisesRegex(TutorError, "needs its article"):
            self.engine.log_word(self.session, "Lebenslauf", "meaning", False, pos="noun", gloss="CV")
        self.engine.log_word(self.session, "der lebenslauf", "meaning", False, pos="noun", gloss="CV")
        result = self.engine.log_word(self.session, "Lebenslauf", "gender", False, answer="die Lebenslauf")
        self.assertEqual(result["word"], "der Lebenslauf")
        with self.assertRaisesRegex(TutorError, "not a noun"):
            self.engine.log_word(self.session, "gehen", "gender", False, pos="verb", gloss="to go")

    def test_anki_export_writes_a_file_and_caps_new_cards_per_day(self):
        self.engine.set_frequency([f"wort{i}" for i in range(1, 8)])
        for i in range(1, 8):
            self.engine.log_word(self.session, f"Wort{i}", "meaning", False, pos="noun", article="das",
                                 gloss=f"word {i}", example=f"Das ist Wort {i}.")
        first = self.engine.export_anki()
        self.assertEqual((first["exported"], first["left_in_queue"]), (5, 2))
        lines = Path(first["file"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[:3], ["#separator:tab", "#html:false", "#tags column:3"])
        self.assertEqual(lines[3], "das Wort1\tword 1 | Das ist Wort 1.\tgerman-tutor")
        self.assertEqual(self.engine.export_anki()["exported"], 0)
        self.clock.advance(days=1)
        self.assertEqual(self.engine.export_anki()["exported"], 2)

    def test_corrected_errors_become_sentence_cards_one_per_pattern(self):
        self.log(correct=False, sentence="Deswegen ich kann nicht.", correction="Deswegen kann ich nicht.")
        self.log(correct=False, sentence="Deswegen ich fahre nach Berlin.", correction="Deswegen fahre ich nach Berlin.")
        self.log("verb_final_subclause", correct=False, trigger="weil", sentence="Ich bleibe hier, weil ich habe "
                 "keine Zeit.", correction="Ich bleibe hier, weil ich keine Zeit habe.")
        self.log("verb_conjugation", correct=False, trigger="fahren", sentence="Er fahrt.", correction="Er fährt.")
        self.log("kein_negation", correct=False, trigger="Zeit", sentence="Ich habe nicht Zeit heute",
                 correction="Heute habe ich keine Zeit für so etwas")  # a rewrite: no card
        result = self.engine.export_anki()
        self.assertEqual((result["exported"], result["sentence_cards"]), (0, 3))
        lines = Path(result["sentence_file"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[3], "Er ___. (fahrt) · Present-tense verb endings\tEr fährt.\t"
                                   "german-tutor error verb_conjugation")  # the latest first
        self.assertTrue(lines[4].startswith("Ich bleibe hier, weil ich ___. (habe / keine / Zeit)"))
        self.assertEqual(lines[5], "Deswegen ___ nach Berlin. (ich / fahre) · Verb second in main clauses\t"
                                   "Deswegen fahre ich nach Berlin.\tgerman-tutor error v2_word_order")
        self.assertIn("'Basic', one way only", result["instructions"])
        self.log(correct=False, sentence="Deswegen ich gehe.", correction="Deswegen gehe ich.")
        self.assertEqual(self.engine.export_anki()["sentence_cards"], 0)  # that pattern has its card

    def test_sentence_cards_are_capped_per_day(self):
        for word in ("Deswegen", "Dann", "Morgen", "Heute", "Danach", "Jetzt", "Oft"):
            self.log(correct=False, trigger=word, sentence=f"{word} ich gehe nach Hause.",
                     correction=f"{word} gehe ich nach Hause.")
        self.assertEqual(self.engine.export_anki()["sentence_cards"], 5)
        self.assertEqual(self.engine.export_anki()["sentence_cards"], 0)
        self.clock.advance(days=1)
        self.assertEqual(self.engine.export_anki()["sentence_cards"], 2)

    def test_a_rare_word_reaches_anki_only_after_misses_on_two_days(self):
        self.engine.log_word(self.session, "Zitronenfalter", "meaning", False, pos="noun", article="der",
                             gloss="brimstone butterfly")
        self.new_session()
        self.assertTrue(self.engine.log_word(self.session, "Zitronenfalter", "meaning", False)["queued_for_anki"])

    def test_summaries_report_words(self):
        check = self.check()
        self.engine.answer_word_check(check["check_id"], ["E"] * 4 + ["D", "D"])
        closed = self.engine.close_session(self.session, 0)
        self.assertEqual(closed["words"]["checked"], "0 of 4 right")
        overview = self.engine.learner_summary()["words"]
        self.assertEqual(overview["meaning"]["unknown"], 4)
        self.assertEqual(overview["anki_queue"], 4)


class FirstRunTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(datetime(2026, 11, 5, 10, 0))
        self.engine = Engine(":memory:", clock=self.clock)
        self.engine.bootstrap(BOOK)

    def test_a_new_learner_is_interviewed_before_any_lesson(self):
        summary = self.engine.learner_summary()
        self.assertEqual(summary["onboarding"]["ask"], FIRST_QUESTIONS)
        self.assertEqual(summary["onboarding"]["later"], LATER_QUESTIONS)
        with self.assertRaisesRegex(TutorError, "no learner yet"):
            self.engine.start_session(60)
        saved = self.engine.save_profile(name="Sam", first_languages="Turkish", level="B1", address="du",
                                         explain_language="English", worries="listening", schedule="evenings")
        self.assertEqual(saved["profile"]["start_date"], "2026-11-05")
        self.assertEqual(saved["profile"]["exam_day"], 150)
        self.assertEqual(saved["profile"]["first_languages"], ["Turkish"])
        self.assertEqual(saved["profile"]["explain_language"], "English")
        self.assertIn("speaking sample", saved["next"])
        self.assertNotIn("onboarding", self.engine.learner_summary())
        self.assertEqual(set(self.engine.learner_summary()["still_to_ask"]),
                         {"goal", "minutes_per_day", "other_languages", "book", "human_tutor", "exam_format"})
        self.assertEqual(self.engine.learner_summary()["progress"]["day"], 1)
        self.engine.start_session(60)

    def test_later_changes_keep_the_other_answers_and_day_one(self):
        self.engine.save_profile(name="Sam", exam_day=120)
        self.clock.advance(days=10)
        profile = self.engine.save_profile(minutes_per_day=90)["profile"]
        self.assertEqual((profile["name"], profile["exam_day"], profile["start_date"]), ("Sam", 120, "2026-11-05"))

    def test_an_exam_date_sets_the_exam_day(self):
        profile = self.engine.save_profile(name="Sam", exam_date="2027-03-14")["profile"]
        self.assertEqual(profile["exam_day"], 130)  # 5 Nov to 14 Mar is 129 days: day 130
        with self.assertRaisesRegex(TutorError, "not both"):
            self.engine.save_profile(exam_date="2027-03-14", exam_day=100)
        with self.assertRaisesRegex(TutorError, "after day 1"):
            self.engine.save_profile(exam_date="2026-10-01")
        self.assertEqual(self.engine.profile()["exam_day"], 130)

    def test_answers_are_checked(self):
        for bad, message in (({"address": "ihr"}, "du or Sie"), ({"exam_day": 0}, "exam_day"),
                             ({"minutes_per_day": 5}, "minutes_per_day"), ({"start_date": "soon"}, "date like")):
            with self.assertRaisesRegex(TutorError, message):
                self.engine.save_profile(**bad)
        self.assertEqual(self.engine.profile(), {})


class TeachingTextTest(EngineTest):
    def blocks(self):
        return {b["type"]: b for b in (self.engine.next_activity(self.session) for _ in range(7))}

    def test_speaking_asks_for_reasons_and_reading_for_the_words_in_the_text(self):
        self.assertIn("Und was entgegnest du?", self.blocks()["speaking"]["instructions"])
        self.engine.close_session(self.session, 0)
        self.clock.advance(days=1)  # day 2: a reading day
        self.session = self.engine.start_session(180)["session_id"]
        reading = self.blocks()["input"]
        self.assertEqual(reading["mode"], "reading")
        self.assertIn("the learner quotes the words", reading["instructions"])
        self.assertIn("A right choice with the wrong words is a guess", reading["instructions"])

    def test_in_the_exam_weeks_only_wrong_items_get_the_quote(self):
        self.engine.set_profile({"exam_day": 15})
        self.engine.close_session(self.session, 0)
        self.clock.advance(days=1)  # day 2 of 15
        self.session = self.engine.start_session(180)["session_id"]
        reading = self.blocks()["input"]["instructions"]
        self.assertIn("These are the exam weeks: go through the wrong items only", reading)
        self.assertNotIn("one right item in three", reading)


class ReasonTest(EngineTest):
    def stored(self):
        return self.engine.conn.execute("SELECT certain, reason FROM attempts ORDER BY id DESC LIMIT 1").fetchone()

    def test_a_right_form_with_a_contradicting_reason_is_a_guess(self):
        result = self.log(reason="contradicts")
        self.assertIn("guess", result)
        self.assertEqual(tuple(self.stored()), (0, "contradicts"))

    def test_a_right_form_without_a_reason_still_counts(self):
        for _ in range(6):
            self.assertNotIn("guess", self.log(reason="absent"))
        self.new_session()
        self.log(reason="unclear")
        self.assertEqual(self.level(), 3)

    def test_a_right_comprehension_answer_needs_the_words_that_decide_it(self):
        for reason, guess in (("absent", True), ("contradicts", True), ("supports", False), (None, False)):
            result = self.log("reading_detail", "free", True, trigger=None, reason=reason)
            self.assertEqual("guess" in result, guess, reason)
            self.assertEqual(self.stored()["certain"], int(not guess))
        self.log("listening_detail", "free", True, trigger=None, channel="listening", reason="absent")
        self.assertEqual(self.stored()["certain"], 0)

    def test_a_wrong_answer_with_a_right_reason_is_still_an_error_and_calls_for_practice(self):
        result = self.log(correct=False, reason="supports", sentence="Deswegen ich habe keine Zeit.")
        self.assertEqual(tuple(self.stored()), (1, "supports"))
        self.assertIn("Practise it", result["reason_note"])
        result = self.log("reading_detail", "free", False, trigger=None, reason="supports", cause="sentence")
        self.assertIn("misread", result["reason_note"])

    def test_reasons_are_checked(self):
        with self.assertRaisesRegex(TutorError, "reason must be one of"):
            self.log(reason="maybe")

    def test_the_session_summary_counts_reasons(self):
        self.log(reason="supports")
        self.log(reason="supports")
        self.log(reason="contradicts")
        self.log()
        summary = self.engine.close_session(self.session, 4)
        self.assertEqual(summary["reasons"], {"supports": 2, "contradicts": 1})


class ExamTest(EngineTest):
    """The learner's exam decides the exam-format practice, the gates and the mock parts."""

    def take(self, exam, minutes=180, day=1):
        """Switch the learner to `exam` (their own words) and start a session on `day`."""
        self.engine.set_profile({"exam": exam})
        return self.new_session(minutes, days=day - self.engine.progress()["day"])

    def blocks(self, started):
        return {b["type"]: b["minutes"] for b in started["plan"]}

    def instructions(self, kind):
        while True:
            block = self.engine.next_activity(self.session)
            if block.get("done"):
                self.fail(f"no {kind} block")
            if block["type"] == kind:
                return block

    def instructions_on(self, exam, day):
        self.take(exam, day=day)
        return self.instructions("writing")

    def test_the_exam_is_read_from_the_learners_own_words(self):
        for words, exam in (("telc B2", "telc_b2"), ("the DTB after my BAMF course", "dtb_b2"),
                            ("Goethe-Zertifikat B2", "goethe_b2"), ("telc B1·B2 Beruf", "general_b2"),
                            ("Goethe or telc, not sure yet", "general_b2"),
                            ("Goethe, or the BAMF course", "general_b2")):
            self.engine.set_profile({"exam": words})
            self.assertEqual(self.engine.exam()["id"], exam, words)
        summary = self.engine.learner_summary()
        self.assertIn("exam_format", summary["still_to_ask"])
        self.assertEqual(summary["progress"]["next_gate"], None)
        self.engine.close_session(self.session, 0)
        choice = self.engine.save_profile(exam_format="telc_b2")["exam"]
        self.assertEqual((choice["id"], "check" in choice), ("telc_b2", False))
        self.assertNotIn("exam_format", self.engine.learner_summary().get("still_to_ask", {}))
        with self.assertRaisesRegex(TutorError, "exam_format must be one of"):
            self.engine.save_profile(exam_format="toefl")

    def test_a_named_exam_is_confirmed_with_the_learner(self):
        self.engine.close_session(self.session, 0)
        choice = self.engine.save_profile(exam="telc")["exam"]
        self.assertEqual(choice["name"], "telc Deutsch B2")
        self.assertIn("Confirm it with them", choice["check"])
        self.assertIn("isn't clear", self.engine.save_profile(exam="maybe one later")["exam"]["check"])
        self.engine.save_profile(exam_format="none")
        self.assertEqual(self.engine.exam()["id"], "general_b2")
        self.assertNotIn("exam_format", self.engine.learner_summary().get("still_to_ask", {}))

    def test_mock_parts_and_gates_follow_the_exam(self):
        self.engine.set_profile({"exam": "telc Deutsch B2"})
        progress = self.engine.progress()
        self.assertEqual(progress["exam"]["parts"]["sprachbausteine"], "Sprachbausteine")
        self.assertIn("Written and oral", progress["next_gate"]["rule"])
        self.engine.log_measure("mock", 20, 30, part="Sprachbausteine", rater="answer_key")
        self.engine.set_profile({"exam": "Goethe-Zertifikat B2"})
        with self.assertRaisesRegex(TutorError, "Goethe-Zertifikat B2 parts: lesen, hoeren, schreiben, sprechen"):
            self.engine.log_measure("mock", 20, 30, part="sprachbausteine", rater="answer_key")

    def test_exam_format_practice_follows_the_exam(self):
        self.take("telc", day=2)  # a reading day
        self.assertIn("telc Deutsch B2", self.instructions("input")["instructions"])
        self.take("telc", day=6)  # exam-format listening
        listening = self.instructions("input")["instructions"]
        self.assertIn("every text is heard only once", listening)
        self.assertNotIn("Goethe", listening)

    def test_writing_twice_a_week_takes_the_focus_block_and_some_speaking(self):
        started = self.take("telc", day=4)
        blocks = self.blocks(started)
        self.assertEqual(blocks["writing"], 40)  # 30 minutes as in the exam, 10 for feedback
        self.assertNotIn("focus", blocks)
        self.assertEqual(blocks["speaking"], 30)
        self.assertEqual(sum(blocks.values()), 180)
        writing = self.instructions("writing")
        self.assertIn("Aufgabe A", writing["task"])
        self.assertIn("log_sample('writing') before any feedback", writing["instructions"])
        self.assertIn("log_measure('writing_task', score, 45", writing["instructions"])
        self.assertIn("Aufgabe B", self.instructions_on("telc", 7)["task"])
        self.assertNotIn("writing", self.blocks(self.take("telc", day=5)))

    def test_a_long_task_gives_way_to_a_shorter_one_on_a_short_day(self):
        self.assertIn("Teil 1", self.instructions_on("Goethe B2", 4)["task"])  # 50 minutes fits 180
        self.assertEqual(self.blocks(self.take("Goethe B2", minutes=120, day=11))["writing"], 35)  # Teil 2
        self.assertNotIn("writing", self.blocks(self.take("Goethe B2", minutes=30, day=18)))

    def test_one_speaking_part_and_sprachbausteine_come_once_a_week(self):
        self.take("telc", day=3)
        speaking = self.instructions("speaking")
        self.assertIn("Teil 1", speaking["exam_part"])
        self.assertIn("Exam format today (telc Deutsch B2)", speaking["instructions"])
        self.take("telc", day=5)
        self.assertIn("Sprachbausteine", self.instructions("focus")["instructions"])
        self.take("Goethe B2", day=12)
        self.assertNotIn("Sprachbausteine", self.instructions("focus")["instructions"])

    def test_the_weakest_part_gets_its_minutes_in_its_own_block(self):
        self.engine.set_profile({"exam": "telc"})
        self.engine.log_measure("mock", 60, 75, part="lesen", rater="answer_key")
        self.engine.log_measure("mock", 12, 30, part="sprachbausteine", rater="answer_key")
        started = self.new_session(days=2)  # day 3, no writing
        self.assertEqual(self.blocks(started)["focus"], 30)


class MigrationTest(unittest.TestCase):
    def test_an_older_database_gains_the_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.db")
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE attempts (id INTEGER PRIMARY KEY, session_id INTEGER, at TEXT, construct_id TEXT,
                                       context TEXT, correct INTEGER);
                CREATE TABLE errors (id INTEGER PRIMARY KEY, attempt_id INTEGER, construct_id TEXT, at TEXT);
                INSERT INTO attempts VALUES (1, 1, '2026-11-05T22:00:00', 'v2_word_order', 'free', 0);""")
            old.commit()
            old.close()
            conn = connect(path)
            self.assertIn("trigger", {r["name"] for r in conn.execute("PRAGMA table_info(attempts)")})
            self.assertTrue({"pattern_id", "anki_exported_at"}
                            <= {r["name"] for r in conn.execute("PRAGMA table_info(errors)")})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
