"""Script to print sample rows from Notion Classes DB using data source query."""

import os

from notion_client import Client

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_CLASSES_DB_ID")
    if not token or not db_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_CLASSES_DB_ID must be set.")
    notion = Client(auth=token)
    db = notion.databases.retrieve(database_id=db_id)
    if db.get("data_sources"):
        ds_id = db["data_sources"][0]["id"]
        # Query the data source for sample rows
        resp = notion.data_sources.query(ds_id, page_size=3)
        print("Sample rows from data source:")
        for row in resp.get("results", []):
            print(row)
    else:
        # Fallback for classic DB
        resp = notion.databases.query(database_id=db_id, page_size=3)
        print("Sample rows from database:")
        for row in resp.get("results", []):
            print(row)


if __name__ == "__main__":
    main()
