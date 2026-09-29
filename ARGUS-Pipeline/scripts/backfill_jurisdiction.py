"""Stamp every existing FIR (Elasticsearch document and Neo4j node) with the jurisdiction that owns its station.

Run once after upgrading — new records get the field at ingest time. Safe to re-run.

    docker compose exec api python scripts/backfill_jurisdiction.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import Elasticsearch, helpers  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from config import ELASTICSEARCH_URL, ES_AUTH_KWARGS, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER  # noqa: E402
from jurisdictions import STATION_JURISDICTION, UNASSIGNED, jurisdiction_for_station  # noqa: E402

INDEX = "argus-firs"


def main() -> None:
    es = Elasticsearch(ELASTICSEARCH_URL, headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"}, **ES_AUTH_KWARGS)

    # 1. Elasticsearch: one pass over every document, so the result never depends on a partial mapping.
    actions, by_fir = [], {}
    for hit in helpers.scan(es, index=INDEX, query={"query": {"match_all": {}}}, _source=["station", "jurisdiction"]):
        source = hit["_source"]
        jurisdiction = source.get("jurisdiction") or jurisdiction_for_station(source.get("station"))
        by_fir[hit["_id"]] = jurisdiction
        if source.get("jurisdiction") != jurisdiction:
            actions.append({"_op_type": "update", "_index": INDEX, "_id": hit["_id"], "doc": {"jurisdiction": jurisdiction}})
    ok, errors = helpers.bulk(es, actions, raise_on_error=False)
    es.indices.refresh(index=INDEX)
    print(f"Elasticsearch: updated {ok} documents ({len(errors) if isinstance(errors, list) else errors} errors)")

    # 2. Neo4j: FIR nodes carry only an id, so the jurisdiction comes from the matching document.
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    rows = [{"id": fir_id, "j": j} for fir_id, j in by_fir.items()]
    with driver.session() as session:
        for i in range(0, len(rows), 500):
            session.run("UNWIND $rows AS r MATCH (f:FIR {id: r.id}) SET f.jurisdiction = r.j", rows=rows[i:i + 500])
        leftover = session.run("MATCH (f:FIR) WHERE f.jurisdiction IS NULL SET f.jurisdiction = $u RETURN count(f) AS n", u=UNASSIGNED).single()["n"]
        counts = {r["j"]: r["n"] for r in session.run("MATCH (f:FIR) RETURN f.jurisdiction AS j, count(*) AS n ORDER BY n DESC")}
    print(f"Neo4j: {leftover} FIR nodes had no matching document -> {UNASSIGNED}")
    print("FIRs per jurisdiction:", counts)
    print(f"Mapped stations: {len(STATION_JURISDICTION)}")


if __name__ == "__main__":
    main()
