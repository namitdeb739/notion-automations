"""Quick script to print Notion Classes DB schema."""

import os

from notion_client import Client

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv is optional, but recommended for local dev


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_CLASSES_DB_ID")
    if not token or not db_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_CLASSES_DB_ID must be set.")
    notion = Client(auth=token)
    db = notion.databases.retrieve(database_id=db_id)
    print("Database API response:")
    print(db)
    # If classic database, print properties
    if "properties" in db:
        print("Properties:")
        for name, prop in db["properties"].items():
            print(f"- {name}: {prop['type']}")
    # If data_sources present, fetch schema from data source endpoint
    elif db.get("data_sources"):
        ds_id = db["data_sources"][0]["id"]
        ds = notion.data_sources.retrieve(ds_id)
        print("Data Source API response:")
        print(ds)
        if "properties" in ds:
            print("Properties:")
            for name, prop in ds["properties"].items():
                print(f"- {name}: {prop['type']}")
        else:
            print("No 'properties' key found in data source.")
    else:
        print("No 'properties' or 'data_sources' key found in database object.")


if __name__ == "__main__":
    main()
