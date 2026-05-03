"""
CMS Guideline Ingestion Script
================================
Loads all .txt files from data/cms_guidelines/ into the Qdrant
'pa_guidelines' collection, replacing any existing data.

Usage (from project root):
    docker compose run --rm app python scripts/ingest_guidelines.py

Or locally (with QDRANT_URL pointing to the running container):
    QDRANT_URL=http://localhost:6334 python scripts/ingest_guidelines.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

from qdrant_client import AsyncQdrantClient

COLLECTION = "pa_guidelines"
DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "cms_guidelines"

CPT_META: dict[str, dict[str, str]] = {
    "67028": {"procedure": "anti-VEGF injection",          "cpt": "67028"},
    "66984": {"procedure": "cataract surgery standard",    "cpt": "66984"},
    "66982": {"procedure": "cataract surgery complex",     "cpt": "66982"},
    "67036": {"procedure": "vitrectomy",                   "cpt": "67036"},
    "65855": {"procedure": "trabeculoplasty glaucoma",     "cpt": "65855"},
}


async def ingest(force: bool = False) -> None:
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = os.getenv("QDRANT_API_KEY")  # required for Qdrant Cloud
    client = AsyncQdrantClient(url=url, api_key=api_key)

    txt_files = sorted(DATA_DIR.glob("*.txt"))
    if not txt_files:
        print(f"[ingest] No .txt files found in {DATA_DIR}. Aborting.")
        sys.exit(1)

    # Delete existing collection if forcing re-ingest
    collections = await client.get_collections()
    existing = [c.name for c in collections.collections]
    if COLLECTION in existing:
        if force:
            print(f"[ingest] Deleting existing collection '{COLLECTION}'...")
            await client.delete_collection(COLLECTION)
        else:
            count = (await client.count(collection_name=COLLECTION)).count
            if count > 0:
                print(f"[ingest] Collection '{COLLECTION}' already has {count} vectors. Use --force to re-ingest.")
                return

    documents, ids, metadata = [], [], []
    for i, path in enumerate(txt_files, start=1):
        cpt = path.stem.split("_")[0]
        meta = CPT_META.get(cpt, {"procedure": path.stem, "cpt": cpt})
        text = path.read_text(encoding="utf-8").strip()
        documents.append(text)
        ids.append(i)
        metadata.append(meta)
        print(f"[ingest] Loaded: {path.name} ({len(text)} chars)")

    print(f"[ingest] Embedding and uploading {len(documents)} documents to '{COLLECTION}'...")
    await client.add(
        collection_name=COLLECTION,
        documents=documents,
        ids=ids,
        metadata=metadata,
    )
    count = (await client.count(collection_name=COLLECTION)).count
    print(f"[ingest] Done. Collection '{COLLECTION}' now has {count} vectors.")


if __name__ == "__main__":
    force = "--force" in sys.argv
    asyncio.run(ingest(force=force))
