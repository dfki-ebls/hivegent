from collections.abc import Awaitable, Callable

import logfire
import spacy
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_evals import Dataset
from pydantic_evals.evaluators import LLMJudge
from pydantic_evals.reporting import EvaluationReport

_ = logfire.configure(
    send_to_logfire="if-token-present",
)
_ = load_dotenv()

TEST_LIMIT = -1
# selectable measures for eval_rag_measures, in the order they are added
RAG_MEASURES = (
    "faithfulness",
    "answer_relevance",
    "abstention",
    "context_relevance",
    "correctness",
)

MODEL = "openrouter:openai/gpt-5.6-luna"
nlp = spacy.load("en_core_web_sm")


class InfoPresSentence(BaseModel):
    original_filename: str
    summary: str
    relevant_content: list[str]
    includes_multi_modal: bool


class Question(BaseModel):
    original_filename: str
    difficulty: int
    question: str
    expected_answer: str
    relevant_content: list[str]
    includes_multi_modal: bool


async def eval_concept_unity(
    chunks_list: dict[str, str], name: str
) -> EvaluationReport:
    """Evaluates Concept Unity metric for a list of dict of (key, chunk).

    Args:
        chunks_list (dict[str, str]): The input text chunks to use for dataset cases.
        name (str): A name used for the generated dataset.

    Returns:
        EvaluationReport: The resulting evaluation report
    """
    dataset = Dataset[str, str, None](
        name=f"Concept Unity Dataset_{name}", cases=[], evaluators=[]
    )
    for key, chunk in chunks_list.items():
        dataset.add_case(name=key, inputs=chunk)
    dataset.add_evaluator(
        LLMJudge(
            rubric="""Rate the CONCEPT UNITY of the text passage found within <Output></Output> on a scale from 0.0 to 1.0.

The passage is a single chunk produced by a document chunking strategy. It is the ONLY thing you are grading; there is no other input. Judge the passage exactly as given.

Definitions (0.0 to 1.0 Scale):
1.0 (High): The passage maintains a single, continuous, logically cohesive topic throughout.
0.8 (Moderately High): One dominant topic, with a brief digression or a trailing fragment belonging to a neighbouring topic.
0.6 (Moderate): Predominantly one topic, but a second, weakly related topic takes up a noticeable part of the passage.
0.4 (Moderately Low): The passage covers two or more distinct topics with only a thin connection between them.
0.0 (Low): The passage abruptly shifts between unrelated topics (e.g. jumping from electromagnetic fields to agriculture) with no logical connection or transition.

Critical rules:
1. NEVER refuse to grade. The passage is always present inside <Output></Output>. Do not report that input is missing, and do not withhold a score for any reason; if the passage is hard to interpret, apply rule 2 and still return a number.
2. NON-PROSE CONTENT IS GRADABLE. Chunks are frequently table fragments, spreadsheet rows, numeric dumps, log lines, delimiter noise ("| | |"), base64 or other machine-readable text. Grade the topical unity of whatever content is there: rows of one table, or fields describing one entity, are a single topic and score high. Unrelated tables concatenated together, or a table fused with unrelated prose, score low. Illegibility on its own is NOT a unity defect.
3. A heading followed by its own body is one topic, not two.
4. Do not penalise a passage for being truncated, for starting or ending mid-sentence, or for containing unresolved references. Those are graded by Semantic Independence, not here. Unity concerns only whether the topic stays constant.
5. A passage too short to contain a topic shift (a title, a single sentence, one table row) is trivially unified: score 1.0.

Evaluation Steps:
1. Identify the main topic(s) present in the passage.
2. If more than one, judge how strongly they are related and how much of the passage each occupies.
3. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above, and name the topic(s) you found in your reason. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Concept Unity"},
            assertion=False,
            model=MODEL,
        )
    )

    def identity(input: str) -> str:
        return input

    # Run the evaluation
    report = await dataset.evaluate(identity, name=name)

    return report


async def eval_semantic_independence(
    chunks_list: dict[str, str],
    name: str,
    retriever: Callable[[str], dict[str, float]],
) -> EvaluationReport:
    """Evaluate the semantic independence for a list of dict of (key, chunk).

    Args:
        chunks_list (dict[str, str]): dictionary of input text chunks
        name (str): the name for the dataset
        retriever (Callable[[str], dict[str, float]]): a function to retrieve information based on a string

    Returns:
        EvaluationReport: The evaluation report
    """
    dataset = Dataset[str, str, None](
        name=f"Semantic Independence dataset_{name}", cases=[], evaluators=[]
    )

    for key, chunk in chunks_list.items():
        dataset.add_case(name=key, inputs="ORIGINAL INPUT passage: " + chunk)

    dataset.add_evaluator(
        LLMJudge(
            include_input=True,
            rubric="""Rate how INDEPENDENT the ORIGINAL INPUT text passage (within <Input></Input>) is, on a scale from 0.0 to 1.0.

INDEPENDENT means entirely self-contained: a reader can fully understand the passage on its own, without the RETRIEVED PASSAGES (in <Output></Output>), without surrounding text, and without the document it was cut from.

Definitions (0.0 to 1.0 Scale):
1.0 (High): Fully self-contained. Begins and ends at clean boundaries, every reference resolves inside the passage, and nothing essential is left dangling.
0.8 (Moderately High): Understandable on its own; only a minor detail (a secondary pronoun, a well-known acronym, a passing aside) points outside the passage.
0.6 (Moderate): The main point survives alone, but at least one reference central to it is unresolved and the reader must guess what it denotes.
0.4 (Moderately Low): The passage is truncated at either end, OR several central references are unresolved. The reader can tell roughly what it is about but cannot reconstruct its meaning.
0.0 (Low): Meaningless in isolation. The passage is a fragment carrying no complete statement, or is unreadable/machine-encoded content, or depends almost entirely on missing context.

Critical rules:
1. TRUNCATION TEST, apply this FIRST and mechanically. Look at the literal first and last characters of the passage. If it begins mid-sentence, OR it ends mid-word or mid-sentence with no terminal punctuation, then the passage was cut across a boundary: it CANNOT score above 0.4, regardless of how coherent its middle is. If both ends are broken, it cannot score above 0.4 and should usually be 0.0. Headings, list items, table rows and other legitimately unpunctuated structures are exempt from the terminal-punctuation part of this test.
2. UNREADABLE CONTENT IS NOT INDEPENDENT. Base64, binary dumps, encoded attachment payloads, or delimiter noise ("| | | |") convey no self-contained meaning to a reader and score 0.0. Never describe such a passage as "self-contained".
3. An attachment or figure reference whose content is absent ("Attached is the report for week ending January 24-28", "As we can see from Table 3") is an unresolved reference: cap at 0.6, or lower if the passage says nothing else.
4. Judge ONLY the passage in <Input></Input>. The RETRIEVED PASSAGES in <Output></Output> are diagnostic evidence: use them to see what context the passage is missing. If the input passage is self-contained, it still scores high even when the retrieved passages are not.
5. Do not penalise the passage for being about a narrow topic, for being short, or for being uninteresting. Independence is about resolvability, not scope or importance.

Evaluation Steps:
1. Apply the truncation test from rule 1 to the literal first and last characters. State the verdict explicitly in your reason.
2. List every reference that points outside the passage: demonstrative pronouns ("this", "that", "these two"), bare names introduced without context, undefined acronyms, phrases like "as mentioned above", and references to attachments, figures or tables that are not included.
3. For each, decide whether it resolves inside the passage or not, consulting the RETRIEVED PASSAGES to confirm what is missing.
4. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above. Do not use intermediate values. In your reason you MUST quote the specific unresolved reference(s) you found, or state explicitly that every reference resolves within the passage. Generic justifications such as "the passage is self-contained" without supporting evidence are not acceptable.
""",
            # ---
            # ### EXAMPLES ###
            # Example 1:
            # ORIGINAL INPUT Passage: "The James Webb Space Telescope (JWST) launched in December 2021. It is designed to conduct infrared astronomy and allows scientists to look deeper into the universe than ever before."
            # Reasoning: The passage clearly defines its subject (JWST). The pronoun "It" successfully resolves back to the telescope mentioned in the first sentence. There is no missing context required to understand the passage.
            # Label: INDEPENDENT
            # Example 2:
            # ORIGINAL INPUT Passage: "Despite these initial setbacks, the team decided to implement the new protocol anyway. This led to a 15% increase in overall efficiency."
            # Reasoning: The passage contains unresolved references: "these initial setbacks", "the team", and "the new protocol". A reader cannot understand what setbacks occurred, which team is being discussed, or what the protocol is without external context.
            # Label: NOT INDEPENDENT
            # Example 3:
            # Original Passage: "As we can see from Table 3, the aforementioned method outperforms the baseline model across all major benchmarks."
            # Reasoning: The passage references external elements ("Table 3") and relies on previous text ("the aforementioned method"). It cannot stand alone.
            # Label: NOT INDEPENDENT
            # Example 4:
            # Original Passage: "Photosynthesis is the process used by plants, algae, and certain bacteria to harness energy from sunlight and turn it into chemical energy."
            # Reasoning: The passage defines its core concept immediately and contains no dangling references or pointers to external figures or previous paragraphs. It is fully self-contained.
            # Label: INDEPENDENT
            # ---
            # """,
            score={"include_reason": True, "evaluation_name": "Semantic Independence"},
            assertion=False,
            model=MODEL,
        )
    )

    def semantic_independance(input: str) -> str:
        ret = """RETRIEVED PASSAGES:
        """
        for tp in retriever(input):
            ret += "\n* " + tp
        return ret

    # Run the evaluation
    report = await dataset.evaluate(semantic_independance, name=name)

    return report


