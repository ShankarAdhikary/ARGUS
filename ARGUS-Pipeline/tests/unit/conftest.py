"""conftest.py — inject lightweight stubs for every heavy dependency so the
unit tests can run without the full Docker stack or GPU packages installed.

This file is auto-loaded by pytest before any test module is imported.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# Packages that are either C-extensions or not installed in the test env.
_STUBS = [
    "minio",
    "deepface",
    "deepface.DeepFace",
    "cv2",
    "faiss",
    "numpy",
    "redis",
    "elasticsearch",
    "neo4j",
    "networkx",
    "networkx.community",
    "rapidfuzz",
    "fpdf",
    "fpdf.enums",
    "psycopg2",
    "psycopg2.extras",
    "psycopg2.pool",
    "scipy",
]

for _name in _STUBS:
    if _name not in sys.modules:
        _stub(_name)

# Give redis a realistic ping method
sys.modules["redis"].Redis = MagicMock(return_value=MagicMock(ping=MagicMock(return_value=True)))

# Give elasticsearch a realistic ping
sys.modules["elasticsearch"].Elasticsearch = MagicMock(
    return_value=MagicMock(ping=MagicMock(return_value=True))
)

# platform_api imports elasticsearch.helpers (bulk scans); the stub needs the attribute
sys.modules["elasticsearch"].helpers = MagicMock()
sys.modules["elasticsearch.helpers"] = sys.modules["elasticsearch"].helpers

# Give minio a realistic Minio class
sys.modules["minio"].Minio = MagicMock(return_value=MagicMock())

# Give neo4j.GraphDatabase.driver
_neo4j_mod = sys.modules["neo4j"]
_neo4j_mod.GraphDatabase = MagicMock()
_neo4j_mod.GraphDatabase.driver = MagicMock(return_value=MagicMock())

# faiss index
sys.modules["faiss"].IndexFlatIP = MagicMock(return_value=MagicMock(ntotal=0))
sys.modules["faiss"].normalize_L2 = MagicMock()

# numpy
import importlib as _il  # noqa: E402
try:
    _np = _il.import_module("numpy")
    sys.modules["numpy"] = _np
except ModuleNotFoundError:
    _np_stub = _stub("numpy")
    _np_stub.array = MagicMock(return_value=MagicMock(shape=(512,)))
    _np_stub.frombuffer = MagicMock(return_value=MagicMock())
    _np_stub.float32 = float

# networkx community needs louvain_communities
_nx = types.ModuleType("networkx")
_nx.Graph = MagicMock(return_value=MagicMock(
    number_of_nodes=MagicMock(return_value=0),
    nodes=MagicMock(return_value=[]),
    add_node=MagicMock(),
    add_edge=MagicMock(),
))
_nx.pagerank = MagicMock(return_value={})
_nx.betweenness_centrality = MagicMock(return_value={})
_nx.community = MagicMock()
_nx.community.louvain_communities = MagicMock(return_value=[])
sys.modules["networkx"] = _nx
sys.modules["networkx.community"] = _nx.community

# rapidfuzz
sys.modules["rapidfuzz"].fuzz = MagicMock()
sys.modules["rapidfuzz"].fuzz.ratio = MagicMock(return_value=0.0)

# fpdf
_fpdf_mod = sys.modules["fpdf"]
_fpdf_mod.FPDF = MagicMock()
_fpdf_enums = sys.modules["fpdf.enums"]
_fpdf_enums.XPos = MagicMock()
_fpdf_enums.YPos = MagicMock()

# psycopg2
_pg2 = sys.modules["psycopg2"]
_pg2.extras = sys.modules["psycopg2.extras"]
_pg2.extras.RealDictCursor = MagicMock()
_pg2.pool = sys.modules["psycopg2.pool"]
_pg2.pool.SimpleConnectionPool = MagicMock(return_value=MagicMock())
_pg2.connect = MagicMock()

# cv2
sys.modules["cv2"].imdecode = MagicMock(return_value=MagicMock())
sys.modules["cv2"].resize = MagicMock(return_value=MagicMock())
sys.modules["cv2"].IMREAD_COLOR = 1

# The persistent face index needs real numpy/faiss and a writable volume; unit tests don't exercise it.
_face_index_mod = _stub("face_index")
_face_index_mod.FaceIndex = MagicMock(return_value=MagicMock(ntotal=0))
