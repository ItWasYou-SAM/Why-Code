"""Participant roles grounded in named evidence.

``AUTHORED`` and ``REVIEWED`` come directly from record fields. Stance roles
(``ADVOCATED`` / ``OBJECTED_TO``) are emitted only for a review or review
comment that is itself cited evidence for the intent and whose text or
review state contains an explicit stance marker. Every edge carries the
source evidence ID and a basis string.
"""

from __future__ import annotations

from app.domain.evidence_ids import person_id
from app.intent.models import IntentBase, Participant
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle

_OBJECTION_MARKERS = (
    "don't think",
    "dont think",
    "do not think",
    "should not",
    "shouldn't",
    "must not",
    "revert",
    "concern",
    "block",
    "instead",
    "why ",
    "please change",
    "request changes",
    "disagree",
    "not convinced",
)
_ADVOCACY_MARKERS = (
    "lgtm",
    "+1",
    "agree",
    "makes sense",
    "sounds good",
    "good catch",
    "nice",
    "thanks",
    "i like",
    "works for me",
)


class ParticipantExtractor:
    def extract(
        self,
        bundle: PrEvidenceBundle,
        intents: list[IntentBase],
    ) -> list[Participant]:
        participants: list[Participant] = []
        seen: set[tuple[str, str, str]] = set()

        def add(
            login: str | None,
            role: str,
            target_id: str,
            target_type: str,
            *,
            evidence_id: str | None = None,
            basis: str = "",
        ) -> None:
            if not login:
                return
            key = (login, role, target_id)
            if key in seen:
                return
            seen.add(key)
            participants.append(
                Participant(
                    person_id=person_id(repo=bundle.repo, login=login),
                    login=login,
                    role=role,  # type: ignore[arg-type]
                    target_id=target_id,
                    target_type=target_type,
                    evidence_id=evidence_id,
                    basis=basis,
                )
            )

        add(
            bundle.pr.author,
            "AUTHORED",
            bundle.pr.record_id,
            "PullRequest",
            evidence_id=bundle.pr.record_id,
            basis="pull_request_author",
        )
        for commit in bundle.commits:
            add(
                commit.author,
                "AUTHORED",
                commit.record_id,
                "Commit",
                evidence_id=commit.record_id,
                basis="commit_author",
            )
        for review in bundle.reviews:
            add(
                review.author,
                "REVIEWED",
                bundle.pr.record_id,
                "PullRequest",
                evidence_id=review.record_id,
                basis="review_author",
            )

        # Stance is only considered for evidence the validated intent cites.
        for intent in intents:
            for evidence_id in intent.evidence_ids:
                item = bundle.item(evidence_id)
                if item is None:
                    continue
                role = _stance(item)
                if role is None:
                    continue
                add(
                    item.author,
                    role,
                    intent.id,
                    intent.kind.value,
                    evidence_id=evidence_id,
                    basis=_basis(item, role),
                )
        return sorted(
            participants,
            key=lambda participant: (
                participant.person_id,
                participant.role,
                participant.target_id,
            ),
        )


def _stance(item: EvidenceItem) -> str | None:
    if item.record_type == "review":
        state = str(item.payload.get("state") or "")
        if state == "CHANGES_REQUESTED":
            return "OBJECTED_TO"
        if state == "APPROVED":
            return "ADVOCATED"
        text = str(item.payload.get("body") or "").lower()
    elif item.record_type == "review_comment":
        text = str(item.payload.get("body") or "").lower()
    else:
        return None
    if any(marker in text for marker in _OBJECTION_MARKERS):
        return "OBJECTED_TO"
    if any(marker in text for marker in _ADVOCACY_MARKERS):
        return "ADVOCATED"
    return None


def _basis(item: EvidenceItem, role: str) -> str:
    if item.record_type == "review":
        return f"review_state:{item.payload.get('state')}:{role.lower()}"
    return f"review_comment_text:{role.lower()}"


__all__ = ["ParticipantExtractor"]
