"""Deterministic text normalisation for question understanding.

The goal is neither stemming nor semantics: it is to make lexical matching
predictable across identifiers (``retry_limit``), prose (``retry limits``),
and plurals without inventing meaning. Every transformation is reversible in
the sense that the original token is kept alongside its variants.
"""

from __future__ import annotations

import re

from app.core.errors import InvalidRecordError

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

# Question words and structural filler. Content words that matter to config
# and code questions (default, enabled, disabled, limit, timeout, retry) are
# deliberately not stopped.
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "being",
        "between",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "for",
        "from",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "more",
        "most",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "over",
        "please",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "up",
        "us",
        "use",
        "used",
        "using",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

# Small, general lexical equivalences. These are vocabulary normalisations,
# not repository-specific guesses. A config key containing "max" should match
# a user who says "limit"; the LLM fallback still resolves genuine ambiguity.
_ALIASES = {
    "authn": "auth",
    "authenticate": "auth",
    "authentication": "auth",
    "auth": "auth",
    "config": "config",
    "configuration": "config",
    "configured": "config",
    "limit": "max",
    "limits": "max",
    "maximum": "max",
    "max": "max",
    "minimum": "min",
    "min": "min",
    "retries": "retry",
    "retrying": "retry",
    "retry": "retry",
    "timeout": "timeout",
    "timeouts": "timeout",
}


def clean_question(value: str, *, max_chars: int) -> str:
    """Normalise a user question and refuse empty or oversized input."""
    if not isinstance(value, str):
        raise InvalidRecordError(
            "question must be a string", component="retrieval"
        )
    text = _CONTROL.sub(" ", value)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        raise InvalidRecordError(
            "question must not be empty", component="retrieval"
        )
    if len(text) > max_chars:
        raise InvalidRecordError(
            f"question exceeds {max_chars} characters", component="retrieval"
        )
    return text


def split_identifier(value: str) -> list[str]:
    """Split a symbol, path, dotted key or camelCase name into tokens."""
    parts = re.split(r"[^A-Za-z0-9]+", value)
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for token in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|\d+", part
        ):
            lowered = token.lower()
            if lowered:
                tokens.append(lowered)
    return tokens


def token_forms(token: str) -> frozenset[str]:
    """Return the lexical variants used for equality-based matching."""
    lowered = token.lower()
    forms = {lowered}
    if len(lowered) > 4 and lowered.endswith("ies"):
        forms.add(lowered[:-3] + "y")
    if len(lowered) > 3 and lowered.endswith("es"):
        forms.add(lowered[:-2])
        forms.add(lowered[:-1])
    if len(lowered) > 3 and lowered.endswith("s"):
        forms.add(lowered[:-1])
    if len(lowered) > 4 and lowered.endswith("ed"):
        forms.add(lowered[:-2])
        forms.add(lowered[:-1])
    if len(lowered) > 5 and lowered.endswith("ing"):
        forms.add(lowered[:-3])
    return frozenset(_ALIASES.get(form, form) for form in forms)


def content_tokens(text: str) -> list[str]:
    """Lowercase content tokens, preserving first-seen order."""
    seen: set[str] = set()
    tokens: list[str] = []
    for token in split_identifier(text):
        if token in STOPWORDS or len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def tokens_match(left: str, right: str) -> bool:
    return bool(token_forms(left) & token_forms(right))


_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def lucene_escape(value: str) -> str:
    """Escape a term for a Neo4j/Lucene full-text query."""
    return _LUCENE_SPECIAL.sub(r"\\\1", value)


__all__ = [
    "STOPWORDS",
    "clean_question",
    "content_tokens",
    "lucene_escape",
    "split_identifier",
    "token_forms",
    "tokens_match",
]
