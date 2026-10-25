"""Deterministic synthesis for the prepared demo questions.

The demo does not call an LLM, but it does not bypass the answer contract
either: it receives the same :class:`EvidencePack` as the production
synthesizer, emits an ordinary :class:`AnswerDraft` with ``[E#]`` citations,
and the service still runs :class:`CitationValidator` over the result.

Sentences are written to be short and defensible. Specific details (PR
numbers, file names) appear only in sentences that cite evidence carrying
them, so the validator's fabricated-detail check remains meaningful.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePack
from app.retrieval.demo.data import (
    QUESTION_1,
    QUESTION_2,
    QUESTION_3,
    QUESTION_4,
)
from app.retrieval.models import (
    AnswerConfidence,
    AnswerDraft,
    AnswerStatus,
    QuestionUnderstanding,
)

DefaultOffReview = "httpx:review_comment:300"
DefaultOffCommit = "httpx:commit:cccccccccccccccccccccccccccccccccccccccc"
DefaultOffFile = "httpx:pr:1525:file:httpx/_config.py"
DefaultOffIssue = "httpx:issue_comment:9100"
DefaultOffDoc = "httpx:doc:retries-default"

ConnectionReview = "httpx:review_comment:123"
ConnectionCommit = "httpx:commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ConnectionFile = "httpx:pr:1419:file:httpx/_client.py"
ConnectionIssue = "httpx:issue_comment:9001"

HistoricalReview = "httpx:review_comment:120"
HistoricalCommit = "httpx:commit:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
HistoricalFile = "httpx:pr:1200:file:httpx/_config.py"

TimeoutReview = "httpx:review_comment:500"
TimeoutFile = "httpx:pr:1215:file:httpx/_config.py"
TimeoutIssue = "httpx:issue_comment:8000"

_INSUFFICIENT = (
    "I cannot find a defensible decision trail for this in the ingested "
    "repository history."
)


class DemoSynthesizer:
    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def synthesize(
        self,
        *,
        pack: EvidencePack,
        understanding: QuestionUnderstanding,
        strict: bool = False,
    ) -> AnswerDraft:
        del strict
        if not pack.decisions or not pack.evidence:
            return AnswerDraft(
                answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                answer=_INSUFFICIENT,
                citations=[],
                caveats=[],
                confidence=AnswerConfidence.NONE,
            )

        question = " ".join(understanding.question.casefold().split())
        if question == QUESTION_1.casefold():
            draft = _q1(pack)
        elif question == QUESTION_2.casefold():
            draft = _q2(pack)
        elif question == QUESTION_3.casefold():
            draft = _q3(pack)
        elif question == QUESTION_4.casefold():
            draft = _q4(pack)
        else:
            draft = None
        return draft or _generic(pack)


def _q1(pack: EvidencePack) -> AnswerDraft | None:
    return _compose(
        pack,
        [
            (
                "Retries are disabled by default because automatic retries "
                "can duplicate non-idempotent requests.",
                (DefaultOffReview, DefaultOffIssue),
            ),
            (
                "The default retry count was reset to zero in PR #1525.",
                (DefaultOffFile, DefaultOffCommit),
            ),
            (
                "Callers must enable retries explicitly on the transport.",
                (DefaultOffDoc, DefaultOffReview),
            ),
        ],
    )


def _q2(pack: EvidencePack) -> AnswerDraft | None:
    return _compose(
        pack,
        [
            (
                "Simple retries are limited to connection failures because "
                "only a connection error is guaranteed not to have reached "
                "the server.",
                (ConnectionReview,),
            ),
            (
                "Retrying after a read or write failure could duplicate a "
                "non-idempotent request.",
                (ConnectionIssue, ConnectionCommit),
            ),
            (
                "The check lives in httpx/_client.py, where the retry "
                "decision tests for a connection error before resending.",
                (ConnectionFile,),
            ),
        ],
    )


def _q3(pack: EvidencePack) -> AnswerDraft | None:
    return _compose(
        pack,
        [
            (
                "Yes — the retry approach was revised and then reversed.",
                (HistoricalFile, HistoricalReview),
            ),
            (
                "PR #1200 first enabled retries for transient failures by "
                "default.",
                (HistoricalFile, HistoricalCommit),
            ),
            (
                "PR #1525 later reset the default retry count to zero, so "
                "retries are now opt-in.",
                (DefaultOffFile, DefaultOffCommit),
            ),
            (
                "The connection-failure limit remains a separate current "
                "constraint.",
                (ConnectionFile,),
            ),
        ],
    )


def _q4(pack: EvidencePack) -> AnswerDraft | None:
    return _compose(
        pack,
        [
            (
                "The client splits timeouts into connect, read, write, and "
                "pool limits in httpx/_config.py.",
                (TimeoutFile,),
            ),
            (
                "Each phase can fail independently: a pool timeout means "
                "waiting for a free connection, while a read timeout means "
                "waiting for the server.",
                (TimeoutReview, TimeoutIssue),
            ),
            (
                "Separating them lets callers distinguish connection "
                "starvation from a slow server.",
                (TimeoutIssue, TimeoutReview),
            ),
        ],
    )


def _compose(
    pack: EvidencePack,
    parts: Sequence[tuple[str, Sequence[str]]],
) -> AnswerDraft | None:
    sentences: list[str] = []
    citations: list[str] = []
    for sentence, evidence_ids in parts:
        aliases: list[str] = []
        for evidence_id in evidence_ids:
            alias = pack.alias_for(evidence_id)
            if alias is None or alias in aliases:
                continue
            aliases.append(alias)
        if not aliases:
            continue
        citations.extend(alias for alias in aliases if alias not in citations)
        rendered = " ".join(f"[{alias}]" for alias in aliases)
        sentences.append(_with_citations(sentence, rendered))
    if not sentences:
        return None
    return AnswerDraft(
        answer_status=AnswerStatus.ANSWERED,
        answer=" ".join(sentences),
        citations=citations,
        caveats=[],
        confidence=(
            AnswerConfidence.HIGH
            if len(citations) >= 2
            else AnswerConfidence.MEDIUM
        ),
    )


def _with_citations(sentence: str, rendered: str) -> str:
    """Keep citations inside the sentence they support.

    The validator splits on sentence boundaries. Appending ``[E1]`` after the
    final period would attach it to the next sentence instead.
    """

    text = sentence.strip()
    if text.endswith((".", "!", "?")):
        return f"{text[:-1]} {rendered}{text[-1]}"
    return f"{text} {rendered}"


def _generic(pack: EvidencePack) -> AnswerDraft:
    """A conservative answer for a supported but unprepped question.

    The prepared demo route only accepts the script, so this is a safety net
    rather than a second demo path. It states no detail the pack cannot
    support.
    """

    item = pack.evidence[0]
    alias = pack.alias_for(item.evidence_id)
    if alias is None:
        return AnswerDraft(
            answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            answer=_INSUFFICIENT,
            citations=[],
            caveats=[],
            confidence=AnswerConfidence.NONE,
        )
    label = item.kind.replace("_", " ")
    return AnswerDraft(
        answer_status=AnswerStatus.ANSWERED,
        answer=(
            "The graph record is supported by selected "
            f"{label} evidence [{alias}]."
        ),
        citations=[alias],
        caveats=["The demo has a prepared answer only for the scripted questions."],
        confidence=AnswerConfidence.MEDIUM,
    )


__all__ = ["DemoSynthesizer"]