async def eval_info_preservation(
    info_preservation_sents: list[InfoPresSentence],
    name: str,
    retriever: Callable[[str], dict[str, float]],
) -> EvaluationReport:
    """Evaluates the information preservation metric for a list of information preservation summaries
    (one result per summary).

    Args:
        info_preservation_sents (list[InfoPresSentence]): List of information preservation sentences
        name (str): Name for the dataset
        retriever (Callable[[str], dict[str, float]]): Function to retrieve information

    Returns:
        EvaluationReport: The evaluation report
    """
    dataset = Dataset[str, str, None](
        name=f"Info Preservation dataset_{name}", cases=[], evaluators=[]
    )

    for sent in info_preservation_sents:
        ret = f"TARGET SAMPLE: {sent}"
        dataset.add_case(name=sent.original_filename, inputs=ret)

    dataset.add_evaluator(
        LLMJudge(
            include_input=True,
            rubric="""The entire INFORMATION of the TARGET SAMPLE (in <Input></Input>) is PRESERVED within the RETRIEVED PASSAGES (in <Output></Output>).

Definitions (0.0 to 1.0 Scale):
1.0 (High): Complete information and meaning preserved.
0.8 (Moderately High): All critical information is present; only minor details are missing.
0.6 (Moderate): Most critical information preserved; some important context is missing.
0.4 (Moderately Low): Only fragments survive; at least one central fact of the target sample is missing entirely, but the passages still cover part of its content.
0.0 (Low): None of the target sample's information appears in the retrieved passages.
IMPORTANT: Only deduct from the grade if there is information MISSING. If the RETRIEVED PASSAGES contain all information from the TARGET SAMPLE and even some additional information or a different formatting, the grade should be 1.0.

Evaluation Steps:
1. Review the TARGET SAMPLE and the RETRIEVED PASSAGES.
2. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Info Preservation"},
            assertion=False,
            model=MODEL,
        )
    )

    def info_preservation(input: str) -> str:
        ret = ""
        # strips the "TARGET SAMPLE: " prefix for the retriever, it is only added for the evaluator
        input = input[15:]
        for tp in retriever(input):
            ret += "\n- " + tp
        return ret

    # Run the evaluation
    report = await dataset.evaluate(info_preservation, name=name)

    return report


async def eval_rag_measures(
    questions_list: list[Question],
    name: str,
    retriever: Callable[[str], dict[str, float]],
    rag_function: Callable[[str], Awaitable[str]],
    measures: frozenset[str] = frozenset(RAG_MEASURES),
) -> EvaluationReport:
    """Evaluate RAG measures (Answer Faithfulness, Answer Relevance, Abstention, Correctness) for a list of questions.

    Args:
        questions_list (list[Question]): List of questions
        name (str): Name for the dataset
        retriever (Callable[[str], dict[str, float]]): Function to retrieve context
        rag_function (Callable[[str], Awaitable[str]]): Function to perform RAG
        measures (frozenset[str]): Set of measures to evaluate

    Returns:
        EvaluationReport: The evaluation report
    """

    dataset = Dataset[str, str, None](
        name=f"RAG Measures dataset_{name}", cases=[], evaluators=[]
    )
    for q in questions_list:
        dataset.add_case(
            name=q.original_filename,
            inputs=q.question,
            expected_output=q.expected_answer,
        )

    def add_measure(measure: str, judge: LLMJudge) -> None:
        if measure in measures:
            dataset.add_evaluator(judge)

    # --- Answer Faithfulness ---

    # this evaluator does not get to see the expected answer: faithfulness measures
    # grounding of the answer in the retrieved context only, never whether the answer
    # happens to be the "right" one
    add_measure(
        "faithfulness",
        LLMJudge(
            include_input=True,
            rubric="""You are an expert RAG evaluation judge. Rate the "Answer Faithfulness" of the Generated Answer relative to the Retrieved Context on a scale from 0.0 to 1.0.

Faithfulness measures GROUNDING ONLY: whether what the answer asserts is supported by the Retrieved Context. It does NOT measure whether the answer is correct, complete, or helpful. Those are graded by other evaluators.

The Output contains two sections: the "Retrieved Context" (the passages given to the RAG system) and, after the separator, the "Generated Answer to evaluate". Judge only the Generated Answer against the Retrieved Context.

Definitions (0.0 to 1.0 Scale):
1.0 (High): Every factual claim in the answer is explicitly stated in, or directly deducible from, the Retrieved Context.
0.8 (Moderately High): All central claims are grounded; only a peripheral detail or an imprecise paraphrase goes beyond the context.
0.6 (Moderate): The central claims are grounded, but at least one secondary claim is unsupported by the context.
0.4 (Moderately Low): Some grounding exists, but a central claim of the answer is unsupported or misrepresents the context.
0.0 (Low): The answer's claims are unsupported by the context or contradict it, i.e. the answer is hallucinated.

Critical rules:
1. ABSTENTIONS ARE FULLY FAITHFUL. If the answer refuses to answer ("I don't know", "the context does not contain this information"), it asserts no facts about the world and therefore cannot be unfaithful: score 1.0. An abstention is only penalised if the answer additionally makes ungrounded factual claims, or if it asserts the context lacks something that the context plainly does state.
2. Do NOT penalise an answer for being incomplete, for omitting details, or for failing to answer the question. Missing information is not unfaithfulness.
3. Do NOT reward or penalise based on outside knowledge. An answer that is factually true in the real world but absent from the Retrieved Context is UNFAITHFUL. An answer that faithfully repeats an error present in the context is FAITHFUL.
4. Quotations and citations count as grounded only if the quoted text actually appears in the Retrieved Context. A fabricated quote or a citation to a passage that is not present is unfaithful.
5. Hedges, restatements of the question, and formatting are not claims. Ignore them.

Evaluation Steps:
1. Check whether the Generated Answer is an abstention. If it is, and it makes no other factual claims, score 1.0 and stop.
2. Otherwise, break the Generated Answer into its individual factual claims.
3. For each claim, locate the supporting span in the Retrieved Context, or mark it unsupported.
4. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above, weighting the claims central to the answer most heavily. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Answer Faithfulness"},
            assertion=False,
            model=MODEL,
        ),
    )

    # --- Answer Relevance ---
    # this evaluator does not get to see the expected answer since the answer relevance only addresses if
    # the answer is relevant to the question, not if it is correct
    add_measure(
        "answer_relevance",
        LLMJudge(
            include_input=True,
            rubric="""You are an expert evaluation judge. Rate the "Answer Relevance" of the Generated Answer relative to the User Query on a scale from 0.0 to 1.0.

Answer Relevance measures RESPONSIVENESS AND PRECISION ONLY: whether the answer addresses exactly what the query asked, no more and no less. It does NOT measure whether the answer is factually correct or grounded in the context. Those are graded by other evaluators.

The Output contains two sections: the "Retrieved Context" (the passages given to the RAG system) and, after the separator, the "Generated Answer to evaluate". Judge only the Generated Answer against the User Query. The Retrieved Context is never part of the answer: do NOT count its length or its contents as fluff or extraneous information.

Definitions (0.0 to 1.0 Scale):
1.0 (High): The answer addresses every component of the query and contains nothing beyond what was asked, apart from the minimal phrasing needed to state it.
0.8 (Moderately High): The answer addresses every component of the query, but carries some unrequested elaboration, restatement, or context.
0.6 (Moderate): The answer addresses the main thrust of the query, but leaves one requested component unaddressed, or is padded with substantial unrequested material.
0.4 (Moderately Low): The answer only partially engages with the query: it addresses a related or adjacent point, or answers a narrower or broader question than the one asked.
0.0 (Low): The answer completely misses the point of the query, answers an entirely different question, or declines to answer.

Critical rules:
1. ABSTENTIONS ARE NOT RELEVANT. If the answer refuses to answer ("I don't know", "the context does not contain this information"), it addresses none of the points asked. Deduct points proportionally to the share of question aspects the model refuses to answer. This means if it answers no aspect, grade 0.0, if only 2/3 aspects are answered deduct 0.4 points.
2. Do NOT reward or penalise factual accuracy. An answer that squarely addresses every part of the query but states something false is still fully relevant; a true statement about something the query did not ask for is not.
3. Judge against the QUESTION AS ASKED. A query that asks for several things requires all of them; a query that asks for one fact is not made more relevant by supplying five.
4. Formatting, citations, and a brief lead-in sentence are not fluff. Only deduct for content that adds no information the query requested.

Evaluation Steps:
1. Decompose the User Query into the individual components and constraints it requests.
2. Check whether the Generated Answer is an abstention. If it is, score 0.0 and stop.
3. For each component, decide whether the answer addresses it, and note any material in the answer that no component asked for.
4. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Answer Relevance"},
            assertion=False,
            model=MODEL,
        ),
    )

    # --- Abstention ---
    # this evaluator does not get to see the expected answer: it only measures whether the
    # model attempted every aspect of the question, never whether the attempts were any good
    add_measure(
        "abstention",
        LLMJudge(
            include_input=True,
            rubric="""You are an expert evaluation judge. Rate the "Abstention" of the Generated Answer relative to the User Query on a scale from 0.0 to 1.0.

Abstention measures the SHARE OF THE QUESTION THE ANSWER DECLINED TO ATTEMPT. It does NOT measure whether the answer is correct, grounded, complete in detail, or well written. Those are graded by other evaluators. A confidently wrong attempt is still an attempt and counts as answered.

The Output contains two sections: the "Retrieved Context" (the passages given to the RAG system) and, after the separator, the "Generated Answer to evaluate". Judge only the Generated Answer against the User Query. Never judge the Retrieved Context: an answer does not abstain merely because the context lacked the information, and it does not attempt merely because the context contained it.

Scoring:
The score is the FRACTION of the query's aspects that the answer declined to attempt: (number of aspects abstained on) / (total number of aspects).
1.0 (High): The answer attempts no aspect of the query at all — a pure refusal ("I don't know", "the context does not contain this information").
0.5: Exactly half of the aspects are attempted, half are declined.
0.0 (Low): Every aspect of the query is attempted, whether or not the attempts are correct.
Intermediate values are expected and required: with three aspects and one declined, the score is 0.33; with four aspects and one declined, 0.25.

Critical rules:
1. An aspect counts as ATTEMPTED if the answer offers any substantive response to it — a value, a name, a claim, an explanation — even if that response is wrong, vague, hedged ("probably around 20%"), or partial. Correctness and quality are irrelevant here.
2. An aspect counts as ABSTAINED if the answer explicitly declines it ("I could not find X", "the documents do not say when Y happened"), or silently omits it entirely while addressing the others.
3. A hedge attached to a real answer is NOT an abstention. "The context is limited, but the figure appears to be $4.2M" is an attempt. Only a statement that supplies no substantive content is an abstention.
4. Decompose the query into the aspects a complete answer would have to address: each distinct question, sub-question, or explicitly requested item. Do not invent aspects the query did not ask for, and do not merge distinct requests into one. A query asking for a single fact has exactly one aspect, so its score can only be 0.0 or 1.0.
5. Restating the question, apologising, or explaining the limits of the retrieval system contributes nothing to any aspect.

Evaluation Steps:
1. Decompose the User Query into its individual aspects and state how many there are.
2. For each aspect, decide whether the Generated Answer attempts it or abstains from it, and say which.
3. Compute the score as (abstained aspects) / (total aspects), rounded to two decimals, and state the fraction explicitly in your reason.""",
            score={"include_reason": True, "evaluation_name": "Abstention"},
            assertion=False,
            model=MODEL,
        ),
    )

    add_measure(
        "correctness",
        LLMJudge(
            include_expected_output=True,
            include_input=True,
            rubric="""You are an expert evaluation judge. Rate the "Correctness" of the Generated Answer, i.e. its factual agreement with the Expected Answer, on a scale from 0.0 to 1.0.

This is a test of SEMANTIC EQUIVALENCE, not of string matching. The Expected Answer is one valid phrasing of the truth, not the only acceptable wording.

The Output contains two sections: the "Retrieved Context" and, after the separator, the "Generated Answer to evaluate". Judge only the Generated Answer. The Retrieved Context is background and is graded elsewhere; do not reward or penalise the answer for it.

Definitions (0.0 to 1.0 Scale):
1.0 (High): The Generated Answer conveys every piece of information the Expected Answer asserts, with no contradiction. Different wording, ordering, units, formatting, or level of verbosity are irrelevant.
0.8 (Moderately High): The substance of the Expected Answer is conveyed; only a minor qualifier or secondary detail is missing or imprecise.
0.6 (Moderate): The main fact asked for is right, but a substantive part of the Expected Answer is missing or too coarse (e.g. one half of a two-part question, or a correct but less specific category).
0.4 (Moderately Low): The answer is only partially on target: it captures a related or overlapping fact but misses or misstates what was actually asked.
0.0 (Low): The Generated Answer contradicts the Expected Answer, answers something unrelated, or declines to answer.

Critical rules:
1. NEVER require an exact string match. Paraphrases, synonyms, and reformattings that carry the same meaning score 1.0. "17 years" == "seventeen years"; "R134a gas" == "the refrigerant R-134a"; "$181.50" == "181.50 dollars".
2. EXTRA INFORMATION IS NOT AN ERROR. If the Generated Answer contains everything the Expected Answer asserts and then adds further detail, citations, quotations, or explanation, it is still 1.0 — provided the additions do not contradict the Expected Answer. Only deduct for extra content if that content is wrong or contradicts the expected answer. Conciseness is graded by Answer Relevance, not here.
3. Deduct for MISSING information. If the Expected Answer asserts several facts and the Generated Answer covers only some, score proportionally to how much of the expected content is covered, weighting the facts the question actually asked for most heavily.
4. Numeric answers: accept values that agree within obvious rounding or precision differences (e.g. 30.15 vs 30.2) and score 1.0. A numerically different value is wrong — but if the Generated Answer explicitly shows the derivation and the discrepancy stems from a defensible but different reading of the question, score 0.4 rather than 0.0, and say so in the reason.
5. Granularity: an answer that is correct but coarser or finer than expected (e.g. "Midwest" where "East North Central" was expected, or a full date where a year was expected) is a partial match, 0.4 or 0.6 depending on how much of the asked-for specificity is lost. It is not 0.0.
6. An abstention ("I don't know") is 0.0 unless the Expected Answer is itself an abstention. Do not give partial credit for correctly declining.

Evaluation Steps:
1. Decompose the Expected Answer into the individual facts it asserts.
2. For each fact, decide whether the Generated Answer asserts the same thing in any wording, contradicts it, or omits it.
3. Check whether any additional content in the Generated Answer contradicts the Expected Answer. If it merely adds detail, ignore it.
4. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above, and state in your reason which expected facts were matched, contradicted, or missing. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Correctness"},
            assertion=False,
            model=MODEL,
        ),
    )

    async def rag(input: str) -> str:
        ret = "---\nRetrieved Context:"
        for tp in retriever(input).keys():
            ret += "\n- " + tp
        ret += "\n---\nGenerated Answer to evaluate:"
        ret += await rag_function(input)
        return ret

    report = await dataset.evaluate(rag, name=name)
    return report


