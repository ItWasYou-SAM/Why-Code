# Why-Code

Answers *why is this code like this?* for a repository using its own
development history as evidence. GraphRAG over Neo4j.

## Status

Red-test phase of Component 0 (foundation). All contract tests are written;
implementation is deliberately absent. See `docs/DECISIONS.md` for the
revised plan and the decisions that contradict the original.

## Run the tests

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest
```

Expected state right now: everything failing. Tests define the contracts;
green comes when `app/core`, `app/domain`, and `app/main` are implemented to
satisfy them — nothing more.
