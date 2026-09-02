# Contributing

ECatVASP is currently pre-alpha. Changes must preserve the Phase 0 architecture freeze.

Before opening a pull request:

```bash
ruff check .
mypy src
pytest
```

Scientific defaults, schema semantics, domain boundaries, and provenance behavior must not change silently. Material architecture changes require an ADR.
