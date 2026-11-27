"""The `answer` step: a structured answer from retrieved chunks and rules (`prompts/`).

The model is behind `Answerer`, so the graph is tested with a fake. `MistralAnswerer` uses
`langchain_mistralai.ChatMistralAI.with_structured_output` (`json_schema` mode: with
function calling the model returned sentences as `[text, "[id]"]` lists that fail the schema,
21 of 24 answer items on golden v1, 2026-11-27) with the pinned
model `mistral-small-2603` (Mistral Small 4; listed by the API `/v1/models` on 2026-11-27).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from rag.citations import AnswerSentence
from rag.precedence import PrecedenceDecision
from rag.retrieval import RetrievedChunk
from rag.rules import RuleRow

PROMPT_VERSION = "answer.v4"
"""Prompt history (each version is kept for the reports made with it; all 2026-11-27):
v1 first version; v2 sources labelled S1..Sn (long chunk ids were mangled by the model),
partial and "does not apply" answers allowed; v3 a related rule is not an answer (decline when
no source states what is asked, e.g. statistics or one prefecture's practice); v4 rules from the
rules table, labelled R1..Rn (build step 5)."""
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"
ANSWER_MODEL = "mistral-small-2603"
EXCERPT_CHARS = 1800
LANG_NAMES = {"fr": "French", "en": "English"}

PRECEDENCE_INSTRUCTIONS = {
    "agreement": (
        "The question is about an Algerian national. The Franco-Algerian agreement of 27 December "
        "1968 governs residence rights: base the answer on the agreement (and on the court "
        "decision if one is provided), and say in the first sentence that the agreement governs. "
        "Name only the sources you cite. Do not apply a CESEDA residence rule to an Algerian "
        "national. If the agreement has no equivalent of the permit asked about, say so."
    ),
    "algerian_procedure": (
        "The question is about an Algerian national and about procedure (where, how, documents, "
        "fees). The general procedure sources apply; the certificate itself is set by the "
        "Franco-Algerian agreement."
    ),
    "general": "",
}


class _Sentence(BaseModel):
    text: str = Field(description="One sentence of the answer.")
    source_ids: list[str] = Field(
        description='Labels of the sources that support the sentence, e.g. "S1".'
    )


class StructuredAnswer(BaseModel):
    """Answer to the user's question, built only from the provided sources."""

    declined: bool = Field(description="True when the sources do not answer the question.")
    sentences: list[_Sentence] = Field(default_factory=list)


@dataclass(frozen=True)
class DraftAnswer:
    declined: bool
    sentences: list[AnswerSentence]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    prompt: str = ""
    """The rendered prompt; recorded by tracing only in `full` mode (D-021)."""
    prompt_version: str = ""


class Answerer(Protocol):
    def answer(
        self,
        question: str,
        lang: Literal["fr", "en"],
        chunks: Sequence[RetrievedChunk],
        decision: PrecedenceDecision,
        rules: Sequence[RuleRow] = (),
    ) -> DraftAnswer: ...


def render_prompt(
    question: str,
    lang: Literal["fr", "en"],
    chunks: Sequence[RetrievedChunk],
    decision: PrecedenceDecision,
    rules: Sequence[RuleRow] = (),
) -> str:
    key: str = decision.governing
    if decision.topic == "procedure":
        key = "algerian_procedure"
    sources = "\n\n".join(
        f"{label(i)} {c.title}\n{c.text[:EXCERPT_CHARS]}" for i, c in enumerate(chunks)
    )
    rules_block = ""
    if rules:
        listed = "\n\n".join(f"{rule_label(i)} {r.text(lang)}" for i, r in enumerate(rules))
        rules_block = f"\nRules:\n{listed}\n"
    return PROMPT_PATH.read_text(encoding="utf-8").format(
        lang_name=LANG_NAMES[lang],
        precedence_instruction=PRECEDENCE_INSTRUCTIONS[key],
        question=question,
        sources=sources,
        rules=rules_block,
    )


def label(index: int) -> str:
    return f"S{index + 1}"


def rule_label(index: int) -> str:
    return f"R{index + 1}"


def to_draft(
    structured: StructuredAnswer,
    usage: dict[str, Any] | None = None,
    chunks: Sequence[RetrievedChunk] = (),
    rules: Sequence[RuleRow] = (),
) -> DraftAnswer:
    """Maps source labels back to chunk ids. An unknown label is kept as is, so the citation
    checker removes the sentence."""
    usage = usage or {}
    ids = {label(i): c.id for i, c in enumerate(chunks)}
    ids |= {rule_label(i): r.ref for i, r in enumerate(rules)}
    return DraftAnswer(
        declined=structured.declined,
        sentences=[
            AnswerSentence(s.text, tuple(ids.get(_clean_id(i), _clean_id(i)) for i in s.source_ids))
            for s in structured.sentences
        ],
        prompt_tokens=int(usage.get("input_tokens", 0)),
        completion_tokens=int(usage.get("output_tokens", 0)),
    )


def _clean_id(source_id: str) -> str:
    """Ids as shown in the prompt, without the square brackets models sometimes copy."""
    return source_id.strip().strip("[]").strip()


class MistralAnswerer:
    def __init__(self, api_key: str, model: str = ANSWER_MODEL, temperature: float = 0.0) -> None:
        from langchain_mistralai import ChatMistralAI

        chat = ChatMistralAI(model=model, api_key=api_key, temperature=temperature)
        self.model = model
        self._structured = chat.with_structured_output(
            StructuredAnswer, method="json_schema", include_raw=True
        )

    def answer(
        self,
        question: str,
        lang: Literal["fr", "en"],
        chunks: Sequence[RetrievedChunk],
        decision: PrecedenceDecision,
        rules: Sequence[RuleRow] = (),
    ) -> DraftAnswer:
        prompt = render_prompt(question, lang, chunks, decision, rules)
        result = self._structured.invoke(prompt)
        parsed = result.get("parsed") if isinstance(result, dict) else None
        raw = result.get("raw") if isinstance(result, dict) else None
        usage = getattr(raw, "usage_metadata", None)
        if not isinstance(parsed, StructuredAnswer):
            # Unparseable output: treated as no supported sentence (the gate abstains).
            tokens = _tokens(usage)
            draft = DraftAnswer(
                declined=False,
                sentences=[],
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
            )
        else:
            draft = to_draft(parsed, usage, chunks, rules)
        return replace(draft, model=self.model, prompt=prompt, prompt_version=PROMPT_VERSION)


def _tokens(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    return {
        "prompt_tokens": int(usage.get("input_tokens", 0)),
        "completion_tokens": int(usage.get("output_tokens", 0)),
    }
