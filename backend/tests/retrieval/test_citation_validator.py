"""The citation validator is the last gate before an answer reaches a judge.
It rejects unknown IDs, uncited factual sentences and fabricated quotes."""

from __future__ import annotations

from retrieval_fixtures import candidate, context, evidence, ranked, understanding

from app.retrieval.answer.citation_validator import CitationValidator
from app.retrieval.context.evidence_packer import EvidencePack, EvidencePacker
from app.retrieval.models import (
    AnswerConfidence,
    AnswerDraft,
    AnswerStatus,
)


def _pack() -> EvidencePack:
    return EvidencePacker().pack(
        understanding=understanding(),
        ranked=[ranked()],
        contexts={"dec_001": context(candidate("dec_001"))},
    )


def _draft(
    answer: str,
    *,
    citations: tuple[str, ...] = ("E1", "E2"),
    status: AnswerStatus = AnswerStatus.ANSWERED,
    confidence: AnswerConfidence = AnswerConfidence.HIGH,
) -> AnswerDraft:
    return AnswerDraft(
        answer_status=status,
        answer=answer,
        citations=list(citations),
        caveats=[],
        confidence=confidence,
    )


def test_valid_answer_passes_and_keeps_high_confidence() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX disables retries by default [E1]. "
            "Automatic retries can duplicate requests [E2]."
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.final_confidence is AnswerConfidence.HIGH
    assert result.citations == ["ev_101", "ev_102"]


def test_unknown_citation_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft("The default is unsafe [E9]."), _pack()
    )
    assert result.citation_valid is False
    assert "E9" in result.invalid_citations
    assert result.final_confidence is AnswerConfidence.NONE


def test_citation_to_a_decision_alias_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft("The default is unsafe [D1]."), _pack()
    )
    assert result.citation_valid is False
    assert "D1" in result.invalid_citations


def test_uncited_factual_sentence_is_unsupported() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX does not retry by default. "
            "Automatic retries can duplicate requests [E1]."
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.unsupported_claims == ["HTTPX does not retry by default."]
    assert result.final_confidence is AnswerConfidence.LOW


def test_answer_with_no_citations_fails() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX does not retry by default. Retries are dangerous.",
            citations=(),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert len(result.unsupported_claims) == 2


def test_declared_citation_without_inline_marker_does_not_ground_the_sentence() -> None:
    result = CitationValidator().validate(
        _draft("HTTPX does not retry by default.", citations=("E1",)),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.unsupported_claims


def test_direct_evidence_id_can_be_cited_inline() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX disables retries by default [ev_101].",
            citations=("ev_101",),
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.citations == ["ev_101"]


def test_exact_quote_passes() -> None:
    result = CitationValidator().validate(
        _draft(
            'A reviewer wrote "Unbounded retries can amplify failures during '
            'an outage." [E1].'
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.quote_mismatches == []


def test_fabricated_quote_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            'A reviewer wrote "Retries are bounded by default" [E1].'
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.quote_mismatches == ["Retries are bounded by default"]
    assert result.final_confidence is AnswerConfidence.NONE


def test_ellipsised_quote_matches_when_every_fragment_exists() -> None:
    result = CitationValidator().validate(
        _draft(
            'The review warned "Unbounded retries ... during an outage." [E1].'
        ),
        _pack(),
    )
    assert result.citation_valid is True


def test_contested_answers_are_capped_at_medium_confidence() -> None:
    result = CitationValidator().validate(
        _draft(
            "One decision says retries are unsafe [E1]. "
            "Another says they are required [E2].",
            status=AnswerStatus.CONTESTED,
            confidence=AnswerConfidence.HIGH,
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.final_confidence is AnswerConfidence.MEDIUM


def test_insufficient_status_needs_no_citations() -> None:
    result = CitationValidator().validate(
        _draft(
            "INSUFFICIENT_EVIDENCE",
            citations=(),
            status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            confidence=AnswerConfidence.NONE,
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.final_confidence is AnswerConfidence.NONE
    assert result.unsupported_claims == []


def test_low_model_confidence_is_not_upgraded() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX disables retries by default [E1].",
            citations=("E1",),
            confidence=AnswerConfidence.LOW,
        ),
        _pack(),
    )
    assert result.final_confidence is AnswerConfidence.LOW


def test_repeated_citations_are_deduplicated() -> None:
    result = CitationValidator().validate(
        _draft(
            "HTTPX disables retries [E1]. The same default applies [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citations == ["ev_101"]


def test_supported_specifics_pass_when_cited() -> None:
    result = CitationValidator().validate(
        _draft(
            "The change in httpx/_client.py was reviewed by Alice "
            "for PR #1234 on 2024-03-30 [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is True
    assert result.fabricated_details == []
    assert result.unsupported_claims == []


def test_fabricated_file_name_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "The change is in httpx/_fake.py [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["httpx/_fake.py"]
    assert result.final_confidence is AnswerConfidence.NONE


def test_fabricated_pr_number_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "The change landed in PR #9999 [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["#9999"]


def test_fabricated_issue_number_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "Issue #108 discussed this [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["#108"]


def test_fabricated_date_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "On 2024-04-01 the default changed [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert "2024-04-01" in result.fabricated_details


def test_fabricated_person_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "This was approved by Mallory [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["Mallory"]


def test_detail_from_another_evidence_item_does_not_ground_the_claim() -> None:
    result = CitationValidator().validate(
        _draft(
            "This was approved by Bob [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["Bob"]


def test_fabricated_commit_sha_is_rejected() -> None:
    result = CitationValidator().validate(
        _draft(
            "Commit abc1234 disabled retries [E1].",
            citations=("E1",),
        ),
        _pack(),
    )
    assert result.citation_valid is False
    assert result.fabricated_details == ["abc1234"]


def test_citation_to_evidence_from_an_unselected_decision_is_rejected() -> None:
    pack = _pack()
    foreign = evidence("ev_999", decision_id="dec_999")
    pack.evidence.append(foreign)
    pack.alias_map["E3"] = "ev_999"
    result = CitationValidator().validate(
        _draft("A foreign decision is relevant [E3].", citations=("E3",)),
        pack,
    )
    assert result.citation_valid is False
    assert "E3" in result.invalid_citations


def test_validation_is_stable() -> None:
    draft = _draft("HTTPX disables retries by default [E1].")
    pack = _pack()
    validator = CitationValidator()
    assert validator.validate(draft, pack) == validator.validate(draft, pack)
