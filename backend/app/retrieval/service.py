"""The controlled retrieval-and-synthesis pipeline.

There is no agent and no free-form tool loop. The stages are fixed, the
model sees one evidence pack, and every answer passes the citation validator
before it is returned.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

from app.retrieval.answer.answer_synthesizer import AnswerSynthesizer
from app.retrieval.answer.citation_validator import CitationValidator
from app.retrieval.answer.refusal_handler import RefusalHandler
from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePack, EvidencePacker
from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    AskParameters,
    AskResponse,
    DecisionCandidate,
    DecisionState,
    DecisionView,
    EvidenceItem,
    EvidenceStrength,
    EvidenceView,
    GraphContext,
    NearestCandidate,
    PersonView,
    RefusalMode,
    RefusalResult,
    RetrievalQuery,
    RetrievalSource,
    RetrievalTrace,
    ValidationResult,
)
from app.retrieval.ports import (
    AnswerSynthesizerLike,
    EmbeddingProvider,
    GraphBackend,
    LookupBackend,
    TextGenerator,
)
from app.retrieval.question import EntityIndex, QuestionAnalyzer
from app.retrieval.question.query_builder import QueryBuilder
from app.retrieval.retrieval.exact_retriever import ExactRetriever
from app.retrieval.retrieval.fulltext_retriever import FullTextRetriever
from app.retrieval.retrieval.fusion import Fusion
from app.retrieval.retrieval.graph_expander import GraphExpander
from app.retrieval.retrieval.reranker import Reranker
from app.retrieval.retrieval.vector_retriever import VectorRetriever

DEFAULT_CAVEAT = "This answer reflects decisions captured in the ingested PR window."


class RetrievalService:
    def __init__(
        self,
        *,
        backend: GraphBackend,
        embedder: EmbeddingProvider,
        generator: TextGenerator,
        config: RetrievalConfig | None = None,
        index: EntityIndex | None = None,
        entity_llm: TextGenerator | None = None,
        lookup: LookupBackend | None = None,
        synthesizer_factory: (
            Callable[[RetrievalConfig], AnswerSynthesizerLike] | None
        ) = None,
    ) -> None:
        self.backend = backend
        self.config = config or RetrievalConfig()
        self.lookup = lookup
        self.generator = generator
        self.synthesizer_factory = synthesizer_factory
        self.analyzer = QuestionAnalyzer(index=index, llm=entity_llm)
        self.query_builder = QueryBuilder()
        self.exact = ExactRetriever(backend, limit=self.config.exact_limit)
        self.fulltext = FullTextRetriever(backend, limit=self.config.fulltext_limit)
        self.vector = VectorRetriever(
            backend, embedder, limit=self.config.vector_limit
        )
        self.expander = GraphExpander(
            backend, limit=self.config.max_expansion_candidates
        )
        self.fusion = Fusion(self.config)
        self.reranker = Reranker(self.config)
        self.packer = EvidencePacker(self.config)
        self.validator = CitationValidator(self.config)
        self.refusals = RefusalHandler()

    def ask(self, parameters: AskParameters) -> AskResponse:
        repo = self.resolve_repo(parameters.repo)
        config = self.config.for_request(
            max_evidence_items=parameters.max_evidence_items,
            max_answer_tokens=parameters.max_answer_tokens,
        )
        # Rebuild the budget-sensitive stages for this request; the
        # retrievers do not depend on the answer token budget.
        packer = EvidencePacker(config)
        validator = CitationValidator(config)

        understanding = self.analyzer.analyze(parameters.question)
        query = self.query_builder.build(understanding)

        exact, fulltext, vector = self._retrieve_sources(
            repo=repo, query=query
        )
        initial = self.fusion.fuse(
            {
                RetrievalSource.EXACT: exact,
                RetrievalSource.FULLTEXT: fulltext,
                RetrievalSource.VECTOR: vector,
            }
        )
        expansion_pool = [
            item.candidate for item in initial[: config.max_expansion_candidates]
        ]
        contexts_list = self.expander.expand(repo=repo, candidates=expansion_pool)
        contexts: dict[str, GraphContext] = {
            context.decision.decision_id: context for context in contexts_list
        }
        graph_candidates = [
            context.decision.model_copy(
                update={
                    "sources": {
                        **context.decision.sources,
                        RetrievalSource.GRAPH: rank,
                    }
                }
            )
            for rank, context in enumerate(contexts_list, start=1)
        ]
        fused = self.fusion.fuse(
            {
                RetrievalSource.EXACT: exact,
                RetrievalSource.FULLTEXT: fulltext,
                RetrievalSource.VECTOR: vector,
                RetrievalSource.GRAPH: graph_candidates,
            }
        )
        ranked = self.reranker.rerank(
            fused=fused, contexts=contexts, understanding=understanding
        )
        selected = [item.candidate for item in ranked]
        trace = RetrievalTrace(
            exact_matches=len(exact),
            fulltext_candidates=len(fulltext),
            vector_candidates=len(vector),
            graph_candidates=len(graph_candidates),
            fused_candidates=len(fused),
            selected_decisions=len(selected),
        )

        early = self.refusals.decide(
            understanding=understanding, candidates=selected, contexts=contexts
        )
        if early is not None:
            return _from_refusal(
                early,
                trace=trace,
                selected=selected,
                contexts=contexts,
                include_graph_path=parameters.include_graph_path,
                include_evidence=parameters.include_evidence,
            )

        pack = packer.pack(
            understanding=understanding, ranked=ranked, contexts=contexts
        )
        if self.refusals.is_contested(candidates=selected, contexts=contexts):
            contested = self.refusals.contested(pack)
            return _from_refusal(
                contested,
                trace=trace,
                pack=pack,
                selected=selected,
                contexts=contexts,
                include_graph_path=parameters.include_graph_path,
                include_evidence=parameters.include_evidence,
            )

        # Construct synthesis only after every deterministic refusal gate has
        # passed. A question with no candidates must never reach a model or a
        # synthesizer factory, even one that would refuse locally.
        synthesizer = (
            self.synthesizer_factory(config)
            if self.synthesizer_factory is not None
            else AnswerSynthesizer(self.generator, config)
        )
        draft = synthesizer.synthesize(pack=pack, understanding=understanding)
        if draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE:
            return _from_refusal(
                RefusalResult(
                    mode=RefusalMode.NO_DECISION,
                    answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                    answer=draft.answer,
                    caveats=draft.caveats,
                    nearest_candidates=_nearest(selected),
                ),
                trace=trace,
                selected=selected,
                contexts=contexts,
                include_graph_path=parameters.include_graph_path,
                include_evidence=parameters.include_evidence,
            )

        validation = validator.validate(draft, pack)
        if not validation.citation_valid:
            draft = synthesizer.synthesize(
                pack=pack, understanding=understanding, strict=True
            )
            validation = validator.validate(draft, pack)
            if (
                draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
                or not validation.citation_valid
            ):
                return _from_refusal(
                    self.refusals.insufficient(
                        candidates=selected,
                        caveats=_validation_caveats(validation),
                    ),
                    trace=trace,
                    selected=selected,
                    contexts=contexts,
                    citation_valid=False,
                    include_graph_path=parameters.include_graph_path,
                    include_evidence=parameters.include_evidence,
                )
        if (
            draft.answer_status is AnswerStatus.ANSWERED
            and validation.final_confidence is AnswerConfidence.NONE
        ):
            # A cited answer with no confidence is still not a defensible
            # answer; fail closed rather than rendering it with "none".
            return _from_refusal(
                self.refusals.insufficient(
                    candidates=selected,
                    caveats=["The answer model returned no confidence."],
                ),
                trace=trace,
                selected=selected,
                contexts=contexts,
                include_graph_path=parameters.include_graph_path,
                include_evidence=parameters.include_evidence,
            )

        caveats = list(dict.fromkeys([*draft.caveats, DEFAULT_CAVEAT]))
        evidence_views = (
            [_evidence_view(item, pack) for item in pack.evidence]
            if parameters.include_evidence
            else []
        )
        return AskResponse(
            answer_status=draft.answer_status,
            answer=draft.answer,
            confidence=validation.final_confidence,
            decision_ids=[candidate.decision_id for candidate in selected],
            evidence=evidence_views,
            citations=validation.citations,
            graph_path=pack.graph_path if parameters.include_graph_path else [],
            supersession=pack.supersession,
            caveats=caveats,
            retrieval_trace=trace,
            citation_valid=validation.citation_valid,
            evidence_strength=_evidence_strength(pack.evidence),
            decisions=_decision_views(pack.decisions, contexts),
            people=_people_views(pack.evidence),
        )

    def _retrieve_sources(
        self,
        *,
        repo: str,
        query: RetrievalQuery,
    ) -> tuple[list[DecisionCandidate], list[DecisionCandidate], list[DecisionCandidate]]:
        tasks: dict[RetrievalSource, Callable[[], list[DecisionCandidate]]] = {
            RetrievalSource.EXACT: lambda: self.exact.retrieve(
                repo=repo, identifiers=query.exact_identifiers
            ),
            RetrievalSource.FULLTEXT: lambda: self.fulltext.retrieve(
                repo=repo, query=query.fulltext_query
            ),
            RetrievalSource.VECTOR: lambda: self.vector.retrieve(
                repo=repo, text=query.vector_text
            ),
        }
        if not self.config.parallel_retrieval:
            results = {source: task() for source, task in tasks.items()}
        else:
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = {
                    source: pool.submit(task) for source, task in tasks.items()
                }
                results = {
                    source: future.result() for source, future in futures.items()
                }
        return (
            results[RetrievalSource.EXACT],
            results[RetrievalSource.FULLTEXT],
            results[RetrievalSource.VECTOR],
        )

    def resolve_repo(self, repo: str | None) -> str:
        resolved = (repo or self.config.repo or "").strip()
        if not resolved:
            raise ValueError("repo is required")
        return resolved

    def get_decision(self, decision_id: str, *, repo: str | None = None) -> GraphContext | None:
        if self.lookup is None:
            return None
        return self.lookup.get_decision(
            repo=self.resolve_repo(repo), decision_id=decision_id
        )

    def get_evidence(self, evidence_id: str, *, repo: str | None = None) -> EvidenceItem | None:
        if self.lookup is None:
            return None
        return self.lookup.get_evidence(
            repo=self.resolve_repo(repo), evidence_id=evidence_id
        )

    def entity_history(
        self, entity_id: str, *, repo: str | None = None
    ) -> list[GraphContext]:
        if self.lookup is None:
            return []
        return list(
            self.lookup.entity_history(
                repo=self.resolve_repo(repo), entity_id=entity_id
            )
        )


def _from_refusal(
    refusal: RefusalResult,
    *,
    trace: RetrievalTrace,
    pack: EvidencePack | None = None,
    selected: Sequence[DecisionCandidate] = (),
    contexts: dict[str, GraphContext] | None = None,
    citation_valid: bool = True,
    include_graph_path: bool = True,
    include_evidence: bool = True,
) -> AskResponse:
    evidence = (
        [_evidence_view(item, pack) for item in pack.evidence]
        if pack is not None and include_evidence
        else []
    )
    visible_decisions = (
        list(pack.decisions)
        if pack is not None and pack.decisions
        else list(selected)
    )
    return AskResponse(
        answer_status=refusal.answer_status,
        answer=refusal.answer,
        confidence=refusal.confidence,
        decision_ids=refusal.decision_ids,
        evidence=evidence,
        citations=refusal.citations,
        graph_path=(
            pack.graph_path
            if pack is not None and include_graph_path
            else []
        ),
        supersession=pack.supersession if pack is not None else [],
        caveats=refusal.caveats,
        nearest_candidates=refusal.nearest_candidates,
        retrieval_trace=trace,
        citation_valid=citation_valid,
        evidence_strength=(
            _evidence_strength(pack.evidence) if pack is not None else EvidenceStrength.NONE
        ),
        refusal_mode=refusal.mode,
        decisions=_decision_views(visible_decisions, contexts or {}),
        people=_people_views(pack.evidence if pack is not None else []),
    )


def _evidence_view(item: EvidenceItem, pack: EvidencePack) -> EvidenceView:
    return EvidenceView(
        evidence_id=item.evidence_id,
        kind=item.kind,
        url=item.url,
        excerpt=" ".join(item.excerpt.split()),
        alias=pack.alias_for(item.evidence_id),
        author=item.author,
        created_at=item.created_at,
        pr_number=item.pr_number,
        issue_number=item.issue_number,
        commit_sha=item.commit_sha,
        path=item.path,
        start_line=item.start_line,
        end_line=item.end_line,
    )


def _decision_views(
    decisions: Sequence[DecisionCandidate],
    contexts: dict[str, GraphContext],
) -> list[DecisionView]:
    historical = {DecisionState.SUPERSEDED, DecisionState.REVERSED}
    views: list[DecisionView] = []
    for decision in decisions:
        context = contexts.get(decision.decision_id)
        views.append(
            DecisionView(
                decision_id=decision.decision_id,
                claim=decision.claim,
                rationale=decision.rationale,
                status=decision.status,
                confidence=decision.confidence,
                target_ids=list(decision.target_ids),
                evidence_ids=list(decision.evidence_ids),
                superseded_by=(
                    decision.superseded_by
                    or (
                        context.later_decisions[0]
                        if context is not None and context.later_decisions
                        else None
                    )
                ),
                created_at=decision.created_at,
                is_historical=(
                    decision.status in historical
                    or bool(context is not None and context.later_decisions)
                ),
            )
        )
    return views


def _evidence_strength(evidence: Sequence[EvidenceItem]) -> EvidenceStrength:
    if not evidence:
        return EvidenceStrength.NONE
    kinds = {item.kind for item in evidence}
    if len(evidence) >= 3 and len(kinds) >= 2:
        return EvidenceStrength.STRONG
    if len(evidence) >= 2 and len(kinds) >= 2:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.THIN


_ROLE_BY_KIND = {
    "commit": "committer",
    "review": "reviewer",
    "review_comment": "reviewer",
    "issue_comment": "commenter",
    "pull_request": "author",
    "changed_file": "author",
    "doc_file": "author",
}


def _people_views(evidence: Sequence[EvidenceItem]) -> list[PersonView]:
    by_login: dict[str, PersonView] = {}
    for item in evidence:
        login = (item.author or "").strip()
        if not login:
            continue
        view = by_login.get(login)
        if view is None:
            view = PersonView(login=login, first_seen=item.created_at, last_seen=item.created_at)
            by_login[login] = view
        view.evidence_count += 1
        role = _ROLE_BY_KIND.get(item.kind, "contributor")
        if role not in view.roles:
            view.roles.append(role)
        if item.created_at:
            if view.first_seen is None or item.created_at < view.first_seen:
                view.first_seen = item.created_at
            if view.last_seen is None or item.created_at > view.last_seen:
                view.last_seen = item.created_at
    return sorted(by_login.values(), key=lambda item: (-item.evidence_count, item.login))


def _nearest(candidates: Sequence[DecisionCandidate]) -> list[NearestCandidate]:
    return [
        NearestCandidate(
            decision_id=candidate.decision_id,
            claim=candidate.claim,
            status=candidate.status,
        )
        for candidate in candidates[:3]
    ]


def _validation_caveats(validation: ValidationResult) -> list[str]:
    caveats = ["The generated answer failed citation validation."]
    if validation.invalid_citations:
        caveats.append(
            "Invalid citations: " + ", ".join(validation.invalid_citations[:5])
        )
    if validation.unsupported_claims:
        caveats.append(
            f"{len(validation.unsupported_claims)} sentence(s) had no valid evidence citation."
        )
    if validation.quote_mismatches:
        caveats.append(
            f"{len(validation.quote_mismatches)} quote(s) did not match the evidence."
        )
    return caveats


__all__ = ["DEFAULT_CAVEAT", "RetrievalService"]
