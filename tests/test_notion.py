from typing import Any
from unittest.mock import patch

from notion_automations.notion import fetch_classes_db


def test_fetch_classes_db(monkeypatch: Any) -> None:
    class DummyClient:
        class Databases:
            @staticmethod
            def retrieve(**kwargs: Any) -> dict[str, Any]:
                # No data_sources → use classic DB fallback
                return {}

            @staticmethod
            def query(**kwargs: Any) -> dict[str, Any]:
                # Simulate two pages of results
                if "start_cursor" in kwargs:
                    return {
                        "results": [{"id": "2", "properties": {}}],
                        "has_more": False,
                    }
                return {
                    "results": [{"id": "1", "properties": {}}],
                    "has_more": True,
                    "next_cursor": "abc",
                }

        databases = Databases()

    monkeypatch.setenv("NOTION_TOKEN", "fake-token")
    with patch("notion_automations.notion.Client", return_value=DummyClient()):
        results = fetch_classes_db("dbid")
        assert len(results) == 2
        assert results[0]["id"] == "1"
        assert results[1]["id"] == "2"
