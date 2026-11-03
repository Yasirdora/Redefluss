"""Fixed reference data: the construct catalog, the day template and the exam gates.

Each construct belongs to one track: word_order, form, listening, reading or other. Word-order
constructs carry a `stage`: L2 German word order is acquired in a fixed sequence (Pienemann's
processability stages), so a later stage is not drilled while an earlier one is unstable.
Impact and relevance (1-3) are judgments [J] used only to rank the other targets: does the
error stop a listener understanding you, and does B2 assessment care?
"""

CONSTRUCTS = [
    # Word order, in acquisition order.
    dict(id="verb_bracket", track="word_order", stage=1, kind="grammar", name="Verb bracket",
         rule="The second verb part goes to the end: separable prefixes (Ich rufe Sie morgen an), "
              "infinitives after modals (Ich kann heute nicht kommen), participles (Ich habe es gelernt).",
         example="Ich rufe Sie morgen an.", impact=1, relevance=3, treatable=1),
    dict(id="v2_word_order", track="word_order", stage=2, kind="grammar", name="Verb second in main clauses",
         rule="If a main clause starts with anything other than the subject, the verb comes second and "
              "the subject follows it.",
         example="Deswegen habe ich heute keine Zeit.", impact=1, relevance=3, treatable=1),
    dict(id="verb_final_subclause", track="word_order", stage=3, kind="grammar",
         name="Verb last in subordinate clauses",
         rule="After weil, dass, ob, wenn and question words in indirect questions, the conjugated verb "
              "goes to the end. Relaxed speech also has weil + verb second after a pause; writing and "
              "the exam expect verb-final.",
         example="…, weil ich nicht gut Deutsch sprechen kann.", impact=1, relevance=3, treatable=1),
    # Form: morphology and other grammar, ranked by failure rate.
    dict(id="kein_negation", track="form", stage=0, kind="grammar", name="kein before nouns",
         rule="Negate a noun that has ein- or no article with kein-; use nicht for verbs, adjectives "
              "and whole statements.",
         example="Ich finde keine Arbeit.", impact=1, relevance=3, treatable=1),
    dict(id="subject_required", track="form", stage=0, kind="grammar", name="The subject is always there",
         rule="German needs the subject pronoun after a fronted word, even when the verb ending shows "
              "the person. Many languages drop it; German cannot. That includes the empty es: es gibt, es geht "
              "um, es regnet, wie geht es Ihnen. Casual speech may drop a subject at the very start (Hab "
              "keine Zeit), so don't mark that as an error; after a fronted word it is one.",
         example="Deswegen lerne ich jeden Tag Deutsch.", impact=2, relevance=3, treatable=1),
    dict(id="verb_conjugation", track="form", stage=0, kind="grammar", name="Present-tense verb endings",
         rule="ich -e, du -st, er/sie/es -t, wir/sie/Sie -en, ihr -t.",
         example="Manchmal verstehe ich die Leute nicht.", impact=1, relevance=3, treatable=1),
    dict(id="refl_required", track="form", stage=0, kind="grammar", name="Reflexive verbs keep their pronoun",
         rule="Verbs such as sich fühlen, sich vorstellen and sich bewerben always take the reflexive "
              "pronoun.",
         example="Ich fühle mich nicht wohl.", impact=1, relevance=3, treatable=1),
    dict(id="refl_case", track="form", stage=0, kind="grammar", name="Reflexive pronoun: mich or mir",
         rule="Use the dative (mir, dir) when the sentence already has an accusative object; otherwise "
              "the accusative (mich, dich).",
         example="Ich stelle mich vor. / Ich stelle mir die Arbeit interessant vor.",
         impact=1, relevance=3, treatable=1),
    dict(id="article_use", track="form", stage=0, kind="grammar", name="Using an article",
         rule="Most singular nouns need an article (many languages have none). Gender itself is "
              "tracked per noun in the word model (log_word, facet gender) and learned in Anki.",
         example="Ich brauche Deutsch für die Arbeit.", impact=1, relevance=3, treatable=1),
    dict(id="profession_no_article", track="form", stage=0, kind="grammar",
         name="Jobs after sein: no article, nominative",
         rule="After sein and werden a job takes no article (Ich bin Koch); with an article it is "
              "nominative (Ich bin ein guter Koch), never einen.",
         example="Ich bin Koch.", impact=1, relevance=2, treatable=1),
    dict(id="prep_case", track="form", stage=0, kind="grammar", name="Case after prepositions",
         rule="mit, bei, nach, von, zu, aus, seit take the dative; für, ohne, durch, gegen, um the "
              "accusative; in, an, auf, über, unter, vor, hinter, neben, zwischen take the dative for where "
              "and the accusative for where to. A wrong ending can be a wrong gender: ask der, die or das "
              "first, and if that is wrong, work on the noun's gender, not the case.",
         example="Ich spreche mit dem Teamleiter. Ich gehe in die Firma.", impact=1, relevance=3, treatable=1),
    dict(id="verb_preposition", track="form", stage=0, kind="grammar", name="Verbs with a fixed preposition",
         rule="Many verbs and nouns take one preposition that must be learned with them, often not the English "
              "one: warten auf (not für), sich bewerben um, sich interessieren für, Interesse an, teilnehmen an.",
         example="Ich warte auf Ihre Antwort.", impact=2, relevance=3, treatable=1),
    dict(id="adjective_endings", track="form", stage=0, kind="grammar", name="Adjective endings",
         rule="An adjective before a noun takes an ending that depends on the article, gender and case: "
              "der neue Kollege, ein neuer Kollege, mit dem neuen Kollegen. Check the noun's gender first, "
              "as for case after prepositions.",
         example="Ich suche eine neue Stelle.", impact=1, relevance=3, treatable=1),
    dict(id="seit_dativ", track="form", stage=0, kind="grammar", name="seit + dative + present tense",
         rule="seit takes the dative, and the verb stays in the present for something still true.",
         example="Ich wohne seit drei Jahren in Deutschland.", impact=1, relevance=2, treatable=1),
    dict(id="perfekt", track="form", stage=0, kind="grammar", name="Perfekt forms",
         rule="haben or sein as the finite verb, and the right participle form (gelernt, gefunden, "
              "angerufen).",
         example="Ich habe gestern lange gearbeitet.", impact=2, relevance=3, treatable=1),
    dict(id="word_choice", track="form", stage=0, kind="grammar", name="Word choice and false friends",
         rule="Words that English or the first language lead astray: wann (question) vs wenn (condition, repeated) vs als "
              "(one time in the past); bekommen is get, not become; aktuell is current, not actually; "
              "word-for-word calques from English.",
         example="Wenn ich Zeit habe, rufe ich an. Als ich ankam, war niemand da.", impact=2, relevance=2,
         treatable=1),
    dict(id="konj2_polite", track="form", stage=0, kind="function", name="Polite requests with Konjunktiv II",
         rule="wären, hätten, könnten and würden make requests and questions polite.",
         example="Wann wären Sie verfügbar?", impact=2, relevance=3, treatable=1),
    dict(id="polite_indirect_question", track="form", stage=0, kind="function",
         name="Polite indirect questions",
         rule="Könnten Sie mir sagen, … + question word or ob, with the verb at the end.",
         example="Könnten Sie mir sagen, wie lange das Gespräch dauert?", impact=2, relevance=3, treatable=1),
    # Reading, listening and interaction.
    dict(id="reading_detail", track="reading", stage=0, kind="reading", name="Reading for detail",
         rule="Find and check what a task asks for: match statements to texts, true or false, gaps. A miss is "
              "a word (an unknown word blocked it), a sentence (the words were known but the sentence was "
              "misread) or the task (the statement or instruction was misunderstood).",
         example="Aussage 2 passt zu Text C.", impact=2, relevance=3, treatable=1),
    dict(id="listening_detail", track="listening", stage=0, kind="listening", name="Listening for details",
         rule="Catch times, numbers, names and short function words (um vs gegen) in real speech, and "
              "act on them: write the appointment down correctly.",
         example="am Dienstag um zehn Uhr", impact=3, relevance=3, treatable=1),
    dict(id="numbers_times", track="listening", stage=0, kind="listening", name="Numbers, times and dates",
         rule="Units come before tens (einundzwanzig = 21, one-and-twenty, the reverse of English); halb drei is "
              "2:30, not 3:30 as in British English; viertel nach, viertel vor; um zehn is exactly ten, "
              "gegen zehn is about ten; ab, bis; weekdays and times of day (Donnerstagnachmittag); "
              "durations. Hearing them and saying them.",
         example="am Dienstag um halb elf", impact=3, relevance=3, treatable=1),
    dict(id="clarification_repair", track="listening", stage=0, kind="function",
         name="Checking and repairing",
         rule="Ask instead of guessing, and read agreements back: Wie bitte? Um drei oder gegen drei? "
              "Wie schreibt man das? Also Dienstag um zehn, richtig?",
         example="Entschuldigung, um zehn oder gegen zehn?", impact=3, relevance=3, treatable=1),
    dict(id="slip", track="other", stage=0, kind="other", name="Slip or spelling",
         rule="One-off slips and spelling: logged, never drilled.",
         example="", impact=1, relevance=1, treatable=0),
]

# The 3-hour day (revised twice after external reviews: more speaking, less book and drill time), in order; scaled to the session length. "mission" is a real conversation in
# German away from the desk.
DAY_180 = [
    ("review", 20),
    ("lesson", 30),
    ("speaking", 50),
    ("input", 40),
    ("focus", 20),
    ("mission", 15),
    ("close", 5),
]

# Exam gates (day numbers from profile.start_date) and pass rules are per exam: data/exams/*.json.

MEASURE_KINDS = ("mock", "c_test", "onset", "lextale", "fluency", "pronunciation", "human_rating",
                 "elicited_imitation", "correction_audit")
RATERS = ("answer_key", "human", "model")
