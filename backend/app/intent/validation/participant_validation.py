"""Participant grounding (Phase 4).

The model may classify a person as an author, reviewer, maintainer, advocate
or objector, but it may not invent a person. A proposed participant is kept
only when the login actually appears in the cited evidence (or is the PR
author), the role is compatible with that artifact, and any explicit
participant evidence is also candidate evidence. Unsupported participants are
dropped; they never become graph relationships.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.intent.llm.schemas import CandidateParticipant
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle

ADVOCATE_MARKERS = re.compile(
    r"\b(agree\w*|support\w*|prefer\w*|good|right call|lgtm|plus one|\+1|yes)\b",
    re.IGNORECASE,
)
OBJECTOR_MARKERS = re.compile(
    r"\b(disagree\w*|concern\w*|shouldn'?t|should not|wrong|incorrect|against|"
    r"avoid\w*|don'?t|do not|not convinced)\b",
    re.IGNORECASE,
)
REVIEWER_TYPES = frozenset({"review", "review_comment"})
COMMENTER_TYPES = frozenset({"review_comment", "issue_comment"})
AUTHORED_TYPES = frozenset(
    {"review", "review_comment", "issue_comment", "commit", "pull_request"}
)


@dataclass
class ParticipantValidation:
    accepted: list[CandidateParticipant] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


class ParticipantValidator:
    def validate(
        self,
        participants: Iterable[CandidateParticipant],
        *,
        bundle: PrEvidenceBundle,
        evidence_items: Iterable[EvidenceItem],
        resolve_citation: Callable[[str], str] | None = None,
    ) -> ParticipantValidation:
        items = list(evidence_items)
        resolver = resolve_citation or (lambda value: value)
        result = ParticipantValidation()
        for participant in participants:
            reason = self._reason(
                participant,
                bundle=bundle,
                items=items,
                resolver=resolver,
            )
            if reason:
                result.rejected.append(
                    f"{participant.login}:{participant.role}"
                )
                result.issues.append(
                    f"participant_unsupported: {participant.login} "
                    f"({participant.role}): {reason}"
                )
            else:
                result.accepted.append(participant)
        return result

    def _reason(
        self,
        participant: CandidateParticipant,
        *,
        bundle: PrEvidenceBundle,
        items: list[EvidenceItem],
        resolver: Callable[[str], str],
    ) -> str:
        login = participant.login.casefold().lstrip("@")
        cited_ids = set(resolver(value) for value in participant.evidence_ids)
        cited = [
            item
            for item in items
            if not cited_ids or item.record_id in cited_ids
        ]
        if cited_ids and not cited:
            return "the cited participant evidence is not candidate evidence"

        authored = [
            item for item in cited if _author(item) == login
        ]
        if participant.role == "author":
            if _author(bundle.pr) != login:
                return "the login is not the pull request author"
            return ""

        if not authored:
            known = _known_login(login, bundle)
            if not known:
                return "the login does not appear anywhere in the evidence"
            return "the login is known but did not author any cited artifact"

        if participant.role == "reviewer":
            if not any(item.record_type in REVIEWER_TYPES for item in authored):
                return "no cited review or review comment was written by this login"
            return ""
        if participant.role == "commenter":
            if not any(item.record_type in COMMENTER_TYPES for item in authored):
                return "no cited comment was written by this login"
            return ""
        if participant.role == "maintainer":
            if not any(item.record_type in AUTHORED_TYPES for item in authored):
                return "no cited artifact shows this login in a maintainer role"
            return ""
        if participant.role == "advocate":
            if not _stance(authored, ADVOCATE_MARKERS):
                return "the cited text does not show support for the claim"
            return ""
        if participant.role == "objector":
            if not _stance(authored, OBJECTOR_MARKERS):
                return "the cited text does not show objection to the claim"
            return ""
        return "unknown participant role"


def _stance(items: list[EvidenceItem], marker: re.Pattern[str]) -> bool:
    for item in items:
        if marker.search(item.text):
            return True
        state = str(item.payload.get("state") or "").upper()
        if marker is ADVOCATE_MARKERS and state == "APPROVED":
            return True
        if marker is OBJECTOR_MARKERS and state == "CHANGES_REQUESTED":
            return True
    return False


def _author(item: EvidenceItem) -> str:
    return (item.author or "").casefold().lstrip("@")


def _known_login(login: str, bundle: PrEvidenceBundle) -> bool:
    return any(_author(item) == login for item in [bundle.pr, *bundle.all_items()])


__all__ = [
    "ADVOCATE_MARKERS",
    "COMMENTER_TYPES",
    "OBJECTOR_MARKERS",
    "ParticipantValidation",
    "ParticipantValidator",
    "REVIEWER_TYPES",
]
