"""Women Safety Risk Score (WSRS) per suspect.

Five factors, each 0-100, combined as a weighted average:

    recency 0.25 · repeat 0.30 · escalation 0.20 · network 0.15 · geographic 0.10

A "WS FIR" is a FIR carrying at least one Women-Safety-category charge (phase 2 LegalCharge nodes), or, when the FIR has
no sections on file, a FIR whose text or network label marks it as women-safety related (`ws_text`, lowest severity).

The score is an investigative lead, not evidence: every result carries its factor breakdown and a confidence value.
Pure Python (no Neo4j import) so it is unit-testable; `load_dataset` / `recompute` take a Neo4j session.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

from indic_text import WS_CATEGORIES

WS_NETWORKS = {"trafficking"}
WS_TERMS = re.compile(r"\b(minor|women|woman|girl|girls|stalk\w*|domestic violence|dowry|sexual\w*)\b", re.I)

WEIGHTS = {"recency": 0.25, "repeat": 0.30, "escalation": 0.20, "network": 0.15, "geographic": 0.10}
# Charge severity, used only to see whether a person's charges climb over time.
SEVERITY = {"HARASSMENT": 1, "STALKING": 2, "ASSAULT": 3, "DOMESTIC_VIOLENCE": 3, "TRAFFICKING": 4, "SEXUAL_OFFENCE": 5, "ACID_ATTACK": 5}
TEXT_ONLY_SEVERITY = 1
RECENCY_HALF_LIFE_DAYS = 90.0
REPEAT_DENOMINATOR_FLOOR = 3   # a lone FIR must not score 100 just because nobody else has more
GEO_ZERO_RADIUS_KM = 10.0      # score = 100 - 10 * radius_km, so 3 km -> 70
LEAD_LABEL = "investigative lead — verify before use"


def is_ws_text(record: dict) -> bool:
    return record.get("network") in WS_NETWORKS or bool(WS_TERMS.search(str(record.get("description") or "")))


def tier_for(score: float) -> str:
    return "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"


def _parse_date(value) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value)[:10])
        return d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def ws_firs(firs: list[dict]) -> list[dict]:
    """Reduce a suspect's FIRs to the women-safety ones: [{fir_id, date, severity, category, source, ...}] by date."""
    out = []
    for f in firs:
        cats = [c for c in (f.get("categories") or []) if c in WS_CATEGORIES]
        if cats:
            top = max(cats, key=lambda c: SEVERITY[c])
            out.append({**f, "severity": SEVERITY[top], "category": top, "source": "charge"})
        elif f.get("ws_text"):
            out.append({**f, "severity": TEXT_ONLY_SEVERITY, "category": None, "source": "text"})
    out.sort(key=lambda f: (str(f.get("date") or ""), str(f.get("fir_id"))))
    return out


def _pagerank(adj: dict[str, set[str]], damping: float = 0.85, iters: int = 60) -> dict[str, float]:
    n = len(adj)
    if not n:
        return {}
    rank = {k: 1.0 / n for k in adj}
    for _ in range(iters):
        dangling = sum(rank[k] for k, v in adj.items() if not v)
        new = {}
        for k in adj:
            incoming = sum(rank[j] / len(adj[j]) for j in adj[k])
            new[k] = (1 - damping) / n + damping * (incoming + dangling / n)
        rank = new
    return rank


