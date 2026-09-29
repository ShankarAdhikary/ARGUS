"""Seed demo cases, entity links, notes, and alert watch-rules into Postgres.

Idempotent — skips cases whose title already exists so it is safe to re-run.

Usage (inside the API container):
    python seed_demo_cases.py

Usage (locally, with .env loaded):
    set -o allexport && source .env && set +o allexport
    python seed_demo_cases.py
"""
from __future__ import annotations

import sys
import psycopg2
import psycopg2.extras
from config import (
    POSTGRES_DB, POSTGRES_HOST, POSTGRES_PASSWORD, POSTGRES_PORT, POSTGRES_USER,
)

# ---------------------------------------------------------------------------
# Demo cases
# Each entry: (title, fir_number, jurisdiction, status, is_sensitive,
#              sensitivity_reason, category, entities, notes)
#
# entities: list of (entity_type, entity_value, linked_by)
# notes:    list of (author, content)
# ---------------------------------------------------------------------------

DEMO_CASES = [
    (
        "Operation Nexus — Trafficking Network",
        "FIR-2026-0032",
        "Central District",
        "open",
        False, None,
        "human_trafficking",
        [
            ("person",  "Vikram Singh",  "Rahul Verma"),
            ("person",  "Rohan Mehta",   "Rahul Verma"),
            ("person",  "Imran Khan",    "Rahul Verma"),
            ("phone",   "9999988888",    "Rahul Verma"),
            ("phone",   "9888877777",    "Rahul Verma"),
        ],
        [
            ("Rahul Verma",
             "Initial FIR filed at Central Police Station. Vikram Singh identified as primary accused. "
             "CDR analysis reveals 24 calls to known lieutenant Rohan Mehta over 30 days."),
            ("Ayesha Khan",
             "Network graph analysis complete. Three-tier hierarchy confirmed: kingpin → 3 lieutenants → 9 recruiters → 28 street operatives. "
             "Louvain community detection identifies three distinct cells operating in separate zones."),
            ("D. Iyer",
             "Case escalated to State HQ for coordination. Surveillance footage from Central Bus Terminal "
             "and East Market corroborates CDR timeline. Cross-zone movement pattern flagged."),
        ],
    ),
    (
        "Operation White Powder — Narcotics Supply Chain",
        "FIR-2026-0055",
        "Harbor Division",
        "under_review",
        True,
        "Ongoing covert surveillance — disclosure would compromise field operatives",
        "narcotics",
        [
            ("person",  "Rajat Sharma",  "Ayesha Khan"),
            ("phone",   "9777766666",    "Ayesha Khan"),
            ("location","Harbor Docks",  "Ayesha Khan"),
        ],
        [
            ("Ayesha Khan",
             "Rajat Sharma linked to 3 FIRs across Harbor and East Market zones. "
             "Primary phone 9777766666 shows 18 calls to unregistered numbers — burner heuristic triggered."),
            ("Rahul Verma",
             "Financial analysis: multiple sub-threshold transfers (₹8,500–₹9,900) routed through "
             "ACCT-0034 → ACCT-0002-HUB. Classic structuring pattern. Referred to financial crimes unit."),
        ],
    ),
    (
        "Operation Iron Fist — Extortion Racket",
        "FIR-2026-0101",
        "East Market Zone",
        "open",
        False, None,
        "extortion",
        [
            ("person",  "Kabir Verma",   "Rahul Verma"),
            ("phone",   "9666655555",    "Rahul Verma"),
            ("location","East Market",   "Rahul Verma"),
            ("location","Ring Road Junction", "Rahul Verma"),
        ],
        [
            ("Rahul Verma",
             "Kabir Verma identified as ringleader of extortion network operating across East Market. "
             "Victims include 14 traders paying weekly 'protection' sums of ₹2,000–₹15,000."),
            ("Ayesha Khan",
             "Two lieutenants identified from CDR betweenness analysis — both act as collection intermediaries. "
             "Street operatives rotate weekly to avoid pattern detection."),
        ],
    ),
    (
        "Operation Hundi — Hawala Network",
        "FIR-2026-0188",
        "Finance Quarter",
        "under_review",
        True,
        "Cross-border financial intelligence — classified pending ED coordination",
        "hawala",
        [
            ("person",           "Farhan Malhotra", "Ayesha Khan"),
            ("phone",            "9555544444",      "Ayesha Khan"),
            ("financial_account","ACCT-0098-HUB",   "Ayesha Khan"),
            ("financial_account","ACCT-0098-SHELL", "Ayesha Khan"),
            ("location",         "Finance Quarter", "Ayesha Khan"),
        ],
        [
            ("Ayesha Khan",
             "Farhan Malhotra operates a hawala desk under the cover of a licensed money-exchange outlet. "
             "Inward remittances from three foreign accounts traced to shell ACCT-0098-SHELL."),
            ("D. Iyer",
             "ED coordination initiated. Transaction layering: mule → recruiter → lieutenant → hub → shell "
             "confirmed across 338 financial records. Total laundered amount estimated ₹4.2 Cr."),
            ("Rahul Verma",
             "Cross-network link detected: Farhan Malhotra's recruiter shares a phone contact "
             "with Kabir Verma's extortion network. Joint surveillance recommended."),
        ],
    ),
    (
        "Operation Dark Web — Cybercrime Infrastructure",
        "FIR-2026-0247",
        "Cyber Hub",
        "open",
        False, None,
        "cybercrime",
        [
            ("person",  "Aditya Kapoor", "Ayesha Khan"),
            ("phone",   "9444433333",    "Ayesha Khan"),
            ("location","Cyber Hub",     "Ayesha Khan"),
        ],
        [
            ("Ayesha Khan",
             "Aditya Kapoor linked to phishing infrastructure targeting customers of 4 nationalised banks. "
             "SIM-swap fraud confirmed on 3 victims. Total financial loss ₹38 lakhs."),
            ("Rahul Verma",
             "Call-centre scam operation identified at Industrial Zone B. "
             "19 operators named in FIRs across 3 states. Ransomware strain linked to Hospital data breach."),
        ],
    ),
]

