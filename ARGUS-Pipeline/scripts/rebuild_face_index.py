"""Rebuild the persistent face index from enrolled records already stored in Elasticsearch.

Run inside the api container (or anywhere with the same env):
    python scripts/rebuild_face_index.py

Skips records whose photo file is missing. Existing index entries are kept; entries whose FIR
is already indexed are not added twice.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import faiss  # noqa: E402
import numpy as np  # noqa: E402
from deepface import DeepFace  # noqa: E402
from elasticsearch import Elasticsearch  # noqa: E402

from config import ELASTICSEARCH_URL, ES_AUTH_KWARGS  # noqa: E402
from face_index import FaceIndex  # noqa: E402

DIM = 512


def main() -> None:
    index = FaceIndex(DIM, os.getenv("FACE_INDEX_DIR", "uploads/face_index"))
    known = {m.get("fir_id") for m in index._registry}  # noqa: SLF001 - maintenance script
    es = Elasticsearch(ELASTICSEARCH_URL, headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"}, **ES_AUTH_KWARGS)
    hits = es.search(index="argus-firs", query={"exists": {"field": "Image"}}, size=1000)["hits"]["hits"]
    added = skipped = 0
    for hit in hits:
        doc = hit["_source"]
        fir_id, name = doc.get("fir_id"), doc.get("accused", "Unknown")
        path = os.path.join("uploads", os.path.basename(doc.get("Image", "")))
        if fir_id in known or not os.path.isfile(path):
            skipped += 1
            continue
        img = cv2.imread(path)
        if img is None:
            skipped += 1
            continue
        obj = DeepFace.represent(img_path=cv2.resize(img, (320, 320)), model_name="ArcFace", detector_backend="retinaface", enforce_detection=False)
        emb = np.array(obj[0]["embedding"], dtype=np.float32)
        if emb.shape[0] != DIM:
            emb = np.resize(emb, (DIM,))
        faiss.normalize_L2(emb.reshape(1, -1))
        index.add(emb, {"hash_id": str(uuid.uuid4()), "name": name, "fir_id": fir_id})
        added += 1
    print(f"added={added} skipped={skipped} total={index.ntotal}")


if __name__ == "__main__":
    main()