def _co_accused_graph(ws_by_suspect: dict[str, list[dict]], fir_suspects: dict[str, set[str]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {s: set() for s in ws_by_suspect}
    for members in fir_suspects.values():
        ws_members = [m for m in members if m in adj]
        for a in ws_members:
            adj[a].update(m for m in ws_members if m != a)
    return adj


def _recency(firs: list[dict], now: datetime) -> tuple[float, str]:
    dates = [d for d in (_parse_date(f.get("date")) for f in firs) if d]
    if not dates:
        return 0.0, "No dated WS FIRs on record"
    days = max((now - max(dates)).days, 0)
    score = 100.0 * math.exp(-math.log(2) * days / RECENCY_HALF_LIFE_DAYS)
    if days <= 30:
        label = "Active in last 30 days"
    elif days <= 90:
        label = "Active in last 90 days"
    elif days <= 365:
        label = f"Last WS FIR {max(days // 30, 1)} months ago"
    else:
        label = "No WS FIR in over a year"
    return score, label


def _escalation(firs: list[dict]) -> tuple[float, str]:
    if len(firs) < 2:
        return 0.0, "Single WS FIR — no trend"
    running, steps = firs[0]["severity"], 0
    for f in firs[1:]:
        if f["severity"] > running:
            steps += 1
            running = f["severity"]
    climb = running - firs[0]["severity"]
    if not steps:
        return 0.0, "No escalation across FIRs"
    return min(100.0, 100.0 * climb / 4.0), f"Charges escalated {'once' if steps == 1 else f'{steps} times'}"


def _geographic(firs: list[dict]) -> tuple[float, str]:
    # Only surveyed points count: station/district centroids put every FIR of an area on the same spot, which would
    # read as "operates within 0 km" for anyone with two FIRs at one station.
    located = [(f["lat"], f["lon"]) for f in firs if f.get("geo_level") == "point" and f.get("lat") is not None and f.get("lon") is not None]
    if len(located) >= 2:
        c = (sum(p[0] for p in located) / len(located), sum(p[1] for p in located) / len(located))
        radius = max(_haversine_km(c, p) for p in located)
        return max(0.0, 100.0 - 100.0 * radius / GEO_ZERO_RADIUS_KM), f"Operates within {max(radius, 1):.0f}km radius" if radius >= 1 else "Operates within 1km radius"
    # No coordinates yet: concentration of WS FIRs in one station is weaker evidence, so it is discounted.
    places = [f.get("station") or f.get("jurisdiction") for f in firs if f.get("station") or f.get("jurisdiction")]
    if len(places) >= 2:
        place, n = Counter(places).most_common(1)[0]
        return 70.0 * n / len(places), f"{n} of {len(places)} WS FIRs in {place} (no coordinates)"
    return 0.0, "Not enough location data"


def score_suspect(firs: list[dict], *, max_repeat: int, co_ws: int, network_score: float, now: Optional[datetime] = None) -> Optional[dict]:
    """WSRS breakdown for one suspect from their WS FIRs, or None when they have none."""
    if not firs:
        return None
    now = now or datetime.now(timezone.utc)
    recency, recency_label = _recency(firs, now)
    repeat = min(100.0, 100.0 * len(firs) / max(max_repeat, REPEAT_DENOMINATOR_FLOOR))
    escalation, escalation_label = _escalation(firs)
    geographic, geo_label = _geographic(firs)
    factors = {
        "recency": (recency, recency_label),
        "repeat": (repeat, f"{len(firs)} WS FIR{'s' if len(firs) != 1 else ''} on record"),
        "escalation": (escalation, escalation_label),
        "network": (network_score, f"{co_ws} co-accused with WS history"),
        "geographic": (geographic, geo_label),
    }
    total = round(sum(WEIGHTS[k] * factors[k][0] for k in WEIGHTS), 1)
    text_only = sum(1 for f in firs if f["source"] == "text")
    confidence = 0.35 + 0.15 * min(len(firs), 3) - (0.15 if text_only else 0.0)
    return {
        "total": total,
        "tier": tier_for(total),
        "factors": {k: {"score": round(v[0]), "label": v[1]} for k, v in factors.items()},
        "weights": WEIGHTS,
        "ws_fir_count": len(firs),
        "confidence": round(min(max(confidence, 0.2), 0.9), 2),
        "explanation": (
            "Weighted average of five factors from FIRs with women-safety charges."
            + (f" {text_only} FIR(s) had no sections on file and were classed from text only." if text_only else "")
        ),
        "label": LEAD_LABEL,
    }


def compute_all(suspect_firs: dict[str, list[dict]], now: Optional[datetime] = None) -> dict[str, dict]:
    """WSRS for every suspect with women-safety FIRs. `suspect_firs`: suspect -> their FIR dicts (see `ws_firs`)."""
    ws_by_suspect = {s: w for s, w in ((s, ws_firs(f)) for s, f in suspect_firs.items()) if w}
    if not ws_by_suspect:
        return {}
    max_repeat = max(len(w) for w in ws_by_suspect.values())
    fir_suspects: dict[str, set[str]] = defaultdict(set)
    for s, firs in suspect_firs.items():
        for f in firs:
            fir_suspects[f["fir_id"]].add(s)
    adj = _co_accused_graph(ws_by_suspect, fir_suspects)
    pr = _pagerank(adj)
    top = max((pr[s] for s in adj if adj[s]), default=0.0)
    out = {}
    for s, w in ws_by_suspect.items():
        co = len(adj[s])
        net = 100.0 * pr[s] / top if co and top else 0.0
        out[s] = score_suspect(w, max_repeat=max_repeat, co_ws=co, network_score=net, now=now)
    return out


# ---------------------------------------------------------------------------
# Neo4j I/O
# ---------------------------------------------------------------------------

_LOAD = """
MATCH (s:Suspect)-[:LINKED_TO_FIR]->(f:FIR)
OPTIONAL MATCH (s)-[:CHARGED_WITH]->(c:LegalCharge)-[:IN_FIR]->(f)
RETURN s.id AS suspect, f.id AS fir_id, f.date AS date, f.jurisdiction AS jurisdiction, f.station AS station,
       f.lat AS lat, f.lon AS lon, f.geo_level AS geo_level, coalesce(f.ws_text, false) AS ws_text,
       [x IN collect(DISTINCT c.offense_category) WHERE x IS NOT NULL] AS categories
"""

_WRITE = """
MATCH (s:Suspect {id: $id})
SET s.wsrs_score = $total, s.wsrs_tier = $tier, s.wsrs_breakdown = $breakdown, s.wsrs_updated = datetime()
"""
_CLEAR = """
MATCH (s:Suspect {id: $id}) REMOVE s.wsrs_score, s.wsrs_tier, s.wsrs_breakdown, s.wsrs_updated
"""


def load_dataset(session) -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = defaultdict(list)
    for r in session.run(_LOAD):
        if r["suspect"] and r["fir_id"]:
            data[r["suspect"]].append({k: r[k] for k in ("fir_id", "date", "jurisdiction", "station", "lat", "lon", "geo_level", "ws_text", "categories")})
    return data


def recompute(session, suspects: Optional[list[str]] = None, now: Optional[datetime] = None) -> dict[str, dict]:
    """Recompute WSRS and store it on the Suspect nodes.

    The score depends on dataset-wide context (max repeat count, PageRank), so the whole graph is read once, but only
    `suspects` and their co-accused are written (None = everyone). Suspects that no longer have WS FIRs are cleared.
    """
    data = load_dataset(session)
    results = compute_all(data, now)
    if suspects is None:
        targets = set(data)
    else:
        targets = set(suspects)
        fir_ids = {f["fir_id"] for s in suspects for f in data.get(s, [])}
        targets |= {s for s, firs in data.items() if any(f["fir_id"] in fir_ids for f in firs)}
    for s in targets:
        r = results.get(s)
        if r:
            session.run(_WRITE, id=s, total=r["total"], tier=r["tier"], breakdown=json.dumps(r))
        else:
            session.run(_CLEAR, id=s)
    return {s: results[s] for s in targets if s in results}
