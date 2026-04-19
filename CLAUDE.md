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
just run [args]     # na CLI (notion-automations umbrella)
```

## Architecture

- `src/notion_automations/` — main package (src-layout)
- `tests/` — pytest tests; shared fixtures in `conftest.py`
- `src/notion_automations/cli.py` — Typer CLI entrypoint
- `src/notion_automations/notion.py` — Notion API fetch helpers
- `src/notion_automations/ics_export.py` — iCalendar export logic

## Notion Schema

The full database schema for the school dashboard is documented in
`docs/notion-schema-erd.md`. Read it before touching any Notion-related code.

Key databases and their data source IDs:

| Database | Data Source ID |
| --- | --- |
| Classes | `33d9080d-a147-809a-a8d6-000b74ccf447` |
| Courses | `33d9080d-a147-80f1-91a1-000b0a393b27` |
| Semesters | `33d9080d-a147-8048-819c-000b3f1a4d1d` |
| Course To-Dos | `33d9080d-a147-8093-ab2d-000bd2b04c53` |
| Admin To-Dos | `33d9080d-a147-81be-b7c2-000b1d69d515` |
| Degree Requirement Groups | `33e9080d-a147-808d-a1d6-000b91b2ecfb` |
| Degree Requirement Items | `33d9080d-a147-80b5-80ec-000b13f5c5e6` |
| Minor Requirement Groups | `33e9080d-a147-8100-8c5e-000bcda820e0` |
| Minor Requirement Items | `33e9080d-a147-817a-8d4e-000b4f908692` |
| Grade Point Average | `33d9080d-a147-80a5-b7ff-000b2a470190` |

Hierarchy: `Semesters` -> `Courses` -> `Classes` (and `Course To-Dos`).
Degree and minor requirements link back to `Courses`.

**Keep `docs/notion-schema-erd.md` up to date** when adding new databases,
properties, or relations. Run the schema explorer script in that doc to
re-confirm IDs after any Notion structural changes.

## Conventions

- All code must pass `mypy --strict`
- Format and lint: ruff (line length 88, Python 3.11+)
- Tests: pytest, fixtures in `conftest.py`
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, etc.)
- PRs: squash-merge to main, delete branch after merge
