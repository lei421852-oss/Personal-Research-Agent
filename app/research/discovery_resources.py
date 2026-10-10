"""既有增强Paper Index的只读来源装配，禁止在请求中抽取画像或重建索引。"""

import hashlib
import json
import sqlite3
from pathlib import Path

from app.papers.catalog import PaperCatalog, load_paper_catalog
from app.papers.models import REPRESENTATION_VERSION, PaperDocument, PaperProfile
from app.papers.profiles import source_fingerprint


def load_index_catalog(root):
    root = Path(root)
    fixture = root / "data/paper_discovery/papers.json"
    raw = load_paper_catalog(fixture)
    database = root / "data/chroma/paper-discovery/chroma.sqlite3"
    cache = root / "artifacts/paper-profiles.json"
    saved = json.loads(cache.read_text())["profiles"] if cache.is_file() else {}
    records = []
    for record in raw.records:
        paper = record.document
        entry = saved.get(paper.paper_id)
        if entry is not None:
            if entry.get("source_sha256") != source_fingerprint(paper):
                raise ValueError("profile_source_mismatch")
            profile = PaperProfile.model_validate(entry["profile"])
            paper = PaperDocument.model_validate(
                paper.model_dump() | profile.model_dump()
            )
        records.append(record.model_copy(update={"document": paper}))
    catalog = PaperCatalog(records)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        collection = db.execute(
            "SELECT id,dimension FROM collections WHERE name=?",
            ("papers-v02-metadata",),
        ).fetchone()
        if not collection or not collection[1]:
            raise ValueError("paper_index_unavailable")
        metadata = {
            key: next(v for v in values if v is not None)
            for key, *values in db.execute(
                "SELECT key,str_value,int_value,float_value,bool_value FROM collection_metadata WHERE collection_id=?",
                (collection[0],),
            )
        }
        if (
            metadata.get("fixture_sha256")
            != hashlib.sha256(fixture.read_bytes()).hexdigest()
            or metadata.get("representation_version") != REPRESENTATION_VERSION
        ):
            raise ValueError("paper_index_source_mismatch")
        rows = db.execute(
            "SELECT e.embedding_id,m.string_value FROM embeddings e JOIN segments s ON e.segment_id=s.id JOIN embedding_metadata m ON m.id=e.id WHERE s.collection=? AND s.scope='METADATA' AND m.key='paper_json'",
            (collection[0],),
        ).fetchall()
    if len(rows) != len(catalog.documents) or {identity for identity, _ in rows} != set(
        catalog.documents
    ):
        raise ValueError("paper_index_identity_mismatch")
    for identity, document in rows:
        if catalog.resolve(identity) != PaperDocument.model_validate_json(document):
            raise ValueError("paper_catalog_index_mismatch")
    return catalog, {
        "record_count": len(rows),
        "dimension": collection[1],
        "embedding_model": metadata.get("embedding_model"),
        "representation_version": metadata["representation_version"],
    }
