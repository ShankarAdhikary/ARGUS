"""Spatial mirrors in Postgres/PostGIS (firs, sightings) written at ingest. Failures here never block ingest."""

from __future__ import annotations

from typing import Optional

from db import get_cursor
from indic_text import WS_CATEGORIES


def upsert_fir(record: dict, geo: Optional[dict], categories: list[str], is_women_safety: bool) -> None:
    point = "ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography" if geo else "NULL"
    with get_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO firs (fir_id, jurisdiction, station, filed_on, offense_categories, is_women_safety, geocode_level, geocode_confidence, location)
            VALUES (%(fir_id)s, %(jurisdiction)s, %(station)s, %(filed_on)s, %(cats)s, %(ws)s, %(level)s, %(conf)s, {point})
            ON CONFLICT (fir_id) DO UPDATE SET jurisdiction = EXCLUDED.jurisdiction, station = EXCLUDED.station,
                filed_on = EXCLUDED.filed_on, offense_categories = EXCLUDED.offense_categories,
                is_women_safety = EXCLUDED.is_women_safety, geocode_level = EXCLUDED.geocode_level,
                geocode_confidence = EXCLUDED.geocode_confidence, location = EXCLUDED.location
            """,
            {
                "fir_id": record.get("fir_id"), "jurisdiction": record.get("jurisdiction") or "Unassigned",
                "station": record.get("station"), "filed_on": str(record.get("date") or "")[:10] or None,
                "cats": sorted(set(categories)), "ws": is_women_safety,
                "level": geo["level"] if geo else None, "conf": geo["confidence"] if geo else None,
                "lat": geo["lat"] if geo else None, "lon": geo["lon"] if geo else None,
            },
        )


def upsert_sighting(event_id: str, suspect: str, camera_id: Optional[str], seen_at: Optional[str], confidence: float, geo: Optional[dict]) -> None:
    if not geo:
        return
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO sightings (sighting_id, suspect_id, camera_id, seen_at, match_confidence, location)
            VALUES (%(id)s, %(s)s, %(c)s, NULLIF(%(t)s, '')::timestamptz, %(conf)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)
            ON CONFLICT (sighting_id) DO NOTHING
            """,
            {"id": event_id, "s": suspect, "c": camera_id, "t": seen_at or "", "conf": confidence, "lon": geo["lon"], "lat": geo["lat"]},
        )
        cur.execute(
            """
            INSERT INTO suspects (suspect_id, last_known_location, updated_at)
            VALUES (%(s)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, now())
            ON CONFLICT (suspect_id) DO UPDATE SET last_known_location = EXCLUDED.last_known_location, updated_at = now()
            WHERE suspects.updated_at <= now()
            """,
            {"s": suspect, "lon": geo["lon"], "lat": geo["lat"]},
        )


def ws_categories(categories: list[str]) -> bool:
    return any(c in WS_CATEGORIES for c in categories)


def fir_points(category: str, scope: Optional[str]) -> list[tuple[float, float]]:
    """(lat, lon) of geocoded FIRs for a category ('WOMEN_SAFETY' or an offense category), optionally one jurisdiction."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon
            FROM firs
            WHERE location IS NOT NULL
              AND (%(scope)s::text IS NULL OR jurisdiction = %(scope)s)
              AND (CASE WHEN %(cat)s = 'WOMEN_SAFETY' THEN is_women_safety ELSE %(cat)s = ANY(offense_categories) END)
            """,
            {"scope": scope, "cat": category},
        )
        return [(r["lat"], r["lon"]) for r in cur.fetchall()]
