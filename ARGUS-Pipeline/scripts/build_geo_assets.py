"""Build the bundled India district assets from the GADM-derived district GeoJSON (594 districts, ~2009 boundaries).

    python scripts/build_geo_assets.py path/to/india_district.geojson

Writes:
  data/india_district_centroids.csv      state,district,lat,lon      (geocoder input, no external API at run time)
  data/district_adjacency.json           {"State|District": ["State|District", ...]}  (shared-boundary neighbours)
  ../ARGUS-Frontend/src/assets/india_districts.geojson   simplified polygons for the map

Source: https://raw.githubusercontent.com/geohacker/india/master/district/india_district.geojson
District boundaries are pre-2010; newer districts are not present. Replace with the current LGD master in production.
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIMPLIFY_DEG = 0.02
MIN_RING_AREA = 0.002  # square degrees; drops specks


def _rings(geometry):
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return [p[0] for p in polys]  # outer rings only


def _area_centroid(ring):
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    a /= 2
    return (abs(a), cx / (6 * a), cy / (6 * a)) if a else (0, ring[0][0], ring[0][1])


def _dp(points, tol):
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
    idx, dmax = 0, 0.0
    for i in range(1, len(points) - 1):
        d = abs(dy * points[i][0] - dx * points[i][1] + x2 * y1 - y2 * x1) / norm
        if d > dmax:
            idx, dmax = i, d
    if dmax > tol:
        return _dp(points[: idx + 1], tol)[:-1] + _dp(points[idx:], tol)
    return [points[0], points[-1]]


def main(src):
    data = json.load(open(src))
    centroids, vertices, features = [], {}, []
    for f in data["features"]:
        state, district = f["properties"]["NAME_1"], f["properties"]["NAME_2"]
        key = f"{state}|{district}"
        rings = _rings(f["geometry"])
        biggest = max(rings, key=lambda r: _area_centroid(r)[0])
        _, cx, cy = _area_centroid(biggest)
        centroids.append((state, district, round(cy, 4), round(cx, 4)))
        vertices[key] = {(round(x, 2), round(y, 2)) for r in rings for x, y in r}
        polys = []
        for r in rings:
            if _area_centroid(r)[0] < MIN_RING_AREA:
                continue
            pts = [(x, y) for x, y in r]
            # A closed ring has a zero-length baseline, so split it at the point farthest from the start first.
            far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
            s = _dp(pts[: far + 1], SIMPLIFY_DEG)[:-1] + _dp(pts[far:], SIMPLIFY_DEG)
            if len(s) >= 4:
                polys.append([[[round(x, 3), round(y, 3)] for x, y in s]])
        if polys:
            features.append({"type": "Feature", "properties": {"id": key, "district": district, "state": state},
                             "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    with open(ROOT / "data/india_district_centroids.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "district", "lat", "lon"])
        w.writerows(sorted(centroids))
    by_vertex = defaultdict(set)
    for key, vs in vertices.items():
        for v in vs:
            by_vertex[v].add(key)
    shared = defaultdict(lambda: defaultdict(int))
    for keys in by_vertex.values():
        for a in keys:
            for b in keys:
                if a != b:
                    shared[a][b] += 1
    adjacency = {a: sorted(b for b, n in nb.items() if n >= 2) for a, nb in shared.items()}
    json.dump(adjacency, open(ROOT / "data/district_adjacency.json", "w"), separators=(",", ":"), ensure_ascii=False)
    json.dump({"type": "FeatureCollection", "features": features}, open(ROOT.parent / "ARGUS-Frontend/src/assets/india_districts.geojson", "w"), separators=(",", ":"))
    print(f"{len(centroids)} districts, {len(features)} polygons, {sum(map(len, adjacency.values()))} adjacency links")


if __name__ == "__main__":
    main(sys.argv[1])
