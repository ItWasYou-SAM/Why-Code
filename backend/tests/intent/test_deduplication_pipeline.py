"""End-to-end Phase 7 duplicate merge through the intent pipeline."""

from __future__ import annotations

import json
from typing import Any

from intent_helpers import REPO, records, static_client, structural_records

from app.intent.config import IntentConfig
from app.intent.llm.schemas import DuplicateVerdict
from app.intent.models import DecisionStatus
from app.intent.pipeline import IntentPipeline
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.deduplicator import DuplicateCandidate
from app.intent.validation.graph_link_validator import GraphIndex

RETRY_COMMENT = "httpx:review_comment:123"
RETRY_COMMIT = "httpx:commit:" + "a" * 40
RETRY_DIFF = "httpx:pr:1419:file:httpx/_client.py"

TIMEOUT_COMMENT = "httpx:review_comment:300"
TIMEOUT_COMMIT = "httpx:commit:" + "c" * 40
TIMEOUT_DIFF = "httpx:pr:1525:file:httpx/_config.py"

CLAIM = "Retrying requests can duplicate side effects."
RATIONALE = "Server-side effects must not be duplicated."


def candidate(
    evidence_ids: list[str],
    *,
    valid_from: str,
) -> dict[str, Any]:
    return {
        "claim": CLAIM,
        "decision_type": "design_decision",
        "status": "accepted",
        "targets": [{"target_type": "config_key", "target_id": "retries"}],
        "evidence_ids": evidence_ids,
        "participants": [],
        "rationale": RATIONALE,
        "temporal_scope": {"valid_from": valid_from, "valid_until": None},
    }


def envelope(payload: dict[str, Any]) -> str:
    return json.dumps({"candidates": [payload], "missing_evidence": []})


class StubDuplicateJudge:
    def judge(self, candidate: DuplicateCandidate) -> DuplicateVerdict:
        return DuplicateVerdict(
            earlier_intent_id=candidate.earlier.id,
            later_intent_id=candidate.later.id,
            relationship="merge",
            evidence_ids=[candidate.earlier.evidence_ids[0]],
            explanation="the same decision was recorded in two PRs",
        )


def run() -> Any:
    fixture = [
        *records("retry_pr_evidence.json"),
        *records("timeout_pr_evidence.json"),
    ]
    from intent_helpers import FakeStore

    index = GraphIndex.from_sources(
        repo=REPO,
        normalized_records=fixture,
        structural_records=structural_records(),
    )
    provider = static_client(
        {
            "decision_extraction": [
                envelope(
                    candidate(
                        [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
                        valid_from="2024-04-02T06:30:45Z",
                    )
                ),
                envelope(
                    candidate(
                        [TIMEOUT_COMMENT, TIMEOUT_COMMIT, TIMEOUT_DIFF],
                        valid_from="2024-06-01T08:00:00Z",
                    )
                ),
            ],
        }
    )
    pipeline = IntentPipeline(
        config=IntentConfig(repo=REPO),
        store=FakeStore(fixture),
        index=index,
        structured_client=provider,
        duplicate_judge=StubDuplicateJudge(),
    )
    return pipeline.run(EvidenceSelector(REPO).select(fixture))


class TestDuplicateMerge:
    def test_identical_decisions_across_prs_become_one_node(self) -> None:
        result = run()
        assert len(result.intents) == 1
        merged = result.intents[0]
        assert merged.claim == CLAIM
        assert len(merged.merged_from) == 1
        assert set(merged.evidence_ids) == {
            RETRY_COMMENT,
            RETRY_COMMIT,
            RETRY_DIFF,
            TIMEOUT_COMMENT,
            TIMEOUT_COMMIT,
            TIMEOUT_DIFF,
        }
        assert result.graph is not None
        assert result.graph.counts()["nodes"]["Decision"] == 1

    def test_merge_is_serialized_with_the_batch(self) -> None:
        data = run().as_dict()
        assert len(data["duplicate_merges"]) == 1
        merge = data["duplicate_merges"][0]
        assert set(merge) == {
            "earlier_id",
            "later_id",
            "similarity",
            "exact",
            "reason",
            "evidence_ids",
            "target_keys",
        }
        assert data["duplicate_decisions"][0]["relationship"] == "merge"
        assert data["duplicate_decisions"][0]["applied"] == "merged"

    def test_merged_decision_is_active_and_has_no_conflict_edge(self) -> None:
        result = run()
        assert result.intents[0].status is DecisionStatus.ACTIVE
        assert result.conflict_edges == []
