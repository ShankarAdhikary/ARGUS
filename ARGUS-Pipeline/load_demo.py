"""Direct-load demo data into Neo4j + Elasticsearch.

Bypasses the HTTP ingest API so demo setup never depends on the worker queue
or MinIO being healthy.  Run this once after `docker compose up -d` to get
a populated graph before the demo.

Usage
-----
    # Generate files (idempotent — same files every time due to fixed seed):
    python generate_demo_dataset.py

    # Then load them:
    python load_demo.py

    # Or in one shot:
    python generate_demo_dataset.py && python load_demo.py

Environment variables are read from the same config.py used by the API so
local overrides (.env or shell exports) work automatically.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from elasticsearch import Elasticsearch, helpers
from neo4j import GraphDatabase

from jurisdictions import jurisdiction_for_station

from config import (
    ELASTICSEARCH_URL,
    ES_AUTH_KWARGS,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
)

# ---------------------------------------------------------------------------
# Helper: wait for Elasticsearch to be ready (it can take ~30 s on first boot)
# ---------------------------------------------------------------------------

def _wait_for_es(es: Elasticsearch, retries: int = 12, delay: float = 5.0) -> None:
    for attempt in range(retries):
        try:
            if es.ping():
                return
        except Exception:
            pass
        print(f"  [.] Waiting for Elasticsearch… ({attempt + 1}/{retries})")
        time.sleep(delay)
    print("[!] Elasticsearch did not become ready — continuing anyway.")


# ---------------------------------------------------------------------------
# Neo4j loaders
# ---------------------------------------------------------------------------

def load_firs_neo4j(driver: GraphDatabase.driver, firs: list[dict]) -> None:
    print(f"  Loading {len(firs)} FIRs into Neo4j …")
    with driver.session() as session:
        for record in firs:
            accused = record.get("accused", "UNKNOWN")
            mobile = (record.get("mobile") or "").strip()
            session.run(
                """
                MERGE (s:Suspect {id: $suspect_id})
                MERGE (f:FIR {id: $fir_id})
                MERGE (s)-[link:LINKED_TO_FIR]->(f)
                SET f.date = $date, f.jurisdiction = $jurisdiction, link.date = $date, link.source_record_id = $fir_id
                FOREACH (_ IN CASE WHEN $mobile <> '' THEN [1] ELSE [] END |
                    MERGE (p:Phone {id: $mobile})
                    MERGE (s)-[:USES_PHONE]->(p)
                )
                """,
                suspect_id=accused,
                fir_id=record.get("fir_id"),
                mobile=mobile,
                date=record.get("date"),
                jurisdiction=record.get("jurisdiction") or jurisdiction_for_station(record.get("station")),
            )


def load_cdrs_neo4j(driver: GraphDatabase.driver, cdrs: list[dict]) -> None:
    print(f"  Loading {len(cdrs)} CDRs into Neo4j …")
    with driver.session() as session:
        for record in cdrs:
            session.run(
                """
                MERGE (c:Phone {id: $caller})
                MERGE (r:Phone {id: $receiver})
                MERGE (c)-[call:CALLED {call_id: $call_id}]->(r)
                ON CREATE SET call.timestamp = $timestamp, call.duration = $duration,
                    call.date = $timestamp, call.source_record_id = $call_id
                """,
                caller=record.get("caller"),
                receiver=record.get("receiver"),
                call_id=record.get("call_id"),
                timestamp=record.get("timestamp"),
                duration=record.get("duration_sec"),
            )


def load_financial_neo4j(driver: GraphDatabase.driver, transactions: list[dict]) -> None:
    print(f"  Loading {len(transactions)} financial transactions into Neo4j …")
    with driver.session() as session:
        for record in transactions:
            session.run(
                """
                MERGE (a:FinancialAccount {id: $account_id})
                SET a.bank = $bank, a.currency = $currency
                MERGE (b:FinancialAccount {id: $counterparty})
                MERGE (a)-[t:TRANSACTED_WITH {transaction_id: $transaction_id}]->(b)
                SET t.amount = $amount, t.currency = $currency,
                    t.timestamp = $timestamp, t.direction = $direction,
                    t.source_record_id = $transaction_id
                """,
                account_id=record.get("account_id"),
                counterparty=record.get("counterparty_account"),
                bank=record.get("bank", "Synthetic Cooperative Bank"),
                currency=record.get("currency", "INR"),
                transaction_id=record.get("transaction_id"),
                amount=record.get("amount"),
                timestamp=record.get("timestamp"),
                direction=record.get("direction", "outgoing"),
            )


def load_surveillance_neo4j(driver: GraphDatabase.driver, events: list[dict]) -> None:
    print(f"  Loading {len(events)} surveillance events into Neo4j …")
    with driver.session() as session:
        for event in events:
            session.run(
                """
                MERGE (c:Camera {id: $camera_id})
                  ON CREATE SET c.zone = $zone
                  ON MATCH SET c.zone = $zone
                MERGE (s:Suspect {id: $suspect_id})
                MERGE (s)-[obs:SEEN_AT {source_id: $event_id}]->(c)
                SET obs.timestamp = $timestamp, obs.confidence = $confidence,
                    obs.evidence = 'Demo camera sighting at ' + $zone
                """,
                camera_id=event.get("camera_id"),
                zone=event.get("zone"),
                suspect_id=event.get("suspect_name"),
                event_id=event.get("event_id"),
                timestamp=event.get("timestamp"),
                confidence=event.get("match_confidence", 0.0),
            )


# ---------------------------------------------------------------------------
# Elasticsearch loaders
# ---------------------------------------------------------------------------

def load_firs_es(es: Elasticsearch, firs: list[dict]) -> None:
    print(f"  Indexing {len(firs)} FIRs into Elasticsearch …")
    actions = [
        {
            "_index": "argus-firs",
            "_id": record.get("fir_id"),
            "_source": {**record, "jurisdiction": record.get("jurisdiction") or jurisdiction_for_station(record.get("station"))},
        }
        for record in firs
    ]
    helpers.bulk(es, actions, raise_on_error=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    base = Path(__file__).parent

    files = {
        "firs": base / "demo_network_firs.json",
        "cdrs": base / "demo_network_cdrs.json",
        "surveillance": base / "demo_network_surveillance.json",
        "financial": base / "demo_network_financial.json",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        print(f"[!] Missing dataset files: {missing}")
        print("    Run `python generate_demo_dataset.py` first.")
        sys.exit(1)

    data = {name: json.loads(path.read_text()) for name, path in files.items()}
    print(f"[+] Loaded dataset: {sum(len(v) for v in data.values())} total records")

    print("[+] Connecting to Neo4j …")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()

    print("[+] Connecting to Elasticsearch …")
    es = Elasticsearch(
        ELASTICSEARCH_URL,
        headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"},
        **ES_AUTH_KWARGS,
    )
    _wait_for_es(es)

    print("[+] Loading FIRs …")
    load_firs_neo4j(driver, data["firs"])
    load_firs_es(es, data["firs"])

    print("[+] Loading CDRs …")
    load_cdrs_neo4j(driver, data["cdrs"])

    print("[+] Loading financial transactions …")
    load_financial_neo4j(driver, data["financial"])

    print("[+] Loading surveillance events …")
    load_surveillance_neo4j(driver, data["surveillance"])

    driver.close()
    print("[✔] Demo dataset loaded successfully.")
    print("    Kingpin: Vikram Singh (phone 9999988888)")
    print("    Run the demo: search 'Vikram Singh' → entity detail → network graph")


if __name__ == "__main__":
    main()
