"""Reset and seed a deterministic ARGUS AML investigation narrative.

Run from ARGUS-Pipeline after the Compose services are healthy:
    python scripts/seed_demo.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase

from config import (
    ELASTICSEARCH_URL,
    ES_AUTH_KWARGS,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    REDIS_HOST,
    REDIS_PORT,
)
from db import get_cursor, init_db
from jurisdictions import jurisdiction_for_station


ENTITIES = [
    ("Alpha Holdings", "9000000001"),
    ("Beta Trading", "9000000002"),
    ("Gamma Offshore", "9000000003"),
    ("Delta Consulting", "9000000004"),
    ("Epsilon Logistics", "9000000005"),
]


def seed() -> None:
    init_db()
    es = Elasticsearch(ELASTICSEARCH_URL, **ES_AUTH_KWARGS)
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    for index in ("argus-firs", "argus-cdrs"):
        if es.indices.exists(index=index):
            es.indices.delete(index=index)
    job_keys = list(redis_client.scan_iter("argus:jobs:*"))
    if job_keys:
        redis_client.delete(*job_keys)

    with neo4j.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

    now = datetime.now(timezone.utc)
    redis_client.set(
        "argus:jobs:seed-demo",
        json.dumps({"job_id": "seed-demo", "status": "processed", "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}),
    )
    firs = []
    for index, (name, phone) in enumerate(ENTITIES, start=1):
        firs.append(
            {
                "fir_id": f"AML-2026-{index:03d}",
                "station": "High-Risk Financial Crimes Unit",
                "complainant": "Financial Intelligence Directorate",
                "accused": name,
                "description": (
                    f"{name} received structured deposits below the reporting threshold "
                    "through multiple branches before transferring funds to the next ring entity."
                ),
                "risk_score": 0.94 - index * 0.02,
                "kyc_status": "enhanced_due_diligence",
                "registered_address": f"{index} Meridian Avenue",
                "phone": phone,
                "country": "India",
            }
        )
    for fir in firs:
        fir.setdefault("jurisdiction", jurisdiction_for_station(fir.get("station")))
        es.index(index="argus-firs", id=fir["fir_id"], document=fir, refresh=True)

    cdrs = []
    for i in range(20):
        source_index = i % len(ENTITIES)
        offset = (i // len(ENTITIES)) + 1
        source_name, source_phone = ENTITIES[source_index]
        target_name, target_phone = ENTITIES[(source_index + offset) % len(ENTITIES)]
        cdrs.append(
            {
                "call_id": f"CDR-AML-{i + 1:03d}",
                "caller": source_phone,
                "receiver": target_phone,
                "caller_entity": source_name,
                "receiver_entity": target_name,
                "timestamp": (now - timedelta(hours=i)).isoformat(),
                "duration_sec": 45 + i * 13,
                "amount": 9950 + (i % 3) * 25,
                "branch": f"Branch-{(i % 5) + 1}",
                "pattern": "structuring",
            }
        )
        es.index(index="argus-cdrs", id=cdrs[-1]["call_id"], document=cdrs[-1], refresh=True)

    with neo4j.session() as session:
        for fir in firs:
            session.run(
                """
                MERGE (s:Suspect {id: $name})
                SET s.entity_type = 'organization', s.kyc_status = $kyc_status,
                    s.risk_score = $risk_score, s.registered_address = $address
                MERGE (f:FIR {id: $fir_id})
                SET f.country = $country, f.pattern = 'structuring', f.jurisdiction = $jurisdiction
                MERGE (s)-[:LINKED_TO_FIR]->(f)
                MERGE (p:Phone {id: $phone})
                MERGE (s)-[:USES_PHONE]->(p)
                """,
                name=fir["accused"],
                kyc_status=fir["kyc_status"],
                risk_score=fir["risk_score"],
                address=fir["registered_address"],
                fir_id=fir["fir_id"],
                country=fir["country"],
                jurisdiction=fir["jurisdiction"],
                phone=fir["phone"],
            )
        for cdr in cdrs:
            session.run(
                """
                MERGE (a:Phone {id: $caller})
                MERGE (b:Phone {id: $receiver})
                MERGE (a)-[:CALLED {call_id: $call_id, timestamp: $timestamp,
                                    duration: $duration, amount: $amount,
                                    branch: $branch, pattern: 'structuring'}]->(b)
                """,
                **cdr,
                duration=cdr["duration_sec"],
            )
        session.run(
            """
            MERGE (a:Suspect {id: 'Alpha Holdings'})
            MERGE (b:Suspect {id: 'Epsilon Logistics'})
            MERGE (a)-[:SHARED_IP {ip: '198.51.100.42'}]->(b)
            MERGE (a)-[:SHARED_PHONE {phone: '9000000005'}]->(b)
            """
        )

    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM alerts")
        cur.execute("DELETE FROM alert_rules")
        cur.execute("DELETE FROM cases")
        cur.execute(
            """
            INSERT INTO cases (title, fir_number, jurisdiction, is_sensitive, sensitivity_reason, category)
            VALUES
              ('North Korea Correspondent Network', 'AML-2026-001', 'High-Risk - North Korea', TRUE, 'sanctions evasion', 'structuring'),
              ('Iran Trade-Based Laundering Ring', 'AML-2026-002', 'High-Risk - Iran', TRUE, 'trade-based laundering', 'structuring'),
              ('Offshore Layering Review', 'AML-2026-003', 'High-Risk - International', TRUE, 'offshore layering', 'structuring')
            """
        )
        cur.execute(
            "INSERT INTO alert_rules (entity_value, created_by) VALUES (%s, %s) RETURNING rule_id",
            ("Alpha Holdings", "seed-demo"),
        )
        rule_id = cur.fetchone()["rule_id"]
        cur.execute(
            """
            INSERT INTO alerts (rule_id, entity_value, message)
            VALUES (%s, %s, %s)
            """,
            (rule_id, "Alpha Holdings", "Structuring pattern detected across five branches."),
        )

    print(json.dumps({"status": "seeded", "fir_count": len(firs), "cdr_count": len(cdrs), "seeded_at": now.isoformat()}))
    neo4j.close()
    es.close()


if __name__ == "__main__":
    seed()
