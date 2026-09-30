"""Geography for ARGUS: offline geocoding, KDE hotspots and jurisdiction risk diffusion.

* `geocode` resolves a police station or district (from structured fields or FIR text) to a lat/lon using the bundled
  CSVs in data/ -- no external API. Coordinates are district/station centroids, i.e. coarse, and are labelled so.
* `hotspots` runs a Gaussian KDE over FIR points and returns gridded high/medium/low density regions as GeoJSON.
* `diffuse_risk` spreads a source jurisdiction's risk onto neighbours that share suspects with it.

Everything here is pure Python / numpy / scipy (imported lazily) so it can be unit-tested without databases.
"""

from __future__ import annotations

import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

DATA = Path(__file__).parent / "data"
LEAD_LABEL = "investigative lead — verify before use"


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@lru_cache(maxsize=1)
def _tables() -> dict:
    stations = {}
    with open(DATA / "ps_centroids.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            stations[_norm(r["station"])] = (float(r["lat"]), float(r["lon"]), r["station"])
    districts: dict[str, list[tuple[float, float, str, str]]] = {}
    with open(DATA / "india_district_centroids.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            districts.setdefault(_norm(r["district"]), []).append((float(r["lat"]), float(r["lon"]), r["district"], r["state"]))
    return {"stations": stations, "districts": districts}


@lru_cache(maxsize=1)
def adjacency() -> dict[str, list[str]]:
    return json.load(open(DATA / "district_adjacency.json"))


def geocode(station: Optional[str] = None, district: Optional[str] = None, text: Optional[str] = None, state: Optional[str] = None) -> Optional[dict]:
    """Best-effort location: {lat, lon, place, level, confidence, source}. None when nothing matches.

    Order: police station (exact) > district field > station/district named in free text. District names that exist in
    several states are only accepted when `state` disambiguates them (or the text names the state).
    """
    t = _tables()
    if station and _norm(station) in t["stations"]:
        lat, lon, name = t["stations"][_norm(station)]
        return {"lat": lat, "lon": lon, "place": name, "level": "station", "confidence": 0.8, "source": "ps_centroids.csv"}

    def district_hit(name: str, hint: Optional[str], base: float) -> Optional[dict]:
        rows = t["districts"].get(_norm(name))
        if not rows:
            return None
        if hint:
            narrowed = [r for r in rows if _norm(r[3]) == _norm(hint)]
            rows = narrowed or rows
        if len(rows) != 1:
            return None
        lat, lon, dname, st = rows[0]
        return {"lat": lat, "lon": lon, "place": f"{dname}, {st}", "level": "district", "confidence": base, "source": "india_district_centroids.csv"}

    if district:
        hit = district_hit(district, state, 0.6)
        if hit:
            return hit
    if text:
        norm_text = f" {_norm(text)} "
        for key, (lat, lon, name) in sorted(t["stations"].items(), key=lambda kv: -len(kv[0])):
            if f" {key} " in norm_text:
                return {"lat": lat, "lon": lon, "place": name, "level": "station", "confidence": 0.55, "source": "ps_centroids.csv"}
        for key in sorted(t["districts"], key=len, reverse=True):
            if len(key) >= 4 and f" {key} " in norm_text:
                hit = district_hit(key, state, 0.4)
                if hit:
                    return hit
    return None


def geocode_record(record: dict) -> Optional[dict]:
    """Geocode a FIR-like record: explicit lat/lon win, then station, district, then the description text."""
    lat, lon = record.get("lat"), record.get("lon")
    try:
        if lat is not None and lon is not None and -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180:
            return {"lat": float(lat), "lon": float(lon), "place": "as supplied", "level": "point", "confidence": 0.95, "source": "record"}
    except (TypeError, ValueError):
        pass
    return geocode(record.get("station"), record.get("district"), " ".join(str(record.get(k) or "") for k in ("description", "fir_text")), record.get("state"))


# ---------------------------------------------------------------------------
# Hotspots (Gaussian KDE)
# ---------------------------------------------------------------------------

LEVELS = (("high", 0.50), ("medium", 0.75), ("low", 0.90))  # share of total probability mass each region encloses


def _row_runs(mask_row) -> Iterable[tuple[int, int]]:
    start = None
    for i, v in enumerate(list(mask_row) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            yield start, i
            start = None


def hotspots(points: list[tuple[float, float]], grid: int = 60, pad_deg: float = 0.25, category: Optional[str] = None) -> dict:
    """KDE over (lat, lon) points -> GeoJSON FeatureCollection of high/medium/low density regions.

    Regions are highest-density regions: 'high' encloses the densest cells holding 50% of the probability mass, 'medium'
    75%, 'low' 90%. They are gridded (adjacent cells in a row are merged into one rectangle), not smoothed contours.
    Needs at least 3 distinct points; fewer returns an empty collection with a reason.
    """
    import numpy as np
    from scipy.stats import gaussian_kde

    empty = {"type": "FeatureCollection", "features": [], "properties": {"category": category, "points": len(points), "label": LEAD_LABEL}}
    pts = np.array(sorted(set((round(la, 5), round(lo, 5)) for la, lo in points)))
    if len(pts) < 3:
        return {**empty, "properties": {**empty["properties"], "reason": "Need at least 3 distinct FIR locations for a density estimate."}}
    try:
        if np.linalg.matrix_rank(np.cov(pts.T), tol=1e-12) < 2:
            raise np.linalg.LinAlgError("collinear")
        kde = gaussian_kde(pts.T)
    except np.linalg.LinAlgError:  # collinear points: no covariance to invert
        return {**empty, "properties": {**empty["properties"], "reason": "FIR locations are collinear; density is undefined."}}
    lat0, lat1 = pts[:, 0].min() - pad_deg, pts[:, 0].max() + pad_deg
    lon0, lon1 = pts[:, 1].min() - pad_deg, pts[:, 1].max() + pad_deg
    lats = np.linspace(lat0, lat1, grid)
    lons = np.linspace(lon0, lon1, grid)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    dens = kde(np.vstack([LA.ravel(), LO.ravel()])).reshape(grid, grid)
    total = dens.sum()
    order = np.sort(dens.ravel())[::-1]
    cum = np.cumsum(order) / total
    dlat, dlon = (lat1 - lat0) / (grid - 1), (lon1 - lon0) / (grid - 1)
    features = []
    for name, mass in LEVELS:
        threshold = order[min(int(np.searchsorted(cum, mass)), len(order) - 1)]
        mask = dens >= threshold
        polys = []
        for i in range(grid):
            for a, b in _row_runs(mask[i]):
                s, w, n, e = lats[i] - dlat / 2, lons[a] - dlon / 2, lats[i] + dlat / 2, lons[b - 1] + dlon / 2
                polys.append([[[round(w, 4), round(s, 4)], [round(e, 4), round(s, 4)], [round(e, 4), round(n, 4)], [round(w, 4), round(n, 4)], [round(w, 4), round(s, 4)]]])
        features.append({
            "type": "Feature",
            "properties": {"level": name, "mass": mass, "density_min": float(threshold), "cells": int(mask.sum())},
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })
    return {"type": "FeatureCollection", "features": features,
            "properties": {"category": category, "points": len(points), "method": "gaussian_kde", "label": LEAD_LABEL}}


# ---------------------------------------------------------------------------
# Risk diffusion
# ---------------------------------------------------------------------------

DECAY_ADJACENT = 0.6
NON_ADJACENT_FACTOR = 0.5


def diffuse_risk(
    source: str,
    risk: dict[str, float],
    shared: dict[str, int],
    total_suspects: int,
    *,
    decay: float = DECAY_ADJACENT,
    adjacent: Optional[set[str]] = None,
    top: int = 5,
) -> list[dict]:
    """One-hop diffusion: risk[neighbour] += risk[source] * (shared_suspects / total_suspects) * decay.

    `shared[n]` is how many of the source's suspects also appear in neighbour n. A neighbour that is not a known
    geographic neighbour of the source has its decay halved (shared suspects still link it, but by a weaker route);
    when the source is not a mapped district (`adjacent is None`) no such discount applies.
    Returns the top neighbours by diffused increment (capped at 100), each with its baseline and the working.
    """
    if total_suspects <= 0 or source not in risk:
        return []
    out = []
    for n, count in shared.items():
        if n == source or count <= 0:
            continue
        d = decay if adjacent is None or n in adjacent else decay * NON_ADJACENT_FACTOR
        increment = risk[source] * (count / total_suspects) * d
        out.append({
            "jurisdiction": n,
            "score": round(min(increment, 100.0), 1),
            "baseline_risk": round(risk.get(n, 0.0), 1),
            "projected_risk": round(min(risk.get(n, 0.0) + increment, 100.0), 1),
            "shared_suspects": count,
            "geographic_neighbour": None if adjacent is None else n in adjacent,  # None: source is not a mapped district
            "explanation": f"{count} of {total_suspects} suspects active in {source} also appear in {n}; "
                           f"{source} risk {risk[source]:.0f} × {count}/{total_suspects} × decay {d:.2f}.",
        })
    out.sort(key=lambda r: (-r["score"], r["jurisdiction"]))
    return out[:top]
