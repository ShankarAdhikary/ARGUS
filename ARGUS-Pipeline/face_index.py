"""File-backed FAISS index shared by every API worker.

The index used to live in process memory, so enrolled faces vanished on every restart and each
uvicorn worker saw a different set. Vectors and their registry are now persisted to a directory
(mounted volume) and each operation first syncs with what other workers have written.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Callable, Optional

import faiss
import numpy as np


# Faces enrolled before jurisdictions were recorded carry none; they count as this, which only unscoped roles can see.
UNASSIGNED = "Unassigned"
# When the caller is jurisdiction-scoped the search must look past out-of-scope faces, so it reads up to this many nearest
# neighbours (exact search; the index is flat) before filtering. A scoped officer can therefore only miss an in-scope face
# that has more than this many closer out-of-scope faces ahead of it.
SCOPED_SEARCH_DEPTH = 5000


class FaceIndex:
    def __init__(self, dim: int, directory: str):
        self.dim = dim
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._vectors_path = self.dir / "vectors.npy"
        self._registry_path = self.dir / "registry.json"
        self._lock_path = self.dir / ".lock"
        self._index = faiss.IndexFlatIP(dim)
        self._registry: list[dict] = []
        self._loaded_stamp = None
        self._mutex = threading.Lock()
        self._sync()

    def _stamp(self):
        try:
            return (self._vectors_path.stat().st_mtime_ns, self._registry_path.stat().st_mtime_ns)
        except FileNotFoundError:
            return None

    def _sync(self) -> None:
        stamp = self._stamp()
        if stamp == self._loaded_stamp:
            return
        index = faiss.IndexFlatIP(self.dim)
        registry: list[dict] = []
        if stamp is not None:
            vectors = np.load(self._vectors_path)
            registry = json.loads(self._registry_path.read_text())
            if len(vectors) == len(registry) and len(vectors):
                index.add(np.ascontiguousarray(vectors, dtype=np.float32))
            else:
                registry = []
        self._index, self._registry, self._loaded_stamp = index, registry, stamp

    @property
    def ntotal(self) -> int:
        with self._mutex:
            self._sync()
            return self._index.ntotal

    def add(self, embedding: np.ndarray, meta: dict) -> None:
        """Append one L2-normalised embedding; safe across threads and worker processes."""
        row = np.ascontiguousarray(embedding.reshape(1, -1), dtype=np.float32)
        with self._mutex, open(self._lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._sync()
                vectors = np.load(self._vectors_path) if self._vectors_path.exists() else np.empty((0, self.dim), np.float32)
                vectors = np.vstack([vectors, row])
                registry = [*self._registry, meta]
                # Write to temp files then rename, so readers never see a half-written index.
                tmp_v, tmp_r = self.dir / "vectors.tmp.npy", self.dir / "registry.tmp.json"
                np.save(tmp_v, vectors)
                tmp_r.write_text(json.dumps(registry))
                os.replace(tmp_v, self._vectors_path)
                os.replace(tmp_r, self._registry_path)
                self._loaded_stamp = None
                self._sync()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def remove_where(self, predicate: Callable[[dict], bool]) -> int:
        """Drop every face whose registry entry satisfies `predicate` (used to clean up test enrolments)."""
        with self._mutex, open(self._lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._sync()
                keep = [i for i, meta in enumerate(self._registry) if not predicate(meta)]
                removed = len(self._registry) - len(keep)
                if removed:
                    vectors = np.load(self._vectors_path)[keep] if keep else np.empty((0, self.dim), np.float32)
                    tmp_v, tmp_r = self.dir / "vectors.tmp.npy", self.dir / "registry.tmp.json"
                    np.save(tmp_v, vectors)
                    tmp_r.write_text(json.dumps([self._registry[i] for i in keep]))
                    os.replace(tmp_v, self._vectors_path)
                    os.replace(tmp_r, self._registry_path)
                    self._loaded_stamp = None
                    self._sync()
                return removed
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def remove_by_fir(self, fir_id: str) -> int:
        """Drop every face registered under `fir_id` (used when a suspect record is purged)."""
        with self._mutex, open(self._lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._sync()
                keep = [i for i, meta in enumerate(self._registry) if meta.get("fir_id") != fir_id]
                removed = len(self._registry) - len(keep)
                if removed:
                    vectors = np.load(self._vectors_path)[keep] if keep else np.empty((0, self.dim), np.float32)
                    tmp_v, tmp_r = self.dir / "vectors.tmp.npy", self.dir / "registry.tmp.json"
                    np.save(tmp_v, vectors)
                    tmp_r.write_text(json.dumps([self._registry[i] for i in keep]))
                    os.replace(tmp_v, self._vectors_path)
                    os.replace(tmp_r, self._registry_path)
                    self._loaded_stamp = None
                    self._sync()
                return removed
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def search(self, embedding: np.ndarray, scope: Optional[str] = None) -> Optional[tuple[float, dict]]:
        """Best match as (cosine similarity, registry entry), or None if there is none.

        With `scope` set only faces enrolled in that jurisdiction are eligible, so a jurisdiction-scoped officer can never be
        shown (or told about) a face that belongs to another jurisdiction. Entries without a jurisdiction are treated as
        Unassigned and stay invisible to scoped callers.
        """
        with self._mutex:
            self._sync()
            if self._index.ntotal == 0:
                return None
            k = 1 if scope is None else min(self._index.ntotal, SCOPED_SEARCH_DEPTH)
            distances, indices = self._index.search(np.ascontiguousarray(embedding.reshape(1, -1), dtype=np.float32), k)
            for score, position in zip(distances[0], indices[0]):
                if position < 0:
                    continue
                meta = self._registry[int(position)]
                if scope is None or meta.get("jurisdiction", UNASSIGNED) == scope:
                    return float(score), meta
            return None

    def backfill_jurisdiction(self, resolve: Callable[[dict], Optional[str]], apply: bool = True) -> dict:
        """Give legacy entries (no `jurisdiction`) one, using `resolve(meta)`; entries it cannot resolve become Unassigned.

        Only the registry is rewritten (vectors are untouched). With apply=False nothing is written: it just reports.
        Returns {"already": n, "resolved": n, "unassigned": n, "by_jurisdiction": {...}}.
        """
        with self._mutex, open(self._lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._sync()
                already, resolved, unassigned, by_j = 0, 0, 0, {}
                registry = []
                for meta in self._registry:
                    if "jurisdiction" in meta:
                        already += 1
                        registry.append(meta)
                        continue
                    found = resolve(meta)
                    if found and found != UNASSIGNED:
                        resolved += 1
                    else:
                        unassigned += 1
                        found = UNASSIGNED
                    by_j[found] = by_j.get(found, 0) + 1
                    registry.append({**meta, "jurisdiction": found})
                if apply and (resolved or unassigned):
                    tmp_r = self.dir / "registry.tmp.json"
                    tmp_r.write_text(json.dumps(registry))
                    os.replace(tmp_r, self._registry_path)
                    self._loaded_stamp = None
                    self._sync()
                return {"already": already, "resolved": resolved, "unassigned": unassigned, "by_jurisdiction": by_j}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
