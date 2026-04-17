
# Export Notion Classes to .ics

Easily export your Notion "Classes" database to a standard .ics calendar file for use in Google Calendar, Outlook, and more.

## Quickstart

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (fast Python package manager)
- Notion integration token and database ID

### Setup

```bash
just setup
```

### Environment Variables

Set your Notion credentials:

```bash
export NOTION_TOKEN="your-secret-token"
export NOTION_CLASSES_DB_ID="your-database-id"
```

### Export to .ics

```bash
just run export-classes-ics --ics-path my-classes.ics
```

- By default, uses the database ID from `NOTION_CLASSES_DB_ID`.
- The resulting `.ics` file can be imported into your calendar app.

---

## CLI Usage

```bash
just run --help
just run export-classes-ics --help
```

Example:

```bash
just run export-classes-ics --ics-path classes.ics --name-prop Name --start-prop Start --end-prop End
```

---

## Troubleshooting

| Problem                         | Solution                                             |
|---------------------------------|------------------------------------------------------|
| `NOTION_TOKEN` not set          | Export your token as an env var                      |
| `NOTION_CLASSES_DB_ID` missing  | Export your DB ID as an env var or pass `--db-id`    |
| Notion API errors               | Check integration permissions and DB ID              |
| .ics file empty                 | Ensure your Notion DB has events with correct fields |

---

## API Reference

- `notion_automations.cli.export_classes_ics`: CLI command for export.
- `notion_automations.notion.fetch_classes_db`: Fetches Notion DB rows.
- `notion_automations.ics_export.classes_to_ics`: Writes .ics file from Notion data.

---

## Contributing

- Add tests for new CLI commands in `tests/`.
- Run `just check` before committing.
- All public functions must be type-annotated.
