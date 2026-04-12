# notion-automations


[![CI](https://github.com/namitdeb739/notion-automations/actions/workflows/ci.yml/badge.svg)](https://github.com/namitdeb739/notion-automations/actions/workflows/ci.yml)
[![Docs](https://github.com/namitdeb739/notion-automations/actions/workflows/docs.yml/badge.svg)](https://namitdeb739.github.io/notion-automations/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Automation scripts to enhance personal notion usage

## Common commands

```bash
just check       # lint + typecheck + test (mirrors CI)
just fix         # auto-fix lint and formatting
just test        # run tests
just docs        # preview docs at http://127.0.0.1:8000/
just release X   # bump version (patch/minor/major), tag, push
just             # list all available recipes
```

## Project structure

```text
src/notion_automations/   Source package (src layout)
tests/                    Test suite (pytest)
docs/                     Documentation (MkDocs Material)
.github/                  Workflows, issue templates, CODEOWNERS
```


## Documentation

Full documentation: [namitdeb739.github.io/notion-automations](https://namitdeb739.github.io/notion-automations/)


## License

[MIT](LICENSE) — Namit Deb
