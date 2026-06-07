"""Ingest a shop's catalog or FAQ into the vector store.

Usage:
    python -m bot.ingest --shop demo_shop --file data/sample_catalog.csv

Accepts any CSV. Each row becomes one searchable chunk built from its columns,
e.g. "name: Krossovka; size: 36-44; price: 350000 so'm; status: bor".
A folded (script-normalized) copy is appended so Latin/Cyrillic spellings match.
"""
import argparse
import csv

from bot import store, translit


def rows_to_records(path: str) -> list[dict]:
    """Parse CSV rows without converting to chunks."""
    records: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if any(v and v.strip() for v in row.values()):
                records.append(dict(row))
    return records


def extract_key(chunk: str, field: str) -> str | None:
    """Extract a named field value from the first line of a stored chunk."""
    prefix = f"{field}: "
    first_line = chunk.split("\n")[0]
    for part in first_line.split("; "):
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def rows_to_chunks(path: str) -> list[str]:
    chunks: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parts = [f"{k}: {v}".strip() for k, v in row.items() if v and v.strip()]
            if not parts:
                continue
            content = "; ".join(parts)
            # Append a script-folded alias to boost cross-script retrieval.
            chunks.append(f"{content}\n{translit.fold(content)}")
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", required=True, help="shop_id from shops.json")
    ap.add_argument("--file", required=True, help="path to a CSV catalog/FAQ")
    ap.add_argument("--replace", action="store_true",
                    help="wipe this shop's existing data first")
    args = ap.parse_args()

    store.init_db()
    if args.replace:
        store.clear_shop(args.shop)
    chunks = rows_to_chunks(args.file)
    n = store.add_items(args.shop, chunks)
    print(f"Ingested {n} chunks for shop '{args.shop}'.")


if __name__ == "__main__":
    main()
