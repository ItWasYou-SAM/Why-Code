"""Graph-link validation.

A target is written only if its exact graph key already exists or a unique,
path/commit-filtered alias resolves to it. Ambiguity is an error, never a
coin flip. The index is built from the same normalized and structural
records that were loaded into Neo4j, and can be replaced by a live check in
the writer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.evidence_ids import code_region_id
from app.domain.records import EvidenceRecord
from app.graph.ops import LABEL_BY_TYPE
from app.graph.records import GraphNode, GraphRecords
from app.graph.regions import parse_patch_hunks
from app.intent.llm.schemas import CandidateTarget
from app.intent.models import ResolvedTarget, TargetOption, TargetType
from app.intent.records import (
    payload_of,
    pr_number_of,
    record_id_of,
    record_type_of,
)
from app.structural.extraction.ids import region_id as structural_region_id

RecordLike = EvidenceRecord | dict[str, Any]

_TARGET_TYPES = {
    "file": TargetType.FILE,
    "symbol": TargetType.SYMBOL,
    "config_key": TargetType.CONFIG_KEY,
    "code_region": TargetType.CODE_REGION,
}


@dataclass(frozen=True)
class TargetResolution:
    target: ResolvedTarget | None
    code: str | None = None
    message: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.target is not None


@dataclass
class TargetValidation:
    targets: list[ResolvedTarget] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and bool(self.targets)


class GraphIndex:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.node_ids: set[str] = set()
        self.node_labels: dict[str, str] = {}
        self.source_record_ids: set[str] = set()
        self._options: dict[str, TargetOption] = {}
        self._aliases: dict[tuple[TargetType, str], list[str]] = {}

    @classmethod
    def from_sources(
        cls,
        *,
        repo: str,
        normalized_records: Iterable[RecordLike] = (),
        structural_records: GraphRecords | Iterable[GraphNode] | None = None,
    ) -> GraphIndex:
        index = cls(repo)
        index.add_normalized_records(normalized_records)
        if structural_records is not None:
            index.add_structural_records(structural_records)
        return index

    # -- normalized evidence -------------------------------------------
    def add_normalized_records(self, records: Iterable[RecordLike]) -> None:
        materialized = list(records)
        commit_by_pr = _latest_commit_by_pr(materialized)
        for record in materialized:
            record_id = record_id_of(record)
            record_type = record_type_of(record)
            label = LABEL_BY_TYPE.get(record_type)
            if label is None:
                continue
            self.node_ids.add(record_id)
            self.node_labels[record_id] = label
            self.source_record_ids.add(record_id)

        for record in materialized:
            record_type = record_type_of(record)
            if record_type == "changed_file":
                self._add_changed_file_target(record)
                self._add_hunk_regions(record, commit_by_pr)
            elif record_type == "review_comment":
                self._add_review_region(record, commit_by_pr)

    def _add_changed_file_target(self, record: RecordLike) -> None:
        """Allow ``file`` targets named by the PR's changed-file set.

        The structural pass normally supplies a commit-scoped ``File`` node.
        When it does not, the normalized ``ChangedFile`` record is still a
        real graph node the model saw, so it can serve as the fallback key.
        """

        record_id = record_id_of(record)
        path = payload_of(record).get("path")
        if not isinstance(path, str) or not path:
            return
        self.add_target(
            TargetOption(
                type=TargetType.FILE,
                identifier=path,
                graph_key=record_id,
                path=path,
            )
        )
        self._alias(TargetType.FILE, path, record_id)

    def _add_hunk_regions(
        self, record: RecordLike, commit_by_pr: Mapping[int, str | None]
    ) -> None:
        pr_number = pr_number_of(record)
        if not isinstance(pr_number, int):
            return
        sha = commit_by_pr.get(pr_number)
        payload = payload_of(record)
        path = payload.get("path")
        patch = payload.get("patch")
        if not isinstance(path, str) or not path or not sha:
            return
        for hunk in parse_patch_hunks(patch if isinstance(patch, str) else None):
            region = structural_region_id(
                repo=self.repo,
                path=path,
                commit_sha=sha,
                start_line=hunk.start_line,
                end_line=hunk.end_line,
            )
            self.node_ids.add(region)
            self.node_labels[region] = "CodeRegion"
            self.add_target(
                TargetOption(
                    type=TargetType.CODE_REGION,
                    identifier=region,
                    graph_key=region,
                    path=path,
                    start_line=hunk.start_line,
                    end_line=hunk.end_line,
                    commit_sha=sha,
                )
            )

    def _add_review_region(
        self, record: RecordLike, commit_by_pr: Mapping[int, str | None]
    ) -> None:
        payload = payload_of(record)
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return
        line = payload.get("line")
        start = payload.get("start_line")
        if isinstance(line, int):
            end_line = line
        elif isinstance(start, int):
            end_line = start
        else:
            return
        start_line = start if isinstance(start, int) else end_line
        if start_line > end_line:
            start_line, end_line = end_line, start_line
        region = code_region_id(
            repo=self.repo, path=path, start_line=start_line, line=end_line
        )
        pr_number = pr_number_of(record)
        sha = commit_by_pr.get(pr_number) if isinstance(pr_number, int) else None
        self.node_ids.add(region)
        self.node_labels[region] = "CodeRegion"
        self.add_target(
            TargetOption(
                type=TargetType.CODE_REGION,
                identifier=region,
                graph_key=region,
                path=path,
                start_line=start_line,
                end_line=end_line,
                commit_sha=sha,
            )
        )

    # -- structural records --------------------------------------------
    def add_structural_records(
        self, records: GraphRecords | Iterable[GraphNode]
    ) -> None:
        nodes = (
            records.nodes
            if isinstance(records, GraphRecords)
            else list(records)
        )
        for node in nodes:
            self.node_ids.add(node.key)
            self.node_labels[node.key] = node.label
            if node.label == "File":
                self._add_file(node)
            elif node.label == "Symbol":
                self._add_symbol(node)
            elif node.label == "ConfigKey":
                self._add_config_key(node)
            elif node.label == "CodeRegion":
                self._add_code_region(node)

    def _add_file(self, node: GraphNode) -> None:
        path = _string(node.properties.get("path")) or node.key
        self.add_target(
            TargetOption(
                type=TargetType.FILE,
                identifier=path,
                graph_key=node.key,
                path=path,
                commit_sha=_string(node.properties.get("commit_sha")),
            )
        )
        self._alias(TargetType.FILE, path, node.key)

    def _add_symbol(self, node: GraphNode) -> None:
        qualname = _string(node.properties.get("qualname"))
        qualified = _string(node.properties.get("qualified_name"))
        name = _string(node.properties.get("name"))
        path = _string(node.properties.get("path"))
        identifier = qualified or qualname or name or node.key
        self.add_target(
            TargetOption(
                type=TargetType.SYMBOL,
                identifier=identifier,
                graph_key=node.key,
                path=path,
                start_line=_int(node.properties.get("start_line")),
                end_line=_int(node.properties.get("end_line")),
                name=name,
                commit_sha=_string(node.properties.get("commit_sha")),
            )
        )
        for alias in (qualified, qualname, name):
            if alias:
                self._alias(TargetType.SYMBOL, alias, node.key)

    def _add_config_key(self, node: GraphNode) -> None:
        key_name = _string(node.properties.get("key")) or node.key
        source = _string(node.properties.get("source_path"))
        self.add_target(
            TargetOption(
                type=TargetType.CONFIG_KEY,
                identifier=key_name,
                graph_key=node.key,
                path=source,
                name=key_name,
            )
        )
        self._alias(TargetType.CONFIG_KEY, key_name, node.key)

    def _add_code_region(self, node: GraphNode) -> None:
        path = _string(node.properties.get("path"))
        start = _int(node.properties.get("start_line"))
        end = _int(node.properties.get("end_line"))
        self.add_target(
            TargetOption(
                type=TargetType.CODE_REGION,
                identifier=node.key,
                graph_key=node.key,
                path=path,
                start_line=start,
                end_line=end,
                commit_sha=_string(node.properties.get("commit_sha")),
            )
        )
        if path and start is not None and end is not None:
            self._alias(
                TargetType.CODE_REGION, f"{path}:{start}-{end}", node.key
            )

    # -- registry -------------------------------------------------------
    def add_target(self, option: TargetOption) -> None:
        self._options.setdefault(option.graph_key, option)
        self._alias(option.type, option.graph_key, option.graph_key)

    def _alias(self, target_type: TargetType, alias: str, key: str) -> None:
        bucket = self._aliases.setdefault((target_type, alias), [])
        if key not in bucket:
            bucket.append(key)

    def has_node(self, key: str) -> bool:
        return key in self.node_ids

    def options(self) -> list[TargetOption]:
        return [self._options[key] for key in sorted(self._options)]

    def options_for_paths(self, paths: Iterable[str]) -> list[TargetOption]:
        changed = set(paths)
        selected = [
            option
            for option in self.options()
            if option.type is TargetType.CONFIG_KEY
            or option.path is None
            or option.path in changed
        ]
        return selected

    # -- resolution -----------------------------------------------------
    def resolve(
        self,
        target_type: TargetType | str,
        identifier: str,
        *,
        prefer_paths: Iterable[str] = (),
        prefer_commit: str | None = None,
    ) -> TargetResolution:
        try:
            kind = TargetType(target_type)
        except ValueError:
            return TargetResolution(
                None,
                code="unknown_target_type",
                message=f"unknown target type {target_type!r}",
            )
        identifier = identifier.strip()
        if not identifier:
            return TargetResolution(
                None, code="empty_target", message="target identifier is empty"
            )

        if identifier in self._options and self._options[identifier].type is kind:
            return TargetResolution(
                self._options[identifier].to_resolved("exact")
            )
        if identifier in self.node_ids and identifier not in self._options:
            return TargetResolution(
                None,
                code="target_type_mismatch",
                message=f"{identifier!r} exists but is not a {kind.value}",
            )

        keys = list(self._aliases.get((kind, identifier), []))
        candidates = [self._options[key] for key in keys if key in self._options]
        preferred_paths = set(prefer_paths)
        if preferred_paths:
            filtered = [option for option in candidates if option.path in preferred_paths]
            if filtered:
                candidates = filtered
        if prefer_commit:
            filtered = [
                option
                for option in candidates
                if option.commit_sha == prefer_commit
            ]
            if filtered:
                candidates = filtered
        if len(candidates) == 1:
            return TargetResolution(candidates[0].to_resolved("alias"))
        if len(candidates) > 1:
            return TargetResolution(
                None,
                code="ambiguous_target",
                message=(
                    f"{kind.value} {identifier!r} resolves to "
                    f"{len(candidates)} graph nodes"
                ),
                candidates=tuple(sorted(option.graph_key for option in candidates)),
            )
        return TargetResolution(
            None,
            code="target_not_found",
            message=(
                f"{kind.value} {identifier!r} is not present in the loaded graph"
            ),
        )


class GraphLinkValidator:
    def __init__(self, index: GraphIndex) -> None:
        self.index = index

    def validate_targets(
        self,
        proposed: Iterable[CandidateTarget],
        *,
        prefer_paths: Iterable[str] = (),
        prefer_commit: str | None = None,
    ) -> TargetValidation:
        result = TargetValidation()
        seen: set[str] = set()
        for target in proposed:
            target_type = _TARGET_TYPES.get(target.target_type)
            if target_type is None:
                result.issues.append(
                    f"unknown target type {target.target_type!r}"
                )
                result.dropped.append(
                    f"{target.target_type}:{target.target_id}"
                )
                continue
            resolution = self.index.resolve(
                target_type,
                target.target_id,
                prefer_paths=prefer_paths,
                prefer_commit=prefer_commit,
            )
            if resolution.target is None:
                result.issues.append(resolution.message or "unresolved target")
                result.dropped.append(
                    f"{target.target_type}:{target.target_id}"
                )
                continue
            if resolution.target.graph_key in seen:
                continue
            seen.add(resolution.target.graph_key)
            result.targets.append(resolution.target)
        return result

    def validate_produced_in(
        self,
        candidate_keys: Iterable[str],
    ) -> list[str]:
        """Keep only PR/commit keys that exist; never invent one."""
        return [key for key in candidate_keys if self.index.has_node(key)]


def _latest_commit_by_pr(records: list[RecordLike]) -> dict[int, str | None]:
    latest: dict[int, tuple[str, str]] = {}
    for record in records:
        if record_type_of(record) != "commit":
            continue
        pr_number = pr_number_of(record)
        payload = payload_of(record)
        sha = payload.get("sha")
        if not isinstance(pr_number, int) or not isinstance(sha, str) or not sha:
            continue
        when = str(payload.get("committed_at") or "")
        current = latest.get(pr_number)
        if current is None or when >= current[0]:
            latest[pr_number] = (when, sha)
    return {number: value[1] for number, value in latest.items()}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


__all__ = [
    "GraphIndex",
    "GraphLinkValidator",
    "TargetResolution",
    "TargetValidation",
]