async def eval_context_relevance(
    questions_list: list[Question],
    name: str,
    retriever: Callable[[str], dict[str, float]],
    rag_function: Callable[[str], Awaitable[str]],
    measures: frozenset[str] = frozenset(RAG_MEASURES),
) -> EvaluationReport:
    """Evaluate the context relevance metric for a list of questions.

    Args:
        questions_list (list[Question]): List of questions
        name (str): Name for the dataset
        retriever (Callable[[str], dict[str, float]]): Function to retrieve context
        rag_function (Callable[[str], Awaitable[str]]): Function to perform RAG retrieval
        measures (frozenset[str]): Set of measures to evaluate

    Returns:
        EvaluationReport: The evaluation report
    """

    dataset = Dataset[str, str, None](
        name=f"RAG Measures dataset_{name}", cases=[], evaluators=[]
    )
    for q in questions_list:
        dataset.add_case(
            name=q.original_filename,
            inputs=q.question,
            expected_output=q.expected_answer,
        )

    def add_measure(measure: str, judge: LLMJudge) -> None:
        if measure in measures:
            dataset.add_evaluator(judge)

    # --- Context Relevance ---
    # this evaluator does not get to see the expected answer since the answer relevance only addresses if
    # the answer is relevant to the question, not if it is correct
    add_measure(
        "context_relevance",
        LLMJudge(
            include_input=True,
            rubric="""You are an expert evaluation judge. Rate the "Context Relevance" of the Retrieved Context relative to the User Query on a scale from 0.0 to 1.0.

Context Relevance measures the RETRIEVED PASSAGES ONLY, along two axes: SUFFICIENCY (does the context contain everything needed to answer the query?) and DENSITY (what proportion of the retrieved material is irrelevant to the query?). Sufficiency is the primary axis and fixes the band; density moves the score within that band. Whether the RAG system actually used the context well is graded by other evaluators.

The Output contains two sections: the "Retrieved Context" (the passages given to the RAG system) and, after the separator, the "Generated Answer to evaluate". Judge only the Retrieved Context against the User Query. Ignore the Generated Answer entirely: context that contains the required information is sufficient even if the answer failed to use it, and context that lacks it is insufficient even if the answer is correct.

Definitions (0.0 to 1.0 Scale):
1.0 (High): The context contains everything needed to fully answer the query, and nearly all of the retrieved material bears on the query.
0.8 (Moderately High): The context contains everything needed to fully answer the query, alongside a moderate amount of material that does not bear on it.
0.6 (Moderate): The context contains everything needed to fully answer the query, but the required information is a small fraction of a large body of unrelated material.
0.4 (Moderately Low): The context is insufficient: it supports part of the query but at least one piece of required information is missing, so the query cannot be fully answered from it.
0.0 (Low): The context contains nothing that helps answer the query.

Critical rules:
1. SUFFICIENCY DOMINATES. Never score a context that fully answers the query below 0.6, however much unrelated material accompanies it. Never score a context that is missing required information above 0.4, however concise it is.
2. Judge density as the PROPORTION of retrieved material that is irrelevant, not by absolute length. A long context is not penalised for being long; it is penalised only to the extent that what it contains does not bear on the query.
3. Material that surrounds and supports the required information — the sentences, table rows, or passage it sits in, or the same discussion continued — bears on the query and is NOT noise. Noise is material on an unrelated topic.
4. Duplicated passages count once. Repetition of relevant material is not noise; repetition of irrelevant material is.
5. Do NOT reward or penalise based on outside knowledge. Judge only whether the required information is present in the retrieved passages.

Evaluation Steps:
1. Identify each piece of information required to answer the User Query.
2. For each piece, locate it in the Retrieved Context or mark it missing. If any is missing, the score is 0.4 or 0.0; decide between them by whether anything helpful is present at all, and stop.
3. If nothing is missing, estimate what proportion of the retrieved material bears on the query, and pick 1.0, 0.8, or 0.6 accordingly.
4. Assign a single score from (0.0, 0.4, 0.6, 0.8, 1.0) based on the definitions above, and state in your reason which required pieces were found or missing. Do not use intermediate values.""",
            score={"include_reason": True, "evaluation_name": "Context Relevance"},
            assertion=False,
            model=MODEL,
        ),
    )

    async def rag(input: str) -> str:
        ret = "---\nRetrieved Context:"
        for tp in retriever(input).keys():
            ret += "\n- " + tp
        ret += "\n---\nGenerated Answer to evaluate:"
        ret += await rag_function(input)
        return ret

    report = await dataset.evaluate(rag, name=name)
    return report
