"""Intent extraction and evidence-linking (Component 3).

The pipeline is deliberately not an agent: candidate detection, context
packing, one sandboxed LLM call per selected PR, schema validation, evidence
validation, hard-rule enforcement, normalisation, supersession and writing
are separate, independently testable steps. The LLM may propose intent; only
evidence that already exists in the ingestion store and the graph can make a
node real.
"""

from app.intent.config import IntentConfig
from app.intent.models import (
    Confidence,
    ConfidenceResult,
    ConstraintIntent,
    DecisionIntent,
    DecisionStatus,
    DecisionType,
    EvidenceRef,
    ExtractionStatus,
    IncidentIntent,
    IntentKind,
    Participant,
    ResolvedTarget,
    ReversalIntent,
    TargetOption,
    TargetType,
    TemporalScope,
)

__all__ = [
    "Confidence",
    "ConfidenceResult",
    "ConstraintIntent",
    "DecisionIntent",
    "DecisionStatus",
    "DecisionType",
    "EvidenceRef",
    "ExtractionStatus",
    "IncidentIntent",
    "IntentConfig",
    "IntentKind",
    "Participant",
    "ResolvedTarget",
    "ReversalIntent",
    "TargetOption",
    "TargetType",
    "TemporalScope",
]
