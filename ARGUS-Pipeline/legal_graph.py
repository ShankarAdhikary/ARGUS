"""Legal-charge and victim graph layer.

Turns the sections and victim fields in a FIR record into `LegalCharge` and `Victim` nodes in Neo4j:

    (:Suspect)-[:CHARGED_WITH]->(:LegalCharge)-[:IN_FIR]->(:FIR)
    (:Victim)-[:VICTIM_IN]->(:FIR)

The graph's person label in this codebase is `Suspect`. Victims are stored only as a keyed hash of the Aadhaar token;
the raw identifier is removed from the record before it reaches Elasticsearch or the graph.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Iterable, Optional

from config import VICTIM_HASH_PEPPER
from indic_text import WS_CATEGORIES, extract_legal_sections, normalise_text_digits

_VICTIM_FIELDS = ("victim_aadhaar", "victim_aadhaar_token", "victim_token")
_VICTIM_IN_TEXT = re.compile(
    r"(?:victim|survivor|complainant\s+victim|पीड़ित(?:ा)?)[^.\n]{0,80}?(?:aadhaar|aadhar|uid|आधार)[^\d]{0,15}(\d{4}\s?\d{4}\s?\d{4})",
    re.IGNORECASE,
)


def victim_hash(token: str) -> Optional[str]:
    """Keyed SHA-256 of an Aadhaar token. Keyed because the 12-digit space is small enough to brute-force a bare hash."""
    digits = re.sub(r"\D", "", normalise_text_digits(token or ""))
    if not digits:
        return None
    return hmac.new(VICTIM_HASH_PEPPER.encode(), digits.encode(), hashlib.sha256).hexdigest()


def charges_for_record(record: dict) -> list[dict]:
    """Charges for one FIR: explicit `sections` (list or string) plus any found in the free text. De-duplicated."""
    explicit = record.get("sections") or record.get("legal_sections") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    parts = [s if isinstance(s, str) else f"{s.get('act', '')} {s.get('section', '')}" for s in explicit]
    text = " ; ".join([*parts, str(record.get("description") or ""), str(record.get("fir_text") or "")])
    fir_id = record.get("fir_id") or ""
    charges = []
    for item in extract_legal_sections(text):
        charges.append({
            "charge_id": f"{fir_id}:{item['act']}:{item['section']}",
            "fir_id": fir_id,
            "section": item["section"],
            "act": item["act"],
            "offense_category": item["offense_category"],
            "date_filed": record.get("date"),
            "confidence": item["confidence"],
        })
    return charges


def victim_ids_for_record(record: dict) -> list[str]:
    """Hashed victim ids for one FIR. Raw tokens are read here and never returned."""
    tokens = [record.get(f) for f in _VICTIM_FIELDS if record.get(f)]
    tokens += _VICTIM_IN_TEXT.findall(normalise_text_digits(str(record.get("description") or "") + " " + str(record.get("fir_text") or "")))
    hashed = [h for h in (victim_hash(str(t)) for t in tokens) if h]
    return list(dict.fromkeys(hashed))


def strip_victim_pii(record: dict) -> dict:
    """Drop raw victim identifiers from a FIR record, and mask any in its free text, before it is indexed."""
    for field in _VICTIM_FIELDS:
        record.pop(field, None)
    for field in ("description", "fir_text"):
        if isinstance(record.get(field), str):
            record[field] = _VICTIM_IN_TEXT.sub(lambda m: m.group(0).replace(m.group(1), "[redacted]"), record[field])
    return record


def is_ws_category(category: str) -> bool:
    return category in WS_CATEGORIES


_CHARGE_CYPHER = """
MATCH (f:FIR {id: $fir_id})
MERGE (c:LegalCharge {charge_id: $charge_id})
SET c.fir_id = $fir_id, c.section = $section, c.act = $act, c.offense_category = $offense_category,
    c.date_filed = $date_filed, c.confidence = $confidence
MERGE (c)-[:IN_FIR]->(f)
WITH c
MATCH (s:Suspect {id: $accused})
MERGE (s)-[:CHARGED_WITH]->(c)
"""

_VICTIM_CYPHER = """
MATCH (f:FIR {id: $fir_id})
MERGE (v:Victim {victim_id: $victim_id})
ON CREATE SET v.jurisdiction = $jurisdiction, v.fir_ids = [], v.offense_categories = []
SET v.fir_ids = [x IN v.fir_ids WHERE x <> $fir_id] + $fir_id,
    v.offense_categories = [x IN v.offense_categories WHERE NOT x IN $categories] + $categories
SET v.repeat_count = size(v.fir_ids)
MERGE (v)-[:VICTIM_IN]->(f)
"""


def write_legal_layer(session, record: dict, accused: Optional[str]) -> dict:
    """Create LegalCharge and Victim nodes for one FIR (the FIR and Suspect nodes must already exist). Idempotent."""
    charges = charges_for_record(record)
    if accused:
        for charge in charges:
            session.run(_CHARGE_CYPHER, accused=accused, **charge)
    categories = sorted({c["offense_category"] for c in charges if c["offense_category"] not in {"UNCLASSIFIED", "OTHER"}})
    victims = victim_ids_for_record(record)
    for vid in victims:
        session.run(_VICTIM_CYPHER, victim_id=vid, fir_id=record.get("fir_id"), jurisdiction=record.get("jurisdiction"), categories=categories)
    return {"charges": len(charges), "victims": len(victims)}


def co_charge_matrix(pairs: Iterable[dict]) -> dict:
    """[{a, b, count}] -> {labels, matrix} symmetric co-occurrence matrix."""
    pairs = list(pairs)
    labels = sorted({p["a"] for p in pairs} | {p["b"] for p in pairs})
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for p in pairs:
        matrix[index[p["a"]]][index[p["b"]]] = matrix[index[p["b"]]][index[p["a"]]] = p["count"]
    return {"labels": labels, "matrix": matrix}


def ensure_schema(driver) -> None:
    """Apply migrations/001_legal_graph.cypher (idempotent). Called at worker and API start."""
    from pathlib import Path

    script = (Path(__file__).parent / "migrations" / "001_legal_graph.cypher").read_text()
    statements = [s.strip() for s in "\n".join(l for l in script.splitlines() if not l.startswith("//")).split(";") if s.strip()]
    with driver.session() as session:
        for statement in statements:
            session.run(statement)
