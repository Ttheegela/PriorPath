"""
Qdrant vector search over CMS/payer clinical criteria guidelines.

Guidelines are loaded from data/cms_guidelines/*.txt at startup.
The collection is seeded automatically on first use (lazy, idempotent).
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

from qdrant_client import AsyncQdrantClient

COLLECTION = "pa_guidelines"

# ---------------------------------------------------------------------------
# Load guidelines from data/cms_guidelines/ at import time.
# Each file is named  <cpt>_<slug>.txt  and begins with metadata headers.
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "cms_guidelines"

# Metadata for each CPT — used when building Qdrant payloads
_CPT_META: dict[str, dict[str, str]] = {
    "67028": {"procedure": "anti-VEGF injection",          "cpt": "67028"},
    "66984": {"procedure": "cataract surgery standard",    "cpt": "66984"},
    "66982": {"procedure": "cataract surgery complex",     "cpt": "66982"},
    "67036": {"procedure": "vitrectomy",                   "cpt": "67036"},
    "65855": {"procedure": "trabeculoplasty glaucoma",     "cpt": "65855"},
}


def _load_guidelines() -> list[dict]:
    """
    Read every .txt file in data/cms_guidelines/.
    Returns list of dicts with keys: id, procedure, cpt, text.
    Falls back to compact hardcoded strings if files are missing.
    """
    guidelines: list[dict] = []
    txt_files = sorted(_DATA_DIR.glob("*.txt")) if _DATA_DIR.exists() else []

    for i, path in enumerate(txt_files, start=1):
        cpt = path.stem.split("_")[0]
        meta = _CPT_META.get(cpt, {"procedure": path.stem, "cpt": cpt})
        text = path.read_text(encoding="utf-8").strip()
        guidelines.append({"id": i, "text": text, **meta})

    if guidelines:
        return guidelines

    # ---- fallback (files not present) ----------------------------------------
    return [
        {
            "id": 1, "procedure": "anti-VEGF injection", "cpt": "67028",
            "text": (
                "Intravitreal anti-VEGF injection (CPT 67028) is medically necessary for: "
                "neovascular (wet) AMD with active choroidal neovascularization; "
                "diabetic macular edema with central involvement confirmed by OCT; "
                "macular edema following retinal vein occlusion. "
                "Required: baseline BCVA, OCT thickness measurements, fundus photography."
            ),
        },
        {
            "id": 2, "procedure": "cataract surgery standard", "cpt": "66984",
            "text": (
                "Cataract extraction with IOL (CPT 66984): BCVA 20/50 or worse, OR functional "
                "impairment documented. Slit-lamp confirms lens opacity. Pre-op biometry required."
            ),
        },
        {
            "id": 3, "procedure": "cataract surgery complex", "cpt": "66982",
            "text": (
                "Complex cataract (CPT 66982): standard criteria plus complicating factor — "
                "small pupil (<6mm), dense/brunescent nucleus, pseudoexfoliation, prior ocular surgery, "
                "traumatic cataract, or zonular weakness."
            ),
        },
        {
            "id": 4, "procedure": "vitrectomy", "cpt": "67036",
            "text": (
                "Pars plana vitrectomy (CPT 67036): non-clearing vitreous hemorrhage >4 weeks; "
                "tractional retinal detachment threatening macula; epiretinal membrane with VA 20/50+; "
                "full-thickness macular hole stage 2-4; retained lens fragments."
            ),
        },
        {
            "id": 5, "procedure": "trabeculoplasty glaucoma", "cpt": "65855",
            "text": (
                "Laser trabeculoplasty SLT (CPT 65855): open-angle glaucoma confirmed by gonioscopy; "
                "IOP inadequately controlled on maximum tolerated medical therapy; "
                "optic nerve and visual field documentation required."
            ),
        },
    ]


_GUIDELINES = _load_guidelines()


class QdrantSearchTool:
    """
    Manages the PA guideline index and exposes semantic search.
    Seeds the Qdrant collection on first use (idempotent).
    Uses client.add() so fastembed handles embedding automatically.
    """

    def __init__(self) -> None:
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = AsyncQdrantClient(url=url)
        self._seeded = False

    async def _ensure_seeded(self) -> None:
        if self._seeded:
            return

        collections = await self.client.get_collections()
        names = [c.name for c in collections.collections]
        needs_seed = COLLECTION not in names
        if not needs_seed:
            count = await self.client.count(collection_name=COLLECTION)
            needs_seed = count.count == 0

        if needs_seed:
            await self.client.add(
                collection_name=COLLECTION,
                documents=[g["text"] for g in _GUIDELINES],
                ids=[g["id"] for g in _GUIDELINES],
                metadata=[{"procedure": g["procedure"], "cpt": g["cpt"]} for g in _GUIDELINES],
            )

        self._seeded = True

    async def search(self, query: str, top_k: int = 2) -> list[dict[str, Any]]:
        """Retrieve relevant PA criteria for a procedure/diagnosis query."""
        await self._ensure_seeded()
        results = await self.client.query(
            collection_name=COLLECTION,
            query_text=query,
            limit=top_k,
        )
        return [
            {
                "procedure": r.metadata.get("procedure"),
                "cpt": r.metadata.get("cpt"),
                "text": r.document,
                "score": r.score,
            }
            for r in results
        ]


qdrant_tool = QdrantSearchTool()
