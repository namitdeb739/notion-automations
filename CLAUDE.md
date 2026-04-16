# notion-automations

Automation scripts to enhance personal notion usage

## Commands

```bash
just check          # full CI mirror: lint + typecheck + test
just lint           # ruff check + format check
just fix            # auto-fix lint and formatting issues
just typecheck      # mypy --strict
just test           # pytest with coverage
just docs           # serve docs locally at http://localhost:8000
just run [args]     # notion-automations CLI
```

## Architecture

- `src/notion_automations/` — main package (src-layout)
- `tests/` — pytest tests; shared fixtures in `conftest.py`
- `src/notion_automations/cli.py` — Typer CLI entrypoint

## Conventions

- All code must pass `mypy --strict`
- Format and lint: ruff (line length 88, Python 3.11+)
- Tests: pytest, fixtures in `conftest.py`
- Commits: Conventional Commits — `feat:`, `fix:`, `docs:`, `refactor:`, etc.
- PRs: squash-merge to main, delete branch after merge
