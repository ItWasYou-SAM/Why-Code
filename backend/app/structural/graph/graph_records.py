"""Structural graph records.

The structural half of the graph is built as an explicit list of nodes and
relationships before any Cypher runs. Every relationship is created from a
fact present in a ``StructuralFile``; there is no inference step here.

Identity rules (all path@commit scoped):

    File        {short}:file:{path}@{sha}
    Symbol      {short}:symbol:{path}@{sha}:{qualname}
    CodeRegion  {short}:region:{path}@{sha}:{start}:{end}
    ConfigKey   {short}:config:{source}:{key}
    ExternalRef {short}:external:{module}
    Doc         {short}:doc:{path}
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidRecordError
from app.structural.extraction.ids import (
    config_key_id,
    doc_node_id,
    external_ref_id,
    file_id,
    region_id,
    symbol_id,
)
from app.structural.extraction.models import DocMention, StructuralBatch, StructuralFile


@dataclass(frozen=True)
class GraphNode:
    label: str
    node_id: str
    props: dict[str, Any]


@dataclass(frozen=True)
class GraphEdge:
    rel_type: str
    from_id: str
    to_id: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuralGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes}

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = {}
        edges: dict[str, int] = {}
        for node in self.nodes:
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for edge in self.edges:
            edges[edge.rel_type] = edges.get(edge.rel_type, 0) + 1
        return {"nodes": nodes, "edges": edges}

    def assert_integrity(self) -> None:
        known = self.node_ids()
        for edge in self.edges:
            for endpoint in (edge.from_id, edge.to_id):
                if endpoint not in known:
                    raise InvalidRecordError(
                        f"{edge.rel_type} references missing structural node {endpoint}",
                        component="structural",
                        record_id=endpoint,
                    )


def build_structural_graph(batch: StructuralBatch) -> StructuralGraph:
    builder = _Builder(batch.repo)
    for entry in batch.files:
        builder.register_file(entry)
    for entry in batch.files:
        builder.add_file_facts(entry)
    builder.add_docs(batch.docs)
    builder.graph.skipped = list(batch.skipped)
    builder.graph.assert_integrity()
    return builder.graph


class _Builder:
    """Two-pass builder: every File node exists before import edges resolve."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.graph = StructuralGraph()
        self._files_by_path: dict[str, list[str]] = {}
        self._symbol_by_qualname: dict[tuple[str, str], str] = {}
        self._declared_keys: dict[str, str] = {}

    # -- pass 1: files ---------------------------------------------------
    def register_file(self, entry: StructuralFile) -> None:
        fid = entry.file_id
        self._node(
            "File",
            fid,
            {
                "path": entry.path,
                "commit_sha": entry.commit_sha,
                "language": entry.language.value,
                "blob_sha": entry.blob_sha,
                "source_pr_numbers": entry.source_pr_numbers,
            },
        )
        self._files_by_path.setdefault(entry.path, []).append(fid)

    # -- pass 2: facts ---------------------------------------------------
    def add_file_facts(self, entry: StructuralFile) -> None:
        fid = entry.file_id

        for symbol in entry.symbols:
            self._node(
                "Symbol",
                symbol.symbol_id,
                {
                    "name": symbol.name,
                    "qualname": symbol.qualname,
                    "kind": symbol.kind.value,
                    "path": symbol.path,
                    "commit_sha": symbol.commit_sha,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "parent_qualname": symbol.parent_qualname,
                    "decorators": symbol.decorators,
                    "is_async": symbol.is_async,
                },
            )
            self._symbol_by_qualname[(entry.path, symbol.qualname)] = symbol.symbol_id
            self._edge("DEFINES", fid, symbol.symbol_id)

        for region in entry.regions:
            self._node(
                "CodeRegion",
                region.region_id,
                {
                    "path": region.path,
                    "commit_sha": region.commit_sha,
                    "start_line": region.start_line,
                    "end_line": region.end_line,
                    "kind": region.kind,
                },
            )
            self._edge("HAS_REGION", fid, region.region_id)
            if region.symbol_qualname:
                owner_id = self._symbol_by_qualname.get(
                    (entry.path, region.symbol_qualname)
                )
                if owner_id:
                    self._edge("HAS_REGION", owner_id, region.region_id)

        for imported in entry.imports:
            if imported.resolved_path:
                for target in self._files_by_path.get(imported.resolved_path, []):
                    self._edge(
                        "IMPORTS",
                        fid,
                        target,
                        {
                            "module": imported.module,
                            "level": imported.level,
                            "names": imported.names,
                        },
                    )

        for external in entry.external_refs:
            eid = external_ref_id(repo=self.repo, module=external.module)
            self._node("ExternalRef", eid, {"module": external.module})
            self._edge("IMPORTS", fid, eid)

        for key in entry.config_keys:
            kid = config_key_id(repo=self.repo, source=entry.path, key=key.key)
            self._node(
                "ConfigKey",
                kid,
                {
                    "key": key.key,
                    "source_path": key.source_path,
                    "source_kind": key.source_kind.value,
                    "line": key.line,
                    "value_preview": key.value_preview,
                },
            )
            self._declared_keys[key.key] = kid
            self._edge("DECLARES", fid, kid)

        for reference in entry.config_references:
            existing = self._declared_keys.get(reference.key)
            if existing is None:
                kid = config_key_id(
                    repo=self.repo, source="environment", key=reference.key
                )
                self._node(
                    "ConfigKey",
                    kid,
                    {
                        "key": reference.key,
                        "source_path": None,
                        "source_kind": reference.source_kind.value,
                        "line": None,
                        "value_preview": None,
                    },
                )
                self._declared_keys[reference.key] = kid
            else:
                kid = existing
            if reference.symbol_qualname:
                sid = self._symbol_by_qualname.get((entry.path, reference.symbol_qualname))
                if sid:
                    self._edge("USED_BY", kid, sid)

    def add_docs(self, docs: Iterable[DocMention]) -> None:
        for mention in docs:
            did = doc_node_id(repo=self.repo, path=mention.doc_path)
            self._node("Doc", did, {"path": mention.doc_path})
            if mention.target_path:
                for fid in self._files_by_path.get(mention.target_path, []):
                    self._edge("DOCUMENTED_BY", fid, did)
            if mention.config_key:
                kid = self._declared_keys.get(mention.config_key)
                if kid:
                    self._edge("DOCUMENTED_BY", kid, did)

    # -- helpers --------------------------------------------------------
    def _node(self, label: str, node_id: str, props: dict[str, Any]) -> None:
        if any(node.node_id == node_id for node in self.graph.nodes):
            return
        self.graph.nodes.append(
            GraphNode(
                label=label,
                node_id=node_id,
                props={**props, "record_id": node_id, "repo": self.repo},
            )
        )

    def _edge(
        self, rel_type: str, from_id: str, to_id: str, props: dict[str, Any] | None = None
    ) -> None:
        candidate = GraphEdge(
            rel_type=rel_type, from_id=from_id, to_id=to_id, props=props or {}
        )
        if candidate not in self.graph.edges:
            self.graph.edges.append(candidate)


__all__ = [
    "GraphEdge",
    "GraphNode",
    "StructuralGraph",
    "build_structural_graph",
    "file_id",
    "region_id",
    "symbol_id",
]
