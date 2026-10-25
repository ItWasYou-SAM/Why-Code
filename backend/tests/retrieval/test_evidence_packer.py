"""The evidence pack is the model's entire world. Tests pin its sections,
aliases, priority, supersession label and budget behavior."""

from __future__ import annotations

from retrieval_fixtures import (
    CONFIG_ID,
    candidate,
    context,
    evidence,
    ranked,
    understanding,
)

from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePacker
from app.retrieval.models import DecisionState, RankedCandidate


def _pack(
    *,
    ranked_items=None,
    contexts=None,
    config: RetrievalConfig | None = None,
):
    items = ranked_items or [ranked()]
    maps = contexts or {"dec_001": context(candidate("dec_001"))}
    return EvidencePacker(config).pack(
        understanding=understanding(), ranked=items, contexts=maps
    )


def test_documented_sections_are_present() -> None:
    text = _pack().text
    for marker in (
        "QUESTION:",
        "CANDIDATE DECISION:",
        "[D1]",
        "Status: active",
        "Confidence: high",
        "Targets:",
        "EVIDENCE:",
        "[E1]",
        "GRAPH PATH:",
        "SUPERSESSION:",
        "ANSWER RULES:",
    ):
        assert marker in text


def test_question_is_included_verbatim() -> None:
    pack = _pack()
    assert "Why are retries disabled by default in httpx?" in pack.text


def test_aliases_resolve_to_exact_evidence_ids() -> None:
    pack = _pack()
    assert set(pack.alias_map) == {"E1", "E2"}
    assert pack.alias_map["E1"] == "ev_101"
    assert pack.alias_map["E2"] == "ev_102"
    assert pack.evidence_ids() == ["ev_101", "ev_102"]


def test_decision_aliases_resolve_to_exact_decision_ids() -> None:
    pack = _pack()
    assert pack.decision_alias_map == {"D1": "dec_001"}


def test_resolve_citation_accepts_brackets_case_and_raw_ids() -> None:
    pack = _pack()
    assert pack.resolve_citation("E1") == "ev_101"
    assert pack.resolve_citation("[E1]") == "ev_101"
    assert pack.resolve_citation("e1") == "ev_101"
    assert pack.resolve_citation("ev_101") == "ev_101"
    assert pack.resolve_citation("[ev_101]") == "ev_101"
    assert pack.resolve_citation("[E9]") is None
    assert pack.resolve_citation("D1") is None


def test_alias_for_round_trips() -> None:
    pack = _pack()
    assert pack.alias_for("ev_102") == "E2"
    assert pack.alias_for("nope") is None


def test_evidence_priority_is_diff_then_review_commit_issue() -> None:
    rows = [
        evidence("ev_issue", kind="issue_comment", issue_number=108),
        evidence("ev_commit", kind="commit", path=None, start_line=None),
        evidence("ev_review", kind="review_comment"),
        evidence("ev_diff", kind="changed_file", excerpt="@@ -1 +1 @@"),
    ]
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        }
    )
    positions = [pack.text.index(f"[E{index}]") for index in (1, 2, 3, 4)]
    assert positions == sorted(positions)
    assert pack.alias_map["E1"] == "ev_diff"
    assert pack.alias_map["E2"] == "ev_review"
    assert pack.alias_map["E3"] == "ev_commit"
    assert pack.alias_map["E4"] == "ev_issue"


def test_evidence_is_round_robin_across_decisions() -> None:
    first = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", decision_id="dec_001"),
            evidence("ev_2", kind="commit", decision_id="dec_001"),
        ],
    )
    second = context(
        candidate("dec_002", claim="Simple retries are limited."),
        evidence_items=[
            evidence("ev_3", decision_id="dec_002"),
            evidence("ev_4", kind="commit", decision_id="dec_002"),
        ],
    )
    pack = _pack(
        ranked_items=[ranked(), ranked(candidate("dec_002"))],
        contexts={"dec_001": first, "dec_002": second},
    )
    assert pack.evidence_ids() == ["ev_1", "ev_3", "ev_2", "ev_4"]


def test_max_evidence_items_is_enforced() -> None:
    rows = [evidence(f"ev_{number}") for number in range(10)]
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        },
        config=RetrievalConfig(max_evidence_items=3),
    )
    assert len(pack.evidence) == 3
    assert len(pack.omitted_evidence_ids) == 7
    assert not set(pack.alias_map.values()) & set(pack.omitted_evidence_ids)


def test_omitted_evidence_never_gets_an_alias() -> None:
    rows = [evidence(f"ev_{number}") for number in range(5)]
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        },
        config=RetrievalConfig(max_evidence_items=2),
    )
    assert set(pack.alias_map.values()) == set(pack.evidence_ids())
    assert len(pack.evidence) == 2


def test_graph_path_is_rendered_with_arrows_and_pack_aliases() -> None:
    pack = _pack()
    assert "ConfigKey:" in pack.text
    assert "→ Symbol:" in pack.text
    assert "Decision:D1" in pack.text
    assert "Evidence:E1,E2" in pack.text
    assert "Decision:dec_001" not in pack.text
    assert "Evidence:ev_101" not in pack.text


def test_sections_follow_the_documented_order() -> None:
    text = _pack().text
    markers = (
        "QUESTION:",
        "CANDIDATE DECISION:",
        "EVIDENCE:",
        "GRAPH PATH:",
        "SUPERSESSION:",
        "ANSWER RULES:",
    )
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_evidence_header_includes_kind_author_and_date() -> None:
    text = _pack().text
    assert "[E1] Review comment by alice on 2024-03-30" in text
    assert "PR #1234, httpx/_client.py:312-318" in text


