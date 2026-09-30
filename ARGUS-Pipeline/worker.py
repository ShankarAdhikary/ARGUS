import json
import time
import redis
from elasticsearch import Elasticsearch
from minio import Minio
from neo4j import GraphDatabase
from config import (
    BUCKET_NAME,
    ELASTICSEARCH_URL,
    ES_AUTH_KWARGS,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)
from indic_text import canonical_name, name_aliases
import geo_risk
import geo_store
import wsrs
from legal_graph import charges_for_record, ensure_schema, strip_victim_pii, write_legal_layer
from jurisdictions import jurisdiction_for_station
from platform_api import check_and_fire_alerts

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    password=REDIS_PASSWORD,
    socket_timeout=None,
    socket_keepalive=True,
)

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)

es_client = Elasticsearch(
    ELASTICSEARCH_URL,
    headers={
        "Accept": "application/vnd.elasticsearch+json; compatible-with=8"
    },
    **ES_AUTH_KWARGS,
)

neo4j_driver = GraphDatabase.driver(
    NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
)


def update_job(event, status, message=None):
    if not event.get("job_id"):
        return
    payload = {**event, "status": status, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if message:
        payload["failure_reason"] = message
    redis_client.set(f"argus:jobs:{event['job_id']}", json.dumps(payload))

def process_queue():
    print("[*] ARGUS Pipeline Worker started. Listening for events...")
    try:
        ensure_schema(neo4j_driver)
    except Exception as exc:
        print(f"[!] Graph schema migration failed (will retry on next start): {exc}")
    while True:
        event = {}
        try:
            queue_item = redis_client.blpop("argus_ingest_queue", timeout=0)
            if not queue_item:
                continue

            _, raw_data = queue_item
            event = json.loads(raw_data.decode("utf-8"))

            filename = event["filename"]
            storage_path = event["storage_path"]
            dataset_type = event.get("dataset_type")
            print(f"[+] Processing file from queue: {filename}")
            update_job(event, "processing")

            response = minio_client.get_object(BUCKET_NAME, storage_path)
            try:
                try:
                    file_data = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Malformed JSON dataset: {exc.msg if hasattr(exc, 'msg') else exc}") from exc
            finally:
                response.close()
                response.release_conn()

            if not isinstance(file_data, list):
                raise ValueError("Dataset must be a JSON array of records.")
            if dataset_type not in {"fir", "cdr", "financial", "surveillance"}:
                raise ValueError(
                    "Unsupported dataset format: filename must identify a FIR, CDR, financial, or surveillance dataset."
                )

            if dataset_type == "fir":
                touched_entities = []
                for record in file_data:
                    # Every FIR is stamped with the jurisdiction that owns its station, so search and graph
                    # results can be scoped to it. An explicit value in the file wins.
                    record.setdefault("jurisdiction", jurisdiction_for_station(record.get("station")))
                    # Victim identifiers are hashed into the graph below; the raw values never reach Elasticsearch.
                    legal_record = dict(record)
                    strip_victim_pii(record)
                    # Canonical Latin key goes into the index; the original script is kept as an alias.
                    record["accused_canonical"] = canonical_name(record.get("accused") or "")
                    record["accused_aliases"] = name_aliases(record.get("accused") or "")
                    # Offline geocode (bundled station/district centroids); the level/confidence say how coarse it is.
                    geo = geo_risk.geocode_record(record)
                    if geo:
                        record["location"] = {"lat": geo["lat"], "lon": geo["lon"]}
                        record["geocode"] = {"level": geo["level"], "confidence": geo["confidence"], "place": geo["place"]}
                    es_client.index(
                        index="argus-firs", id=record.get("fir_id"), document=record
                    )

                    accused = record.get("accused", "UNKNOWN")
                    mobile = (record.get("mobile") or "").strip()
                    with neo4j_driver.session() as session:
                        session.run(
                            """
                            MERGE (s:Suspect {id: $suspect_id})
                            MERGE (f:FIR {id: $fir_id})
                            MERGE (s)-[link:LINKED_TO_FIR]->(f)
                            SET f.date = $date, f.jurisdiction = $jurisdiction, link.date = $date, link.source_record_id = $fir_id,
                                s.canonical = $canonical, f.station = $station, f.ws_text = $ws_text,
                                f.lat = $lat, f.lon = $lon, f.geo_level = $geo_level
                            // Without this the suspect/FIR records and the call
                            // graph stay disconnected, so no network path exists
                            // between a named person and the numbers they use.
                            FOREACH (_ IN CASE WHEN $mobile <> '' THEN [1] ELSE [] END |
                                MERGE (p:Phone {id: $mobile})
                                MERGE (s)-[:USES_PHONE]->(p)
                            )
                            """,
                            suspect_id=accused,
                            canonical=record["accused_canonical"],
                            station=record.get("station"),
                            ws_text=wsrs.is_ws_text(record),
                            lat=geo["lat"] if geo else None,
                            lon=geo["lon"] if geo else None,
                            geo_level=geo["level"] if geo else None,
                            fir_id=record.get("fir_id"),
                            mobile=mobile,
                            date=record.get("date"),
                            jurisdiction=record["jurisdiction"],
                        )
                        # IPC/BNS charges and (hashed) victims from this FIR.
                        write_legal_layer(session, legal_record, accused)
                    try:
                        cats = [c["offense_category"] for c in charges_for_record(legal_record)]
                        geo_store.upsert_fir(record, geo, cats, geo_store.ws_categories(cats) or wsrs.is_ws_text(record))
                    except Exception as geo_exc:
                        print(f"[!] PostGIS mirror failed (non-fatal): {geo_exc}")
                    touched_entities.append(accused)
                    if mobile:
                        touched_entities.append(mobile)
                # WSRS depends on the whole graph, so refresh it for the accused in this file and their co-accused.
                try:
                    with neo4j_driver.session() as session:
                        wsrs.recompute(session, sorted({r.get("accused") for r in file_data if r.get("accused")}))
                except Exception as wsrs_exc:
                    print(f"[!] WSRS recompute failed (non-fatal): {wsrs_exc}")
                try:
                    check_and_fire_alerts(touched_entities)
                except Exception as alert_exc:
                    print(f"[!] Alert check failed (non-fatal): {alert_exc}")
                print(f"[✔] Successfully indexed FIRs for {filename}")

            elif dataset_type == "cdr":
                touched_entities = []
                for record in file_data:
                    es_client.index(
                        index="argus-cdrs", id=record.get("call_id"), document=record
                    )

                    with neo4j_driver.session() as session:
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
                            duration=record.get("duration_sec")
                        )
                    touched_entities.extend([record.get("caller"), record.get("receiver")])
                try:
                    check_and_fire_alerts(touched_entities)
                except Exception as alert_exc:
                    print(f"[!] Alert check failed (non-fatal): {alert_exc}")
                print(f"[✔] Successfully indexed CDRs for {filename}")
            elif dataset_type == "financial":
                touched_entities = []
                for record in file_data:
                    account_id = record.get("account_id")
                    counterparty = record.get("counterparty_account")
                    if not account_id or not counterparty:
                        raise ValueError("Financial record requires account_id and counterparty_account.")
                    with neo4j_driver.session() as session:
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
                            account_id=account_id,
                            counterparty=counterparty,
                            bank=record.get("bank"),
                            currency=record.get("currency", "INR"),
                            transaction_id=record.get("transaction_id"),
                            amount=record.get("amount"),
                            timestamp=record.get("timestamp"),
                            direction=record.get("direction", "outgoing"),
                        )
                    touched_entities.extend([account_id, counterparty])
                try:
                    check_and_fire_alerts(touched_entities)
                except Exception as alert_exc:
                    print(f"[!] Alert check failed (non-fatal): {alert_exc}")
                print(f"[✔] Successfully indexed financial transactions for {filename}")
            elif dataset_type == "surveillance":
                touched_entities = []
                for record in file_data:
                    suspect_name = record.get("suspect_name")
                    camera_id = record.get("camera_id")
                    zone = record.get("zone")
                    if not suspect_name or not camera_id:
                        continue
                    with neo4j_driver.session() as session:
                        session.run(
                            """
                            MERGE (c:Camera {id: $camera_id})
                              ON CREATE SET c.zone = $zone
                              ON MATCH SET c.zone = $zone
                            MERGE (s:Suspect {id: $suspect_id})
                            MERGE (s)-[obs:SEEN_AT {source_id: $event_id}]->(c)
                            SET obs.timestamp = $timestamp, obs.confidence = $confidence,
                                obs.evidence = 'Camera sighting at ' + $zone
                            """,
                            camera_id=camera_id,
                            zone=zone or "",
                            suspect_id=suspect_name,
                            event_id=record.get("event_id", camera_id),
                            timestamp=record.get("timestamp", ""),
                            confidence=record.get("match_confidence", 0.0),
                        )
                    touched_entities.append(suspect_name)
                    try:
                        geo_store.upsert_sighting(
                            str(record.get("event_id", camera_id)), suspect_name, camera_id, record.get("timestamp"),
                            record.get("match_confidence", 0.0), geo_risk.geocode_record({**record, "station": zone, "description": zone}),
                        )
                    except Exception as geo_exc:
                        print(f"[!] PostGIS sighting mirror failed (non-fatal): {geo_exc}")
                try:
                    check_and_fire_alerts(touched_entities)
                except Exception as alert_exc:
                    print(f"[!] Alert check failed (non-fatal): {alert_exc}")
                print(f"[✔] Successfully loaded surveillance events for {filename}")
            else:
                raise ValueError(f"Unsupported dataset type: {dataset_type}")

            update_job(event, "processed")

        except Exception as e:
            print(f"[!] Error processing queue item: {str(e)}")
            try:
                update_job(event, "quarantined", str(e))
            except Exception:
                pass

        time.sleep(1)

if __name__ == "__main__":
    process_queue()
