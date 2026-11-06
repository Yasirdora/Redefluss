# Redefluss

*Redefluss* is German for "flow of speech". It is a small engine behind an AI German tutor for adults working from about A2 towards B2: confident speaking and listening first, with an exam such as the Goethe-Zertifikat B2 as a checkpoint.

**The model teaches, code keeps score, tools verify.** Any chat app that speaks the Model Context Protocol (MCP) can be the tutor: Claude Desktop, Claude Code, ChatGPT developer mode, Gemini CLI, Google Antigravity and others. The engine decides what to practise and keeps an honest record of what happened, so the tutor can't flatter you or forget your mistakes.

It runs on the stock macOS Python 3.9, uses only the standard library and has no dependencies. Your learner record stays in one SQLite file on your computer; the lessons themselves go through the AI app you connect, and so to its provider.

> **Disclaimer.** Redefluss is unofficial and experimental, and not affiliated with the Goethe-Institut or any exam provider. AI feedback can be wrong, its marks are estimates, and nothing here guarantees an exam result. See [DISCLAIMER.md](DISCLAIMER.md).

## Getting started

```bash
git clone <repository URL> Redefluss
cd Redefluss
python3 -m unittest discover -s tests -t .
```

Register the server with your AI app. For Claude Code:

```bash
claude mcp add german-tutor -- python3 /path/to/Redefluss/server.py
```

For Claude Desktop, add this to `claude_desktop_config.json`:

```json
{"mcpServers": {"german-tutor": {"command": "python3", "args": ["/path/to/Redefluss/server.py"]}}}
```

Then say "Let's start learning German." On the first run there is no learner yet, so the tutor starts with a short interview, the way a new teacher would: what to call you, your first languages, how good your German is now and how you learned it, what you need it for, when your exam is, how much time you have a day, and du or Sie. It saves your answers with `save_profile`; no lesson starts before that. The rest (which language to explain grammar in, what worries you most, when in the week you study, other languages, your textbook, a human teacher) it asks over the first sessions. The first session includes a one-minute speaking sample and one reading item. If you're near your exam's level and have a date, it asks you to do one full mock of the exam in the first week as a baseline; mock scores never change the mastery levels.

The database is created at `data/private/tutor.db` (or wherever `GERMAN_TUTOR_DB` points). Every app you connect reads and writes the same file. Only one session can be open at a time, so close a session in one app before starting another.

Some apps don't pass a server's instructions to the model. For Google Antigravity, write them as an always-on workspace rule:

```bash
python3 scripts/export_rules.py --out /path/to/workspace/.agents/rules/german-tutor.md
```

### Optional: a word-frequency list

With a frequency list, word checks can also test common words a task doesn't need. Without one, only words marked as needed are tested. The OpenSubtitles list (CC BY-SA 4.0, from [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)) suits spoken German:

```bash
mkdir -p data/frequency
curl -L -o data/frequency/de_50k.txt https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt
python3 scripts/import_frequency.py data/frequency/de_50k.txt
```

### Optional: the Anki link

With Anki open and the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on installed, `export_anki` adds the day's new cards straight to a deck called Redefluss, with two note types it creates (Redefluss word, Redefluss sentence), and `sync_anki` reads back how those cards are going. Without it, or while Anki is closed, cards go out as files to import. AnkiConnect listens on this computer only (127.0.0.1:8765), and the engine refuses any other address. Set `GERMAN_TUTOR_ANKI=off` to turn the link off, or `GERMAN_TUTOR_ANKI_URL` for another local port.

### Optional: LanguageTool

