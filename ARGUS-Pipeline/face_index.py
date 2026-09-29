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
from typing import Optional

import faiss
import numpy as np


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

    def search(self, embedding: np.ndarray) -> Optional[tuple[float, dict]]:
        """Best match as (cosine similarity, registry entry), or None if the index is empty."""
        with self._mutex:
            self._sync()
            if self._index.ntotal == 0:
                return None
            distances, indices = self._index.search(np.ascontiguousarray(embedding.reshape(1, -1), dtype=np.float32), 1)
            return float(distances[0][0]), self._registry[int(indices[0][0])]
