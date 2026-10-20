"""Region-first evidence packing under a hard, category-aware token budget.

The packer answers one question: *what is the smallest evidence set that lets
a model explain why this code region changed?* It never includes a whole
file, a whole comment thread, CI logs, or every commit in a large PR. The
priority order is fixed (D-0040):

1. the diff hunk around each selected changed region, with the current and
   previous side reconstructed from the patch;
2. review comments anchored to that hunk;
3. commit messages (head and rationale commits, capped);
4. the PR title and description;
5. linked issues and a bounded, summarised issue discussion;
6. docs changed in the same PR;
7. the target catalogue (copy-only identifiers).

Every included artifact is rendered with an ``[E#]`` label *and* its exact
``record_id``. The label is a convenience for the model; the record ID remains
the citation currency validated by ``EvidenceValidator`` (D-0034). Items that
do not fit are reported in ``omitted_ids`` and are never shown, so a citation
can never point outside what was actually retrieved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.intent.context.budget import allocate_budget
from app.intent.context.filters import (
    is_ci_path,
    is_readme_path,
    is_test_path,
)
from app.intent.context.region_context import RegionContext, build_region_contexts
from app.intent.context.token_budget import TokenBudget
from app.intent.models import TargetOption, TargetType
from app.intent.selection.candidate_detector import Candidate
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle

# Per-section ceilings. Category budgets still decide the total; these stop a
# single long comment or hunk from consuming the whole category.
ITEM_TOKEN_CAPS: dict[str, int] = {
    "diff": 1000,
    "review_comments": 400,
    "commit_messages": 260,
    "pr_description": 500,
    "issue_context": 360,
    "docs": 420,
    "targets": 800,
}
DEFAULT_ITEM_TOKEN_CAP = 260
MIN_SECTION_TOKENS = 16

RATIONAL_MARKERS = (
    "because",
    "so that",
    "in order to",
    "reason",
    "why",
    "fix",
    "prevent",
    "avoid",
    "require",
)

_RULES = """\
RULES:
- Cite only evidence IDs present above; copy the [E#] label or the exact ID printed beside it.
- If the evidence is insufficient, return no decision.
- Do not infer intent from code style alone.
- Do not treat a refactor as a decision unless the evidence says it was one.
- Do not cite omitted IDs, graph keys, confidence, or status.
"""


@dataclass(frozen=True)
class PackSection:
    title: str
    text: str
    evidence_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    category: str = ""


@dataclass
class EvidencePack:
    bundle: PrEvidenceBundle
    candidate: Candidate
    sections: list[PackSection] = field(default_factory=list)
    target_options: list[TargetOption] = field(default_factory=list)
    target_text: str = ""
    omitted_ids: list[str] = field(default_factory=list)
    max_tokens: int = 6000
    estimated_tokens: int = 0
    regions: list[RegionContext] = field(default_factory=list)
    alias_map: dict[str, str] = field(default_factory=dict)
    budget_usage: dict[str, int] = field(default_factory=dict)
    budget_allocation: dict[str, int] = field(default_factory=dict)
    header_text: str = ""
    rules_text: str = _RULES

    def evidence_ids(self) -> list[str]:
        return sorted(
            {
                evidence_id
                for section in self.sections
                for evidence_id in section.evidence_ids
            }
        )

    def resolve_citation(self, value: str) -> str:
        """Map ``E1``/``[E1]`` to its record ID; pass record IDs through."""
        candidate = value.strip().strip("[]() ,.;")
        if candidate in self.alias_map:
            return self.alias_map[candidate]
        upper = candidate.upper()
        if upper in self.alias_map:
            return self.alias_map[upper]
        return candidate

    def text(self) -> str:
        parts: list[str] = [self.header_text or _header(self)]
        if self.target_text:
            parts.append("TARGETS (copy an exact identifier; do not invent keys)")
            parts.append(self.target_text)
        parts.append("EVIDENCE (cite only these IDs; use the [E#] label or exact ID)")
        for section in self.sections:
            label = ", ".join(section.aliases)
            heading = f"### [{label}] {section.title}" if label else f"### {section.title}"
            parts.append(heading)
            parts.append(section.text)
        parts.append(self.rules_text)
        return "\n\n".join(part for part in parts if part)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.bundle.repo,
            "pr_number": self.bundle.pr_number,
            "candidate": self.candidate.as_dict(),
            "evidence_ids": self.evidence_ids(),
            "omitted_ids": list(self.omitted_ids),
            "aliases": dict(self.alias_map),
            "targets": [option.model_dump(mode="json") for option in self.target_options],
            "regions": [
                {
                    "path": region.path,
                    "start_line": region.start_line,
                    "end_line": region.end_line,
                    "old_start": region.old_start,
                    "old_end": region.old_end,
                    "comment_ids": list(region.comment_ids),
                }
                for region in self.regions
            ],
            "budget_allocation": dict(self.budget_allocation),
            "budget_usage": dict(self.budget_usage),
            "estimated_tokens": self.estimated_tokens,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class _DiffSelection:
    item: EvidenceItem
    regions: tuple[RegionContext, ...]

    @property
    def path(self) -> str:
        return str(self.item.payload.get("path") or "")


class ContextPacker:
    def __init__(
        self,
        *,
        max_tokens: int = 6000,
        max_target_options: int = 80,
        max_diff_hunks: int = 6,
        max_diff_lines_per_hunk: int = 40,
        max_doc_characters: int = 4000,
        max_regions: int = 4,
        max_review_comments: int = 8,
        max_reviews: int = 3,
        max_commit_messages: int = 3,
        max_issues: int = 3,
        max_issue_comments: int = 4,
        max_docs: int = 3,
        max_code_lines: int = 24,
    ) -> None:
        self.budget = TokenBudget(max_tokens=max_tokens)
        self.max_target_options = max_target_options
        self.max_diff_hunks = max_diff_hunks
        self.max_diff_lines_per_hunk = max_diff_lines_per_hunk
        self.max_doc_characters = max_doc_characters
        self.max_regions = max_regions
        self.max_review_comments = max_review_comments
        self.max_reviews = max_reviews
        self.max_commit_messages = max_commit_messages
        self.max_issues = max_issues
        self.max_issue_comments = max_issue_comments
        self.max_docs = max_docs
        self.max_code_lines = max_code_lines

    def pack(
        self,
        bundle: PrEvidenceBundle,
        candidate: Candidate,
        *,
        target_options: list[TargetOption] | None = None,
    ) -> EvidencePack:
        options = self._relevant_targets(bundle, target_options or [])
        change_files = self._select_change_files(bundle, candidate)
        regions = build_region_contexts(
            change_files,
            bundle.review_comments,
            max_regions=min(self.max_regions, self.max_diff_hunks),
            max_lines_per_hunk=self.max_diff_lines_per_hunk,
            max_code_lines=self.max_code_lines,
        )
        diffs = self._diff_selections(change_files, regions)
        review_comments = self._select_review_comments(bundle, regions)
        reviews = self._select_reviews(bundle)
        commits = self._select_commits(bundle)
        issues = self._select_issues(bundle)
        issue_comments, omitted_issue_comments = self._select_issue_comments(bundle)
        docs = self._select_docs(bundle)

        header = _header_from(bundle, candidate)
        overhead = self.budget.estimate(header) + self.budget.estimate(_RULES) + 30
        available = max(1, self.budget.max_tokens - overhead)
        present = {
            name
            for name, items in (
                ("diff", diffs),
                ("review_comments", review_comments or reviews),
                ("commit_messages", commits),
                ("pr_description", [bundle.pr]),
                ("issue_context", issues or issue_comments),
                ("docs", docs),
                ("targets", options),
            )
            if items
        }
        allocation = allocate_budget(available, present)

        builder = _PackBuilder(
            budget=self.budget,
            allocation=allocation,
            header=header,
            rules=_RULES,
        )
        self._add_diff_sections(builder, diffs)
        self._add_review_sections(builder, review_comments)
        self._add_review_body_sections(builder, reviews)
        self._add_commit_sections(builder, commits)
        self._add_pr_section(builder, bundle.pr)
        self._add_issue_sections(builder, issues)
        self._add_issue_discussion(
            builder,
            issue_comments,
            omitted_count=omitted_issue_comments,
            issue=bundle.issues[0] if bundle.issues else None,
        )
        self._add_doc_sections(builder, docs)

        target_text = ""
        target_budget = allocation.get("targets", 0)
        if options and target_budget > 0:
            target_text = self._render_targets(options, max(1, target_budget - 12))
            if target_text:
                builder.usage["targets"] = self.budget.estimate(target_text) + 12

        all_ids = {item.record_id for item in bundle.all_items()}
        pack = EvidencePack(
            bundle=bundle,
            candidate=candidate,
            sections=builder.sections,
            target_options=options,
            target_text=target_text,
            omitted_ids=sorted(all_ids - builder.included),
            max_tokens=self.budget.max_tokens,
            regions=builder.regions,
            alias_map=builder.alias_map,
            budget_usage=builder.usage,
            budget_allocation=allocation,
            header_text=header,
            rules_text=_RULES,
        )
        self._enforce_budget(pack, all_ids)
        pack.estimated_tokens = self.budget.estimate(pack.text())
        return pack

    # -- selection ------------------------------------------------------
    def _select_change_files(
        self, bundle: PrEvidenceBundle, candidate: Candidate
    ) -> list[EvidenceItem]:
        candidate_ids = set(candidate.evidence_ids)
        anchored_paths = {
            str(item.payload.get("path"))
            for item in bundle.review_comments
            if item.payload.get("path")
        }
        files = [
            item
            for item in bundle.files
            if self._include_changed_file(item, bundle, candidate_ids)
        ]
        non_test = [
            item
            for item in files
            if not is_test_path(str(item.payload.get("path") or ""))
        ]
        if non_test:
            # Unrelated test files never crowd out the code under change. A
            # test file is kept only when a review comment is anchored to it.
            files = [
                item
                for item in files
                if not is_test_path(str(item.payload.get("path") or ""))
                or str(item.payload.get("path")) in anchored_paths
            ]
        return sorted(files, key=lambda item: _file_priority(item, candidate_ids, anchored_paths))

    def _include_changed_file(
        self,
        item: EvidenceItem,
        bundle: PrEvidenceBundle,
        candidate_ids: set[str],
    ) -> bool:
        del candidate_ids  # candidate score is not a direct link to a README
        path = str(item.payload.get("path") or "")
        if not path or is_ci_path(path):
            return False
        if is_readme_path(path):
            return self._directly_linked(item, bundle)
        return True

    def _directly_linked(
        self,
        item: EvidenceItem,
        bundle: PrEvidenceBundle,
    ) -> bool:
        path = item.payload.get("path")
        if not isinstance(path, str) or not path:
            return False
        if any(
            comment.payload.get("path") == path for comment in bundle.review_comments
        ):
            return True
        texts = [
            bundle.pr.payload.get("body"),
            *(issue.payload.get("body") for issue in bundle.issues),
            *(comment.payload.get("body") for comment in bundle.issue_comments),
            *(review.payload.get("body") for review in bundle.reviews),
        ]
        return any(isinstance(text, str) and path in text for text in texts)

    def _diff_selections(
        self,
        files: list[EvidenceItem],
        regions: list[RegionContext],
    ) -> list[_DiffSelection]:
        by_path: dict[str, list[RegionContext]] = {}
        for region in regions:
            by_path.setdefault(region.path, []).append(region)
        return [
            _DiffSelection(item=item, regions=tuple(by_path[path]))
            for item in files
            if (path := str(item.payload.get("path") or "")) in by_path
        ]

    def _select_review_comments(
        self, bundle: PrEvidenceBundle, regions: list[RegionContext]
    ) -> list[EvidenceItem]:
        selected_paths = {region.path for region in regions}

        def anchored(item: EvidenceItem) -> bool:
            path = item.payload.get("path")
            line = item.payload.get("line")
            if not isinstance(path, str) or not isinstance(line, int):
                line = item.payload.get("start_line")
            if not isinstance(path, str) or not isinstance(line, int):
                return False
            side = str(item.payload.get("side") or "RIGHT")
            return any(
                region.path == path and region.contains_line(line, side=side)
                for region in regions
            )

        def includable(item: EvidenceItem) -> bool:
            path = str(item.payload.get("path") or "")
            if not path:
                return True
            if is_ci_path(path):
                return False
            if is_test_path(path):
                return path in selected_paths
            if is_readme_path(path):
                return path in selected_paths
            return True

        anchored_items = sorted(
            (item for item in bundle.review_comments if anchored(item)),
            key=lambda item: (
                str(item.payload.get("path") or ""),
                _line_number(item),
                item.record_id,
            ),
        )
        other_items = sorted(
            (
                item
                for item in bundle.review_comments
                if not anchored(item) and includable(item)
            ),
            key=lambda item: (str(item.payload.get("created_at") or ""), item.record_id),
        )
        return (anchored_items + other_items)[: self.max_review_comments]

    def _select_reviews(self, bundle: PrEvidenceBundle) -> list[EvidenceItem]:
        return sorted(
            bundle.reviews,
            key=lambda item: (
                str(item.payload.get("submitted_at") or ""),
                item.record_id,
            ),
        )[: self.max_reviews]

    def _select_commits(self, bundle: PrEvidenceBundle) -> list[EvidenceItem]:
        commits = sorted(
            bundle.commits,
            key=lambda item: (
                str(item.payload.get("committed_at") or ""),
                item.record_id,
            ),
            reverse=True,
        )
        if not commits:
            return []
        head = bundle.latest_commit()
        ordered: list[EvidenceItem] = []
        if head is not None:
            ordered.append(head)
        rationale = [
            item
            for item in commits
            if item is not head and _has_rationale(item)
        ]
        ordered.extend(rationale)
        ordered.extend(item for item in commits if item not in ordered)
        return ordered[: self.max_commit_messages]

    def _select_issues(self, bundle: PrEvidenceBundle) -> list[EvidenceItem]:
        return sorted(
            bundle.issues,
            key=lambda item: (
                str(item.payload.get("number") or ""),
                item.record_id,
            ),
        )[: self.max_issues]

    def _select_issue_comments(
        self, bundle: PrEvidenceBundle
    ) -> tuple[list[EvidenceItem], int]:
        comments = [
            item
            for item in bundle.issue_comments
            if item.payload.get("issue_number") is not None
        ]
        ranked = sorted(
            comments,
            key=lambda item: (
                0 if _contains_rationale(item) else 1,
                str(item.payload.get("created_at") or ""),
                item.record_id,
            ),
        )
        return ranked[: self.max_issue_comments], max(
            0, len(ranked) - self.max_issue_comments
        )

    def _select_docs(
        self, bundle: PrEvidenceBundle
    ) -> list[EvidenceItem]:
        docs = [
            item
            for item in bundle.docs
            if not is_readme_path(str(item.payload.get("path") or ""))
            or self._directly_linked(item, bundle)
        ]
        return sorted(
            docs, key=lambda item: (str(item.payload.get("path") or ""), item.record_id)
        )[: self.max_docs]

    # -- target catalogue ----------------------------------------------
    def _relevant_targets(
        self, bundle: PrEvidenceBundle, options: list[TargetOption]
    ) -> list[TargetOption]:
        changed = bundle.changed_paths()
        # A target with no path cannot be judged, and a config key is
        # cross-cutting: the changed code may read a key whose defining file
        # was not touched. Everything else is offered only when it lives in a
        # file this PR actually changed.
        relevant = [
            option
            for option in options
            if option.type is TargetType.CONFIG_KEY
            or not option.path
            or option.path in changed
        ]
        relevant.sort(
            key=lambda option: (
                option.type.value,
                option.path or "",
                option.start_line or 0,
                option.identifier,
            )
        )
        return relevant[: self.max_target_options]

    def _render_targets(self, options: list[TargetOption], max_tokens: int) -> str:
        if not options:
            return ""
        lines = [option.prompt_line() for option in options]
        return self.budget.truncate_lines(lines, max_tokens)

    # -- section builders ----------------------------------------------
    def _add_diff_sections(
        self, builder: _PackBuilder, diffs: list[_DiffSelection]
    ) -> None:
        for diff in diffs:
            added = builder.add(
                "diff",
                (diff.item.record_id,),
                f"{diff.item.record_id} — changed_file {diff.path}",
                _constant(partial(_render_diff, diff)),
                cap=ITEM_TOKEN_CAPS["diff"],
            )
            if added:
                builder.regions.extend(diff.regions)

    def _add_review_sections(
        self, builder: _PackBuilder, comments: list[EvidenceItem]
    ) -> None:
        for item in comments:
            builder.add(
                "review_comments",
                (item.record_id,),
                _section_title(item),
                _constant(partial(_render_review_comment, item)),
                cap=ITEM_TOKEN_CAPS["review_comments"],
            )

    def _add_review_body_sections(
        self, builder: _PackBuilder, reviews: list[EvidenceItem]
    ) -> None:
        for item in reviews:
            builder.add(
                "review_comments",
                (item.record_id,),
                _section_title(item),
                _constant(partial(_render_review, item)),
                cap=ITEM_TOKEN_CAPS["review_comments"],
            )

    def _add_commit_sections(
        self, builder: _PackBuilder, commits: list[EvidenceItem]
    ) -> None:
        for item in commits:
            builder.add(
                "commit_messages",
                (item.record_id,),
                _section_title(item),
                _constant(partial(_render_commit, item)),
                cap=ITEM_TOKEN_CAPS["commit_messages"],
            )

    def _add_pr_section(self, builder: _PackBuilder, item: EvidenceItem) -> None:
        builder.add(
            "pr_description",
            (item.record_id,),
            _section_title(item),
            _constant(partial(_render_pr, item)),
            cap=ITEM_TOKEN_CAPS["pr_description"],
        )

    def _add_issue_sections(
        self, builder: _PackBuilder, issues: list[EvidenceItem]
    ) -> None:
        for item in issues:
            builder.add(
                "issue_context",
                (item.record_id,),
                _section_title(item),
                _constant(partial(_render_issue, item)),
                cap=ITEM_TOKEN_CAPS["issue_context"],
            )

    def _add_issue_discussion(
        self,
        builder: _PackBuilder,
        comments: list[EvidenceItem],
        *,
        omitted_count: int,
        issue: EvidenceItem | None,
    ) -> None:
        if not comments:
            return
        new_comments = [
            item for item in comments if item.record_id not in builder.included
        ]
        if not new_comments:
            return
        issue_number = (
            issue.payload.get("number") if issue is not None else None
        )
        title = f"issue_discussion: issue #{issue_number}" if issue_number else "issue_discussion"

        def render(labels: dict[str, str]) -> str:
            return _render_issue_discussion(
                new_comments, labels, omitted_count=omitted_count
            )

        builder.add(
            "issue_context",
            tuple(item.record_id for item in new_comments),
            title,
            render,
            cap=ITEM_TOKEN_CAPS["issue_context"],
        )

    def _add_doc_sections(
        self, builder: _PackBuilder, docs: list[EvidenceItem]
    ) -> None:
        for item in docs:
            builder.add(
                "docs",
                (item.record_id,),
                _section_title(item),
                _constant(partial(_render_doc, item, self.max_doc_characters)),
                cap=ITEM_TOKEN_CAPS["docs"],
            )

    # -- hard budget ----------------------------------------------------
    def _enforce_budget(self, pack: EvidencePack, all_ids: set[str]) -> None:
        if self.budget.estimate(pack.text()) <= self.budget.max_tokens:
            return
        while pack.sections and self.budget.estimate(pack.text()) > self.budget.max_tokens:
            removed = pack.sections.pop()
            pack.omitted_ids = sorted(set(pack.omitted_ids) | set(removed.evidence_ids))
            for alias, evidence_id in list(pack.alias_map.items()):
                if evidence_id in removed.evidence_ids:
                    del pack.alias_map[alias]
            pack.regions = [
                region
                for region in pack.regions
                if region.path
                not in {
                    _path_from_title(section.title)
                    for section in [removed]
                    if section.category == "diff"
                }
            ]
        if self.budget.estimate(pack.text()) > self.budget.max_tokens:
            pack.target_text = ""
        if self.budget.estimate(pack.text()) > self.budget.max_tokens:
            pack.rules_text = ""
        if self.budget.estimate(pack.text()) > self.budget.max_tokens:
            pack.header_text = self.budget.truncate(
                pack.header_text, max(1, self.budget.max_tokens - 2)
            )
        pack.omitted_ids = sorted(set(pack.omitted_ids) & all_ids)

    def __repr__(self) -> str:
        return f"ContextPacker(max_tokens={self.budget.max_tokens})"


class _PackBuilder:
    """Assign evidence aliases and spend category budgets in section order."""

    def __init__(
        self,
        *,
        budget: TokenBudget,
        allocation: dict[str, int],
        header: str,
        rules: str,
    ) -> None:
        self.budget = budget
        self.allocation = allocation
        self.header = header
        self.rules = rules
        self.sections: list[PackSection] = []
        self.included: set[str] = set()
        self.alias_map: dict[str, str] = {}
        self.usage: dict[str, int] = {}
        self.regions: list[RegionContext] = []
        self._alias_next = 1

    def add(
        self,
        category: str,
        evidence_ids: tuple[str, ...],
        title: str,
        render: Callable[[dict[str, str]], str],
        *,
        cap: int,
    ) -> bool:
        remaining = self.allocation.get(category, 0) - self.usage.get(category, 0)
        if remaining < MIN_SECTION_TOKENS:
            return False
        new_ids = tuple(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id and evidence_id not in self.included
        )
        if not new_ids:
            return False
        start = self._alias_next
        labels = tuple(f"E{start + index}" for index in range(len(new_ids)))
        text = render(dict(zip(new_ids, labels, strict=True)))
        if not text:
            return False
        text = self.budget.truncate(
            text, min(remaining, max(MIN_SECTION_TOKENS, cap))
        )
        cost = self.budget.estimate(text) + 12
        if cost > remaining:
            text = self.budget.truncate(text, max(1, remaining - 12))
            cost = self.budget.estimate(text) + 12
        if not text or cost > remaining:
            return False
        self.sections.append(
            PackSection(
                title=title,
                text=text,
                evidence_ids=new_ids,
                aliases=labels,
                category=category,
            )
        )
        for label, evidence_id in zip(labels, new_ids, strict=True):
            self.alias_map[label] = evidence_id
        self.included.update(new_ids)
        self._alias_next = start + len(new_ids)
        self.usage[category] = self.usage.get(category, 0) + cost
        return True


# -- rendering ----------------------------------------------------------
def _constant(render: Callable[[], str]) -> Callable[[dict[str, str]], str]:
    """Adapt a label-free renderer to the section-builder signature."""

    def wrapper(_labels: dict[str, str]) -> str:
        return render()

    return wrapper


def _header_from(bundle: PrEvidenceBundle, candidate: Candidate) -> str:
    pr = bundle.pr.payload
    return (
        "TASK: Extract only decisions supported by the evidence below.\n"
        f"REPOSITORY: {bundle.repo}\n"
        f"PULL_REQUEST: #{bundle.pr_number}\n"
        f"TITLE: {pr.get('title', '')}\n"
        f"MERGED_AT: {pr.get('merged_at', '')}\n"
        f"CANDIDATE: {candidate.candidate_class.value} score={candidate.score}\n"
        f"WHY THIS PR WAS SELECTED: {'; '.join(candidate.reasons)}"
    )


def _header(pack: EvidencePack) -> str:
    return _header_from(pack.bundle, pack.candidate)


def _render_diff(diff: _DiffSelection) -> str:
    lines: list[str] = ["TARGET_CODE_REGION:"]
    for region in diff.regions:
        lines.append(f"File: {region.path}")
        lines.append(f"Lines: {region.start_line}-{region.end_line}")
        if region.current_code:
            lines.append("Current code:")
            lines.append(region.current_code)
        if region.previous_code:
            lines.append("Previous code:")
            lines.append(region.previous_code)
        else:
            lines.append("Previous code: (no previous lines in this hunk)")
        lines.append("")
    lines.append("DIFF:")
    lines.extend(region.diff_text for region in diff.regions)
    return "\n".join(lines).strip()


def _render_pr(item: EvidenceItem) -> str:
    payload = item.payload
    return (
        f"PR #{payload.get('number')}: {payload.get('title', '')}\n"
        f"author={payload.get('author')} merged_at={payload.get('merged_at')}\n"
        f"url={payload.get('url')}\n"
        f"{str(payload.get('body') or '')[:2000]}"
    )


def _render_commit(item: EvidenceItem) -> str:
    payload = item.payload
    return (
        f"commit {payload.get('sha')} by {payload.get('author')} "
        f"at {payload.get('committed_at')}\n"
        f"{str(payload.get('message') or '')[:1500]}"
    )


def _render_review_comment(item: EvidenceItem) -> str:
    payload = item.payload
    anchor = payload.get("line") or payload.get("start_line")
    return (
        f"review_comment by {payload.get('author')} on "
        f"{payload.get('path')}:{anchor} side={payload.get('side')} "
        f"at {payload.get('created_at')}\n"
        f"{str(payload.get('body') or '')[:1500]}"
    )


def _render_review(item: EvidenceItem) -> str:
    payload = item.payload
    return (
        f"review {payload.get('state')} by {payload.get('author')} "
        f"at {payload.get('submitted_at')}\n"
        f"{str(payload.get('body') or '')[:1200]}"
    )


def _render_issue(item: EvidenceItem) -> str:
    payload = item.payload
    return (
        f"issue #{payload.get('number')}: {payload.get('title', '')} "
        f"state={payload.get('state')} by {payload.get('author')}\n"
        f"url={payload.get('url')}\n"
        f"{str(payload.get('body') or '')[:1500]}"
    )


def _render_issue_discussion(
    comments: list[EvidenceItem],
    labels: dict[str, str],
    *,
    omitted_count: int,
) -> str:
    lines: list[str] = []
    for item in comments:
        label = labels.get(item.record_id, "?")
        payload = item.payload
        lines.append(
            f"[{label}] {item.record_id} by {payload.get('author')} "
            f"at {payload.get('created_at')}:"
        )
        lines.append(str(payload.get("body") or "")[:600])
    if omitted_count > 0:
        lines.append(
            f"[... {omitted_count} lower-signal comment(s) omitted from this thread ...]"
        )
    return "\n".join(lines)


def _render_doc(item: EvidenceItem, max_characters: int) -> str:
    payload = item.payload
    content = str(payload.get("content") or "")
    if len(content) > max_characters:
        content = content[:max_characters] + "\n[... doc truncated ...]"
    return f"doc {payload.get('path')}\n{content}"


def _section_title(item: EvidenceItem) -> str:
    payload = item.payload
    if item.record_type == "changed_file":
        return f"{item.record_id} — changed_file {payload.get('path')}"
    if item.record_type == "review_comment":
        return (
            f"{item.record_id} — review_comment "
            f"{payload.get('path')}:{payload.get('line')}"
        )
    if item.record_type == "issue":
        return f"{item.record_id} — issue #{payload.get('number')}"
    if item.record_type == "doc_file":
        return f"{item.record_id} — doc {payload.get('path')}"
    return f"{item.record_id} — {item.record_type}"


def _line_number(item: EvidenceItem) -> int:
    line = item.payload.get("line")
    if isinstance(line, int):
        return line
    start = item.payload.get("start_line")
    return start if isinstance(start, int) else 0


def _contains_rationale(item: EvidenceItem) -> bool:
    text = str(item.payload.get("body") or "").lower()
    return any(marker in text for marker in RATIONAL_MARKERS)


def _has_rationale(item: EvidenceItem) -> bool:
    text = str(item.payload.get("message") or "").lower()
    return any(marker in text for marker in RATIONAL_MARKERS)


def _file_priority(
    item: EvidenceItem,
    candidate_ids: set[str],
    anchored_paths: set[str],
) -> tuple[int, int, int, str]:
    path = str(item.payload.get("path") or "")
    return (
        0 if path in anchored_paths else 1,
        0 if item.record_id in candidate_ids else 1,
        1 if is_test_path(path) else 0,
        path,
    )


def _path_from_title(title: str) -> str:
    marker = "changed_file "
    if marker in title:
        return title.split(marker, 1)[1]
    return ""


__all__ = ["ContextPacker", "EvidencePack", "PackSection"]
