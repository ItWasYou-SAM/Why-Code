"""Documentation extraction.

Docs are linked to code only when the doc names the target explicitly:
a path in backticks, a fenced/plain code path, or a config key. We do not
infer "this paragraph is about the retry logic".
"""

from __future__ import annotations

import re

from app.structural.extraction.models import DocMention

_BACKTICK = re.compile(r"`([^`\n]+)`")
_PATH_LIKE = re.compile(r"^[\w./-]+\.(?:py|yaml|yml|toml|json|md|rst)$")
_CONFIG_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def extract_doc_mentions(
    source: str,
    *,
    doc_path: str,
    known_paths: set[str] | None = None,
    known_config_keys: set[str] | None = None,
) -> list[DocMention]:
    known = known_paths or set()
    keys = known_config_keys or set()
    mentions: list[DocMention] = []
    seen: set[tuple[str, str]] = set()

    for number, line in enumerate(source.splitlines(), start=1):
        for match in _BACKTICK.finditer(line):
            token = match.group(1).strip()
            if token.startswith("http://") or token.startswith("https://"):
                continue
            if _PATH_LIKE.match(token):
                path = token.lstrip("./")
                if path in known or not known:
                    key = (path, "")
                    if key not in seen:
                        seen.add(key)
                        mentions.append(
                            DocMention(doc_path=doc_path, target_path=path, line=number)
                        )
                continue
            if token in keys:
                key = ("", token)
                if key not in seen:
                    seen.add(key)
                    mentions.append(
                        DocMention(doc_path=doc_path, config_key=token, line=number)
                    )
    mentions.sort(key=lambda mention: (mention.line or 0, mention.target_path or ""))
    return mentions
