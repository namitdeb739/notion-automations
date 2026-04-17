import os
import tempfile
from typing import Any

from typer.testing import CliRunner

from notion_automations.cli import app


def test_export_classes_ics_cli(monkeypatch: Any) -> None:
    monkeypatch.setenv("NOTION_CLASSES_DB_ID", "dbid")
    called: dict[str, object] = {}

    def fake_fetch_classes_db(db_id: str) -> list[dict[str, Any]]:
        called["fetch"] = db_id
        return [
            {
                "properties": {
                    "Title": {"title": [{"plain_text": "Test"}]},
                    "Dates": {"date": {"start": "2024-04-12", "end": None}},
                    "Start Time (Decimal)": {"number": 10.0},
                    "End Time (Decimal)": {"number": 11.0},
                }
            }
        ]

    def fake_classes_to_ics(
        classes: list[dict[str, Any]],
        mapping: dict[str, Any],
        ics_path: str,
        timezone: str = "Europe/Berlin",
    ) -> None:
        called["ics"] = (classes, mapping, ics_path)
        with open(ics_path, "w") as f:
            f.write("BEGIN:VCALENDAR\nSUMMARY:Test\nEND:VCALENDAR\n")

    import notion_automations.cli as cli_mod

    cli_mod.fetch_classes_db = fake_fetch_classes_db
    cli_mod.classes_to_ics = fake_classes_to_ics
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        ics_path = os.path.join(tmpdir, "out.ics")
        result = runner.invoke(app, ["--ics-path", ics_path])
        assert result.exit_code == 0
        assert os.path.exists(ics_path)
        with open(ics_path) as f:
            content = f.read()
        assert "BEGIN:VCALENDAR" in content
        assert "SUMMARY:Test" in content
