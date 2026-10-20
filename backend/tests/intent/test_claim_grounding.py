"""Phase 4 claim-grounding contracts.

Grounding is lexical and deterministic: an atomic claim must share content
terms with the cited text, citations are scored for relevance, temporal claims
must be anchored in evidence dates, reversals need a change artifact, and
explicit disagreement is surfaced as conflict. These tests pin the firewall's
observable behaviour, not a particular stemming implementation.
"""

from __future__ import annotations

from intent_helpers import REPO, records

from app.intent.selection.evidence_selector import EvidenceItem, EvidenceSelector
from app.intent.validation.claim_grounding import ClaimGrounder, decompose_claim

RETRY_COMMENT = "httpx:review_comment:123"
RETRY_REVIEW = "httpx:review:2001"
RETRY_COMMIT = "httpx:commit:" + "a" * 40
RETRY_DIFF = "httpx:pr:1419:file:httpx/_client.py"


def bundle():
    return EvidenceSelector(REPO).select(records("retry_pr_evidence.json"))[0]


def ground(
    claim: str,
    rationale: str = "",
    evidence_ids: list[str] | None = None,
    **overrides,
):
    current = bundle()
    ids = evidence_ids or [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF]
    items = [current.item(evidence_id) for evidence_id in ids]
    return ClaimGrounder().ground(
        claim,
        rationale,
        [item for item in items if item is not None],
        valid_from=overrides.pop("valid_from", "2024-04-02T06:30:45Z"),
        valid_until=overrides.pop("valid_until", None),
        decision_type=overrides.pop("decision_type", "design_decision"),
        reference_items=[current.pr],
        **overrides,
    )


class TestClaimSupport:
    def test_supported_claim_passes(self) -> None:
        result = ground(
            "Retries are limited to connection errors.",
            "Retrying a read or write can duplicate server-side effects.",
        )
        assert result.ok
        assert result.unsupported_claims == []
        assert result.supporting_evidence_ids

    def test_unrelated_claim_is_unsupported(self) -> None:
        claim = "The team adopted GraphQL for all internal services."
        result = ground(claim)
        assert not result.ok
        assert result.unsupported_claims == [claim]
        assert "unsupported_claim" in {issue.code for issue in result.issues}

    def test_claim_is_decomposed_at_contrast_boundaries(self) -> None:
        claim = (
            "Retries are limited to connection errors, but the database stores "
            "passwords in plaintext."
        )
        atoms = decompose_claim(claim)
        assert len(atoms) == 2
        result = ground(claim)
        assert not result.ok
        assert result.unsupported_claims == [
            "the database stores passwords in plaintext"
        ]

    def test_irrelevant_citation_is_reported(self) -> None:
        result = ground(
            "Retries are limited to connection errors.",
            evidence_ids=[
                RETRY_COMMENT,
                "httpx:pr:1419:file:pyproject.toml",
            ],
        )
        assert result.ok
        assert (
            "httpx:pr:1419:file:pyproject.toml"
            in result.irrelevant_evidence_ids
        )

    def test_weak_only_evidence_is_flagged(self) -> None:
        result = ground(
            "Retries are limited to connection errors.",
            evidence_ids=[RETRY_COMMENT],
        )
        assert result.ok
        assert result.weak_only


class TestDistinctiveness:
    def test_single_term_shared_by_every_citation_cannot_support_alone(self) -> None:
        items = [
            EvidenceItem(
                record_id="httpx:review_comment:1",
                record_type="review_comment",
                pr_number=1419,
                payload={"body": "httpx parses URLs."},
            ),
            EvidenceItem(
                record_id="httpx:review_comment:2",
                record_type="review_comment",
                pr_number=1419,
                payload={"body": "httpx sends requests."},
            ),
        ]
        result = ClaimGrounder().ground(
            "The httpx client uses HTTP/2.", "", items
        )
        assert not result.ok
        assert result.unsupported_claims == ["The httpx client uses HTTP/2."]
        assert "in every cited artifact" in result.issues[0].message

    def test_a_distinctive_term_still_supports_the_claim(self) -> None:
        items = [
            EvidenceItem(
                record_id="httpx:review_comment:1",
                record_type="review_comment",
                pr_number=1419,
                payload={"body": "httpx parses URLs."},
            ),
            EvidenceItem(
                record_id="httpx:review_comment:2",
                record_type="review_comment",
                pr_number=1419,
                payload={"body": "httpx sends requests."},
            ),
        ]
        result = ClaimGrounder().ground(
            "The httpx client sends requests.", "", items
        )
        assert result.ok
        assert "httpx:review_comment:2" in result.supporting_evidence_ids


class TestTemporalGrounding:
    def test_valid_from_must_appear_in_evidence(self) -> None:
        result = ground(
            "Retries are limited to connection errors.",
            valid_from="2024-05-05T00:00:00Z",
        )
        assert not result.ok
        assert any(
            "temporal_date_not_in_evidence" in issue
            for issue in result.temporal_issues
        )

    def test_supporting_evidence_must_not_postdate_valid_until(self) -> None:
        result = ground(
            "Retries are limited to connection errors.",
            valid_from="2024-03-30T09:05:00Z",
            valid_until="2024-03-30T23:59:59Z",
        )
        assert any(
            "temporal_support_after_valid_until" in issue
            for issue in result.temporal_issues
        )


class TestReversalGrounding:
    def test_reversal_requires_commit_or_pr_evidence(self) -> None:
        result = ground(
            "Revert the default retry behaviour.",
            "The previous default duplicated writes.",
            evidence_ids=[RETRY_COMMENT, RETRY_REVIEW],
            decision_type="reversal",
        )
        assert not result.ok
        assert any(
            "reversal_without_later_change" in issue
            for issue in result.reversal_issues
        )


class TestConflictGrounding:
    def test_explicit_disagreement_is_reported(self) -> None:
        current = bundle()
        synthetic = EvidenceItem(
            record_id="httpx:review_comment:999",
            record_type="review_comment",
            pr_number=1419,
            payload={
                "body": "I disagree; we should not retry connection errors.",
                "author": "skeptic",
                "created_at": "2024-03-30T09:15:00Z",
            },
        )
        result = ClaimGrounder().ground(
            "Retries are limited to connection errors.",
            "",
            [current.item(RETRY_COMMENT), synthetic],
            valid_from="2024-04-02T06:30:45Z",
            reference_items=[current.pr],
        )
        assert result.ok
        assert result.conflict
        assert result.conflicting_evidence_ids == ["httpx:review_comment:999"]