# Alert watch-rules for all five kingpins + key phones
ALERT_WATCH_ENTITIES = [
    ("Vikram Singh",    "INV001"),
    ("Rajat Sharma",    "INV001"),
    ("Kabir Verma",     "ANL001"),
    ("Farhan Malhotra", "ANL001"),
    ("Aditya Kapoor",   "ANL001"),
    ("9999988888",      "INV001"),
    ("9777766666",      "INV001"),
    ("Rohan Mehta",     "INV001"),
    ("Imran Khan",      "INV001"),
]


def run() -> None:
    conn = psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        user=POSTGRES_USER, password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    inserted_cases = 0
    inserted_entities = 0
    inserted_notes = 0

    for (title, fir_number, jurisdiction, status, is_sensitive,
         sensitivity_reason, category, entities, notes) in DEMO_CASES:

        # Idempotency check
        cur.execute("SELECT case_id FROM cases WHERE title = %s", (title,))
        existing = cur.fetchone()
        if existing:
            print(f"  [skip] Case already exists: {title!r}")
            case_id = str(existing["case_id"])
        else:
            cur.execute(
                """
                INSERT INTO cases
                    (title, fir_number, jurisdiction, status,
                     is_sensitive, sensitivity_reason, category,
                     opened_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,
                        now() - (random()*interval '60 days'))
                RETURNING case_id
                """,
                (title, fir_number, jurisdiction, status,
                 is_sensitive, sensitivity_reason, category),
            )
            case_id = str(cur.fetchone()["case_id"])
            inserted_cases += 1
            print(f"  [+] Case: {title!r}")

        for entity_type, entity_value, linked_by in entities:
            cur.execute(
                """
                INSERT INTO case_entity_links
                    (case_id, entity_type, entity_value, linked_by)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (case_id, entity_type, entity_value) DO NOTHING
                """,
                (case_id, entity_type, entity_value, linked_by),
            )
            if cur.rowcount:
                inserted_entities += 1

        for author, content in notes:
            # Avoid duplicate notes (same case + author + first 40 chars)
            cur.execute(
                "SELECT 1 FROM case_notes WHERE case_id=%s AND author=%s AND LEFT(content,40)=%s",
                (case_id, author, content[:40]),
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO case_notes (case_id, author, content) VALUES (%s,%s,%s)",
                    (case_id, author, content),
                )
                inserted_notes += 1

    # Alert watch-rules
    inserted_rules = 0
    for entity_value, created_by in ALERT_WATCH_ENTITIES:
        cur.execute(
            "SELECT 1 FROM alert_rules WHERE entity_value = %s", (entity_value,)
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO alert_rules (entity_value, created_by) VALUES (%s,%s)",
                (entity_value, created_by),
            )
            inserted_rules += 1
            print(f"  [+] Alert rule: watch {entity_value!r}")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n[✔] Seeding complete")
    print(f"    Cases inserted    : {inserted_cases}")
    print(f"    Entity links      : {inserted_entities}")
    print(f"    Notes             : {inserted_notes}")
    print(f"    Alert watch-rules : {inserted_rules}")


if __name__ == "__main__":
    run()
