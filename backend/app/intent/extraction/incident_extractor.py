"""Deterministic incident extraction.

An ``Incident`` node is created only from a linked issue whose title, body,
labels or discussion uses incident vocabulary. No LLM call is needed or
allowed here: the issue itself is the evidence.
"""

from __future__ import annotations

import re

from app.intent.ids import incident_id
from app.intent.models import IncidentIntent
from app.intent.selection.evidence_selector import PrEvidenceBundle

_INCIDENT = re.compile(
    r"\b(bug|regression|crash|incident|failure|fails|timeout|race|deadlock|"
    r"memory leak|security|vulnerab\w*|hang|corrupt\w*|data loss)\b",
    re.IGNORECASE,
)


class IncidentExtractor:
    def extract(self, bundle: PrEvidenceBundle) -> list[IncidentIntent]:
        incidents: list[IncidentIntent] = []
        issue_ids = {item.record_id for item in bundle.issues}
        for issue in bundle.issues:
            text = " ".join(
                [
                    str(issue.payload.get("title") or ""),
                    str(issue.payload.get("body") or ""),
                ]
            )
            labels = issue.payload.get("labels")
            if isinstance(labels, list):
                text += " " + " ".join(str(label) for label in labels)
            if not _INCIDENT.search(text):
                continue
            number = issue.payload.get("number")
            if not isinstance(number, int):
                continue
            comments = [
                comment.record_id
                for comment in bundle.issue_comments
                if comment.payload.get("issue_number") == number
            ]
            evidence_ids = sorted({issue.record_id, *comments})
            incidents.append(
                IncidentIntent(
                    id=incident_id(repo=bundle.repo, issue_number=number),
                    repo=bundle.repo,
                    issue_id=issue.record_id,
                    summary=str(issue.payload.get("title") or f"issue {number}"),
                    pr_numbers=[bundle.pr_number],
                    evidence_ids=evidence_ids,
                )
            )
        # Keep one node per linked issue even if a PR is ingested twice.
        unique: dict[str, IncidentIntent] = {}
        for incident in incidents:
            if incident.issue_id in issue_ids:
                unique[incident.id] = incident
        return [unique[key] for key in sorted(unique)]


__all__ = ["IncidentExtractor"]