`check_text` always runs its verb-position check. With a [LanguageTool](https://dev.languagetool.org/http-server) server running on this computer (port 8081 by default, or `GERMAN_TUTOR_LANGUAGETOOL_URL`), it adds LanguageTool's grammar and spelling findings. The engine never uses the public LanguageTool service, so the learner's text stays on this computer. `GERMAN_TUTOR_LANGUAGETOOL=off` turns it off.

### Optional: a noun lexicon

With a noun lexicon, the gender drill (`gender_drill`) checks the learner's article and plural against a dictionary instead of the tutor model's memory, and a wrong stored article gets corrected. Without one, there is no gender drill. The lexicon from [gambolputty/german-nouns](https://github.com/gambolputty/german-nouns) (CC BY-SA 4.0, built from Wiktionary, about 20 MB) has about 100,000 nouns:

```bash
mkdir -p data/nouns
curl -L -o data/nouns/nouns.csv https://raw.githubusercontent.com/gambolputty/german-nouns/main/german_nouns/nouns.csv
python3 scripts/import_nouns.py data/nouns/nouns.csv
```

### Optional: your textbook

Units of the textbook you work through go in `data/book/`, one JSON file per unit: its pages, sections, target constructs and any answer keys you have. `tests/fixtures/book/B1-U3.json` shows the format with invented content. Publishers' books and keys are copyrighted, so `data/book/` is git-ignored: keep your extractions to yourself. Without a book, the tutor asks which page you're on and works from a photo.

## How it teaches

- **You always try first.** Hints come in steps: the category, then where, then part of the answer.
- **Corrections are prompts.** The tutor points at an error and you fix it, then say the whole sentence again. It gives the right form only when the prompt fails or the rule is new to you. At most two corrections per task, and none while you're speaking for fluency.
- **It asks why, like a teacher in class, but not after every answer.** After every error, the first time you get something new right, and on about one right answer in four while a rule is still new: "Which word decided this form?" (Welches Wort bestimmt hier die Form?), not a general why, and never a yes/no question. Explaining a rule and using it are different skills, so a right form without an explanation still counts; a right form with an explanation that argues for a different form is logged as a guess. A wrong form with the right explanation means you know the rule but it isn't automatic yet, so you practise instead of hearing it again.
- **In reading and listening, it asks where.** After a reading text you quote the words that decide each wrong item and about one right item in three; a right answer backed by the wrong words is a guess, and a wrong answer at the right place is a misreading. In listening you name the words that decided it, and the tutor replays that stretch once. In the last three weeks before the exam this shrinks to the wrong items, to keep exam format.
- **When you're confused, it doesn't lecture.** It asks what you think and what made you think it, names the step that went wrong, helps with that step only, and lets you finish; then you say the rule in one sentence. After two failed prompts it shows the form and has you use it in a new sentence. Rules are explained in the language you chose or as a short German formula (weil … Verb am Ende).
- **Discussions follow the exam.** It lets your first turn run, then asks one follow-up at a time: a reason (Warum denkst du das?), the other side (Was würde jemand dagegen sagen? Und was entgegnest du?), an example, a summary. It switches between du and Sie roles, since register is marked.
- **Speaking repeats a task three times:** free, then after fixing up to two errors and learning three new expressions, then timed with a twist. The next day those expressions come back in a new situation.
- **A human teacher, if you have one, tests what carries over.** Once a week `human_brief` gives them stored speech to rate without dates, the AI's latest corrections to check, and a sentence-repetition test.

## Tools

| Tool | What it does |
|---|---|
| `learner_summary` | Profile, day count and next exam gate, today's focus, every construct's level, last session; on the first run, the interview questions, and later the questions still to ask |
| `save_profile` | Saves the first-run interview, or a later change |
| `start_session` | Plans the day from the learner model; at least 15 minutes, and refuses before the interview or while another session is open |
| `next_activity` | The next block: type, minutes, target constructs, how to teach it, the learner's own error sentences with their triggers, retests due, and whether a break is due |
| `log_attempt` | One judged answer for one construct, with channel, repair, certainty, the trigger word and the learner's reason when asked; updates mastery, records error patterns and retests, and returns signals |
| `close_session` | Refuses until every judged answer is logged, then writes the summary, including planned targets that were never tested and errors logged as slips |
| `get_lesson` | A book unit by id or page, with answer keys and their verification status |
| `log_measure` | Mock-exam modules (with who rated them), C-test, onSET, LexTALE, fluency, pronunciation, human ratings of stored samples, sentence repetition, and a human's audit of the tutor's corrections |
| `log_sample` | Stores the learner's own speech transcripts and writing, uncorrected, for re-scoring and rating later |
| `set_unit_status` | Marks a book unit new, in progress or done |
| `word_check` | Takes up to 15 candidate words, skips known ones, and returns multiple-choice items it scores itself, plus words only to gloss |
| `answer_word_check` | Scores the learner's letters, records the evidence, and says which words to teach and which to gloss |
| `show_words` | Records words given to the learner, such as in a planning step, so using them that day doesn't count |
| `log_word` | One observation about a word: meaning asked about, missed in audio, wrong article, or used without help |
| `check_text` | A second judge for typed text: verb second and verb last (weil, dass) by rule, plus LanguageTool when a local server runs. Findings are leads to check; a clean result proves nothing |
| `gender_drill` | Nouns from the learner's word list whose gender isn't settled, for the learner to say with article and plural; needs the noun lexicon |
| `answer_gender_drill` | Scores the answers against the lexicon: the article as gender evidence for the word, the plural as a `noun_plural` answer with the noun as trigger |
| `export_anki` | Sends today's new Anki cards, words (at most 5 new a day) and sentence cards from the learner's own corrected errors (at most 5): straight into the deck with the Anki link, otherwise as tab-separated files to import |
| `sync_anki` | Reads how the cards it added are going in Anki: interval and lapses |
| `human_brief` | A brief for the weekly human lesson: what to listen for, what to steer towards, stored speech to rate blind, the tutor's latest corrections to audit, and a sentence-repetition test when one is due |
| `speak` | Reads German aloud with macOS `say`, for drills where the content matters more than the voice, such as numbers and times |

## Rules the code enforces

- **Mastery needs first-time, unassisted evidence from more than one session.**
  - A rung counts when the last 8 answers in that context include at least 5 from at least 2 sessions, and at least 80% of them were right the first time.
  - Hinted answers, guesses the learner couldn't explain, and answers the tutor was unsure about are kept but don't count.
  - A self-repair counts as known for recognise, recall and controlled use, but not for free use.
  - Free use only counts when it is unprimed: not after drilling the same construct earlier in that session.
  - Free use (level 4) needs at least one judge besides the tutor model: a checker, the examiner or a human. On tutor scoring alone, the level stops at 3 with a note.
  - Level 5 needs unprimed free use, judged by someone other than the tutor model, 7 or more days after the first.
- **Focus.**
  - Word order follows the order learners acquire it: verb bracket, then verb second, then verb last in subordinate clauses.
    - The earliest stage below free use that has failed is the target, and earlier stages that haven't failed are probed.
    - If the next stage fails too, it is drilled with the target as one contrast straight away: the stages say what to work on first, not what must wait. A stage two steps on waits.
    - Drills are untimed and accuracy-first until the construct reaches controlled use (level 3). Only then do the 3-second timed repetitions start.
  - One form target and one listening target are ranked by failure rate over 14 days, smoothed as (failures + 1) / (answers + 2), times impact and B2 relevance. Unprimed free use decides the rate once there are 3 such answers. An answer that needed a hint counts as a failure here.
  - A construct with fewer than 3 answers is probed, not drilled. One-off slips are logged but never drilled.
- **Mistakes are counted as patterns.**
  - An error on a word-order or form construct needs its trigger: the word that set the rule off (deswegen for verb-second, weil for verb-last, mit for case, the noun for an article). The same construct and trigger make one pattern.
  - A pattern comes back as a retest in the next session's review block: a fresh prompt with the same trigger, in the channel it went wrong in. A retest passed without a hint, unsure scoring or same-day drilling schedules the next one 4 days later, then 10 days after that; passing the third resolves the pattern. Another error resets it.
  - At most 6 retests a session, one per construct, so one doesn't prime the next. Patterns that recur over several days come first.
  - Focus targets show failure rates per channel and per trigger. The speaking block pulls the construct failing most in speech, which written drills don't fix.
  - A listening miss is classified as speed (caught on one audio-only replay), decoding (clear in print), knowledge (unclear in print too), retention or unsure. A reading miss is word, sentence, task or unsure; every reading answer, right or wrong, is backed by the words in the text that decide it.
- **Words have four facets:** meaning, hearing (recognised in speech), gender and use.
  - A facet is known after unprimed evidence on 2 different days since its last miss. A word shown, glossed or corrected earlier in a session counts as primed for the rest of it.
  - The tutor model may record misses and uses, but never that a meaning is known: understanding a text doesn't prove each word.
  - A word check tests up to 6 words by multiple choice with a "weiß nicht" option, plus the gender of each noun not yet known. The engine holds the key and scores the letters. Gender only counts when the meaning was right.
  - With a noun lexicon loaded, the review block has a gender drill: the learner says article, noun and plural (der Termin, die Termine) for their own nouns whose gender isn't known, missed ones first. The lexicon scores it, so the model's memory of der, die or das never decides. Nouns with two genders (der or das Joghurt) accept both.
  - It tests words the task needs, however rare: needed common words first, then the other needed words, then common words the task doesn't need (within the top 10,000 of the frequency list). Other words are only glossed. A subtitle list ranks work words like Bewerbung low, so marking a word as needed is what counts.
  - At most 2 missed words are taught on the spot; the rest are glossed. A missed word goes to Anki if the task needed it, it is common, or it was missed on 2 days, with at most 5 new cards a day. Anki keeps the review schedule.
  - With the Anki link, Anki's record counts: a word whose cards reach a 21-day interval is settled and leaves word checks, and a word forgotten 4 times or more is flagged to be taught another way.
  - Errors become Anki cards too, at most 5 a day: the learner's own sentence, corrected, with the changed words gapped and their words as the hint (`Deswegen ___ nach Berlin. (ich / fahre)`). One card per open pattern, and none for a rewritten sentence.
  - No word check before listening or exam-format practice: a topic preview comes before listening, and unknown words are collected afterwards.
- **The day:** review 20, book lesson 30, speaking 50, input 40, focus drills 20, a real conversation away from the desk 15, close 5 minutes, scaled to the session length.
  - A session is at least 15 minutes. Under 45 minutes the plan is only review, speaking and the close, so a 20-minute minimum day still counts.
  - Anki reviews go first, ideally in the morning; new words go later in the day, since sleep after learning helps them stick.
- **Input rotation:** by day number, 1 reading day and 1 exam-format listening day a week, and narrow listening on the other days. Reading gets a second day while it is the weakest of at least two rated mock modules. Listening ends by listening again while reading the transcript and writing down a short stretch word for word. A 5-minute numbers, times and dates drill opens the block until that construct reaches level 3.
- **Checks fall due:** a C-test (or onSET) every 30 days and a sentence-repetition test every 14; `learner_summary` lists the ones due. The sentence-repetition test has two forms of 12 sentences in `data/measures/`.
- **Exam gates** at days 90, 120 and 140 compare mock-exam scores with the pass mark plus a buffer. Only mocks rated by an answer key or a human count.
- **Breaks:** a 5-minute break after 50 minutes of work, a stop when the planned minutes and their breaks are used up (195 for a full day), and an effort check when hint use rises within a block.
- **Rebalancing:** up to 10 minutes move from the strongest mock-exam module's block to the weakest's, and speaking never gives minutes away.
- **Every session records the engine version,** so data logged under different rules can be told apart. Opening an older database upgrades it in place: missing tables and columns are added, and nothing is deleted.

The thresholds are judgments, marked [J] in the code; the constants at the top of `german_tutor/engine.py` hold them. The design was revised several times after critiques by other AI models and research on how adults learn languages: task repetition, prompts rather than recasts, spaced retrieval, self-explanation, and human ratings as the check on the AI.

## Layout

```
german_tutor/   db.py (schema), catalog.py (constructs, day plan, gates), engine.py, mcp_server.py
server.py       starts the MCP server
data/measures/  the sentence-repetition test, two forms of 12 sentences
data/book/      your textbook units, one JSON file per unit (git-ignored)
data/frequency/ an optional frequency list (git-ignored)
data/nouns/     an optional noun lexicon (git-ignored)
data/private/   tutor.db, Anki exports (git-ignored)
scripts/        init_db.py, import_frequency.py, import_nouns.py, export_rules.py
tests/          engine and MCP protocol tests, with an invented book unit in fixtures/
```

`scripts/init_db.py` sets up a database by hand: it loads the catalog and book units, a profile from `data/private/profile.json` if there is one (see `data/profile.example.json`), and past sessions saved as JSON. You don't need it for a normal start: the server does the same on its own, and the interview replaces the profile file.

## Not built yet

- The speech pipeline: speech recognition, fluency metrics and pronunciation scoring. The learner's own transcript of their recording comes first.
- An independent examiner model, used only once 20 or more stored samples have human ratings, with its agreement with the human reported.

The MCP layer is written without the SDK because the SDK needs Python 3.10 or newer. With a newer Python, swapping in the official `mcp` package only replaces `mcp_server.py`.

## Third-party data

The sentence-repetition forms in `data/measures/` and the test fixtures in `tests/fixtures/` are original. Data you load yourself, such as a frequency list, a noun lexicon or a textbook, keeps its own licence and stays out of the repository.