def test_commit_evidence_renders_sha_message_and_date() -> None:
    item = evidence(
        "ev_commit",
        kind="commit",
        commit_sha="abc123",
        author="bob",
        created_at="2024-03-31T12:00:00Z",
        excerpt="Disable automatic retries to avoid duplicating requests.",
        url=None,
        path=None,
        start_line=None,
        end_line=None,
    )
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=[item])
        }
    )
    assert "[E1] Commit message by bob on 2024-03-31" in pack.text
    assert "SHA abc123" in pack.text
    assert '"Disable automatic retries to avoid duplicating requests."' in pack.text


def test_issue_evidence_renders_the_issue_number() -> None:
    item = evidence(
        "ev_issue",
        kind="issue_comment",
        issue_number=108,
        author="bob",
        pr_number=None,
        path=None,
        start_line=None,
        end_line=None,
        url=None,
        excerpt="Retrying non-idempotent requests can duplicate side effects.",
    )
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=[item])
        }
    )
    assert "[E1] Issue comment by bob on 2024-03-30" in pack.text
    assert "Issue #108" in pack.text


def test_targets_are_rendered_without_graph_labels() -> None:
    text = _pack().text
    assert "Targets: retry.max_attempts, Client.send" in text
    assert "Targets: ConfigKey:" not in text


def test_supersession_is_explicit_when_present() -> None:
    item = context(candidate("dec_001"), later_decisions=["dec_009"])
    pack = _pack(contexts={"dec_001": item})
    assert "dec_001 --SUPERSEDED_BY--> dec_009" in pack.text
    assert pack.supersession[0].superseded_by == "dec_009"


def test_no_supersession_reads_as_none_found() -> None:
    pack = _pack()
    assert "None found in ingested window." in pack.text
    assert pack.supersession == []


def test_superseded_status_is_not_relabelled_active() -> None:
    item = candidate("dec_001", status=DecisionState.SUPERSEDED)
    pack = _pack(
        ranked_items=[ranked(item)],
        contexts={"dec_001": context(item)},
    )
    assert "Status: superseded" in pack.text


def test_answer_rules_are_present_verbatim() -> None:
    text = _pack().text
    for rule in (
        "- Use only evidence IDs provided.",
        "- Cite evidence inline as [E1], [E2].",
        "- If evidence does not support an answer, return INSUFFICIENT_EVIDENCE.",
        "- Do not use outside knowledge about the repository.",
        "- Do not speculate about maintainer intent.",
        "- Keep the answer under 180 words.",
    ):
        assert rule in text


def test_pack_is_stable_for_the_same_input() -> None:
    assert _pack() == _pack()


def test_evidence_excerpts_are_truncated() -> None:
    long = evidence("ev_long", excerpt="word " * 200)
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=[long])
        },
        config=RetrievalConfig(max_excerpt_chars=80),
    )
    assert "…" in pack.text
    assert "word " * 200 not in pack.text


def test_token_budget_drops_evidence_instead_of_overrunning() -> None:
    rows = [evidence(f"ev_{number}", excerpt="x" * 300) for number in range(8)]
    config = RetrievalConfig(
        max_context_tokens=200,
        max_answer_tokens=100,
        max_evidence_items=8,
    )
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        },
        config=config,
    )
    assert pack.token_count <= config.max_context_tokens
    assert len(pack.evidence) < 8
    assert pack.omitted_evidence_ids


def test_tiny_budget_keeps_the_answer_rules() -> None:
    rows = [evidence(f"ev_{number}", excerpt="x" * 300) for number in range(4)]
    config = RetrievalConfig(
        max_context_tokens=100,
        max_answer_tokens=100,
        max_evidence_items=4,
    )
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        },
        config=config,
    )
    assert pack.token_count <= config.max_context_tokens
    assert "ANSWER RULES:" in pack.text
    assert "- If evidence does not support an answer" in pack.text


def test_empty_evidence_still_packs_a_decision() -> None:
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=[])
        }
    )
    assert "EVIDENCE:\nNone." in pack.text
    assert pack.alias_map == {}
    assert pack.evidence == []


def test_graph_path_does_not_include_omitted_evidence() -> None:
    rows = [evidence(f"ev_{number}") for number in range(5)]
    pack = _pack(
        contexts={
            "dec_001": context(candidate("dec_001"), evidence_items=rows)
        },
        config=RetrievalConfig(max_evidence_items=2),
    )
    omitted = set(pack.omitted_evidence_ids)
    assert not any(f"Evidence:{evidence_id}" in pack.graph_path for evidence_id in omitted)


def test_decisions_are_capped_at_the_config_limit() -> None:
    items = [
        RankedCandidate(
            candidate=candidate(f"dec_{number}"),
            fused_score=0.1,
            final_score=0.1,
        )
        for number in range(8)
    ]
    contexts = {
        f"dec_{number}": context(
            candidate(f"dec_{number}"),
            evidence_items=[evidence(f"ev_{number}", decision_id=f"dec_{number}")],
        )
        for number in range(8)
    }
    pack = _pack(
        ranked_items=items,
        contexts=contexts,
        config=RetrievalConfig(max_decisions=3),
    )
    assert len(pack.decisions) == 3
    assert set(pack.decision_alias_map) == {"D1", "D2", "D3"}


def test_question_context_is_not_leaked_from_outside_the_pack() -> None:
    pack = _pack()
    assert CONFIG_ID in pack.text
    assert "purple logo" not in pack.text
