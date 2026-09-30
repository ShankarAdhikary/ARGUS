"""Measure fingerprint matching error rates offline and, only if the data is sufficient, write the calibration file.

    docker compose exec api python scripts/evaluate_fingerprint.py --manifest /data/fp/manifest.csv --image-root /data/fp
    docker compose exec api python scripts/evaluate_fingerprint.py --manifest ... --dry-run     # report only, never writes

manifest.csv (header required):
    path        image file, relative to --image-root
    finger_id   ground truth: images with the same id are the same finger of the same person
    print_type  `rolled` (the gallery; also usable as probes) or `latent`
    origin      optional. `scan` (a scanned or live-scan print), `lift` (a real crime-scene latent lift) or `crop` (a cropped
                rolled print used as a stand-in). Only `lift` counts as a latent for calibration. Default: scan for rolled,
                and for latent nothing is assumed: undeclared latents are treated as crops.
    session     optional impression id. Two images with the same session are the same impression and are never compared:
                a crop must carry its parent print's session so it is not scored against itself.
    subject_id  optional person id: different fingers of one person are then reported as their own (harder) impostor group.

What it does. Every probe is scored against every rolled gallery print with the same engine, quality gate and minutiae floors
the service applies (a print the service would refuse is refused here too and is counted, not silently dropped). Same finger,
different impression = genuine trial; different finger = impostor trial. Modes:
    rolled        rolled probes vs the rolled gallery
    latent        real lifts (origin=lift) vs the gallery: the only latent mode that can set a threshold
    latent_crop   crops/undeclared latents vs the gallery: shown for information, never used for calibration

The report has, per mode, the threshold table for scores 20 to 100 in steps of 5: the false-accept rate, the 95% upper bound
on it ("could be as high as"), the false-reject rate and the counts. SourceAFIS scores are not a percentage and can exceed 100.
"0 false accepts in 180 trials" is not a false-accept rate of zero: read the upper-bound column.

The calibration file is written ONLY for a mode with enough data (fingerprint.CALIBRATION_MIN: >= 100 fingers, >= 100 genuine
trials, >= 10,000 impostor trials; latent also >= 50 real lifts) and only with --provenance saying where the data came from
(e.g. the dataset name and the licence or data-sharing agreement reference). Otherwise the script says what is missing, exits 2
and writes nothing, and the service keeps the SourceAFIS default thresholds for that mode. The script cannot tell real prints from
synthetic ones; that is what --provenance is for, and why it is required.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import fingerprint as fp
from evalstats import eer_of, frr_at, rate_at, threshold_at_far, upper_bound

THRESHOLD_GRID = list(range(20, 101, 5))
FAR_POINTS = {"far_1pct": 0.01, "far_0_1pct": 0.001, "far_0_01pct": 0.0001}
ORIGINS = ("scan", "lift", "crop")
MODES = ("rolled", "latent", "latent_crop")


@dataclass
class Entry:
    path: str
    finger_id: str
    print_type: str
    origin: str
    session: str
    subject_id: str
    template: fp.Template


# ---------------------------------------------------------------------------
# Manifest and templates
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    need = {"path", "finger_id", "print_type"}
    if not rows or not need <= set(rows[0]):
        raise SystemExit(f"manifest needs columns {sorted(need)} (optional: origin, session, subject_id)")
    for k, r in enumerate(rows):
        r["print_type"] = r["print_type"].strip().lower()
        if r["print_type"] not in fp.PRINT_TYPES:
            raise SystemExit(f"print_type must be one of {fp.PRINT_TYPES}, got {r['print_type']!r} ({r['path']})")
        origin = (r.get("origin") or "").strip().lower() or ("scan" if r["print_type"] == "rolled" else "crop")   # never assume a lift
        if origin not in ORIGINS:
            raise SystemExit(f"origin must be one of {ORIGINS}, got {origin!r} ({r['path']})")
        r["origin"] = origin
        r["session"] = (r.get("session") or "").strip() or f"file-{k}"
        r["subject_id"] = (r.get("subject_id") or "").strip()
    return rows, hashlib.sha256(raw).hexdigest()


def prepare(rows: list[dict], engine, image_root: Path) -> tuple[list[Entry], list[dict]]:
    """Templates for every usable image, under the same gates as the service. Returns (entries, refused)."""
    entries, refused = [], []
    precheck = getattr(engine, "precheck", None)
    for r in rows:
        try:
            image = (image_root / r["path"]).read_bytes()
            if precheck is not None:
                quality = precheck(image)
                if not quality["passed"]:
                    raise fp.LowQualityError(quality["reason"], quality["quality_score"])
            template = engine.extract(image)
            floor = fp.min_minutiae_for(r["print_type"])
            if template.minutiae < floor:
                raise fp.LowQualityError(f"{template.minutiae} minutiae, the {r['print_type']} floor is {floor}")
        except fp.LowQualityError as exc:
            refused.append({"path": r["path"], "print_type": r["print_type"], "reason": str(exc)})
            continue
        except (OSError, ValueError) as exc:
            refused.append({"path": r["path"], "print_type": r["print_type"], "reason": f"unreadable: {exc}"})
            continue
        entries.append(Entry(r["path"], r["finger_id"], r["print_type"], r["origin"], r["session"], r["subject_id"], template))
    return entries, refused


# ---------------------------------------------------------------------------
# Trials
# ---------------------------------------------------------------------------

def mode_of(entry: Entry) -> str:
    if entry.print_type == "rolled":
        return "rolled"
    return "latent" if entry.origin == "lift" else "latent_crop"


def score_trials(entries: list[Entry], engine, threads: int = 1) -> dict:
    """{mode: {"genuine": [...], "impostor": [...], "impostor_same_subject": [...], "probes": n}} for every probe against the rolled gallery.

    Directional, as in the service (probe = the query, candidate = the enrolled print). A pair is skipped if it is the same
    image or the same impression (equal session): a crop is never scored against its own parent.
    """
    gallery = [e for e in entries if e.print_type == "rolled"]
    out = {m: {"genuine": [], "impostor": [], "impostor_same_subject": [], "probes": 0} for m in MODES}

    def run(probe: Entry) -> tuple[str, list, list, list]:
        genuine, impostor, same_subject = [], [], []
        for g in gallery:
            if g is probe or g.session == probe.session:
                continue
            score = float(engine.score(probe.template, g.template))
            if g.finger_id == probe.finger_id:
                genuine.append(score)
            elif probe.subject_id and g.subject_id == probe.subject_id:
                same_subject.append(score)
            else:
                impostor.append(score)
        return mode_of(probe), genuine, impostor, same_subject

    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:      # one thread per probe, so a probe's matcher is never shared
        for mode, g, i, s in pool.map(run, entries):
            out[mode]["genuine"] += g
            out[mode]["impostor"] += i
            out[mode]["impostor_same_subject"] += s
            out[mode]["probes"] += 1
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def percentiles(scores: np.ndarray, points=(0, 5, 50, 95, 99, 99.9, 100)) -> dict:
    return {} if len(scores) == 0 else {f"p{p:g}": round(float(np.percentile(scores, p)), 1) for p in points}


def threshold_table(genuine: np.ndarray, impostor: np.ndarray, grid=THRESHOLD_GRID) -> list[dict]:
    rows = []
    for t in grid:
        false_accepts = int((impostor >= t).sum())
        far = rate_at(impostor, t, above=True)
        frr = rate_at(genuine, t, above=False)
        rows.append({"threshold": t, "far": far, "far_upper_95": upper_bound(false_accepts, len(impostor)), "false_accepts": false_accepts,
                     "impostor_trials": int(len(impostor)), "frr": frr, "false_rejects": int((genuine < t).sum()), "genuine_trials": int(len(genuine))})
    return rows


def analyse_mode(trials: dict) -> dict:
    genuine = np.array(trials["genuine"], dtype=float)
    hard = np.array(trials["impostor_same_subject"], dtype=float)
    impostor = np.concatenate([np.array(trials["impostor"], dtype=float), hard])      # the harder same-person impostors count as impostors
    eer, eer_t = eer_of(genuine, impostor)
    thresholds = {name: threshold_at_far(impostor, p) for name, p in FAR_POINTS.items()}
    return {
        "probes": trials["probes"], "genuine_trials": int(len(genuine)), "impostor_trials": int(len(impostor)),
        "impostor_same_person_other_finger": int(len(hard)),
        "genuine_scores": percentiles(genuine), "impostor_scores": percentiles(impostor),
        "same_person_other_finger_scores": percentiles(hard),
        "eer": None if np.isnan(eer) else round(eer, 4), "threshold_eer": None if np.isnan(eer_t) else round(eer_t, 2),
        "thresholds_at_far": {k: None if v is None else round(v, 2) for k, v in thresholds.items()},
        "frr_at_far": {k: (None if (r := frr_at(genuine, thresholds[k])) is None else round(r, 4)) for k in thresholds},
        "table": threshold_table(genuine, impostor),
        "genuine": genuine, "impostor": impostor,
    }


def evaluate(entries: list[Entry], engine, threads: int = 1) -> dict:
    trials = score_trials(entries, engine, threads)
    gallery = [e for e in entries if e.print_type == "rolled"]
    result = {"modes": {m: analyse_mode(trials[m]) for m in MODES if trials[m]["probes"]},
              "gallery_prints": len(gallery), "gallery_fingers": len({e.finger_id for e in gallery}),
              "fingers": len({e.finger_id for e in entries})}
    result["real_latent_probes"] = trials["latent"]["probes"]
    return result


def mode_counts(result: dict, mode: str) -> dict:
    m = result["modes"][mode]
    return {"fingers": result["gallery_fingers"], "genuine_trials": m["genuine_trials"], "impostor_trials": m["impostor_trials"],
            "real_latent_probes": result["real_latent_probes"] if mode == "latent" else 0, "probes": m["probes"]}


def calibratable_modes(result: dict) -> tuple[list[str], dict]:
    """(modes with enough data to set a threshold, {mode: why not}). latent_crop can never calibrate."""
    ok, why = [], {}
    for mode in ("rolled", "latent"):
        if mode not in result["modes"]:
            why[mode] = ["no probes in the manifest for this mode" + (" (needs real lifts, origin=lift)" if mode == "latent" else "")]
            continue
        short = fp.mode_shortfalls(mode, mode_counts(result, mode))
        (why.__setitem__(mode, short) if short else ok.append(mode))
    if "latent_crop" in result["modes"]:
        why["latent_crop"] = ["cropped or undeclared latents are stand-ins and never set a threshold"]
    return ok, why


def build_calibration(result: dict, engine_name: str, manifest_sha256: str, provenance: str, target_far: float) -> Optional[dict]:
    """The calibration document for the modes with enough data, or None. `operating` is the threshold at `target_far`."""
    ok, _ = calibratable_modes(result)
    modes = {}
    for mode in ok:
        m = result["modes"][mode]
        operating = threshold_at_far(m["impostor"], target_far)
        if operating is None:                      # too few impostor trials to support the target rate
            continue
        thresholds = {"operating": round(operating, 2), **m["thresholds_at_far"]}
        modes[mode] = {"thresholds": thresholds, "counts": mode_counts(result, mode), "target_far": target_far,
                       "frr_at_operating": None if (r := frr_at(m["genuine"], operating)) is None else round(r, 4), "eer": m["eer"]}
    if not modes:
        return None
    return {"engine": engine_name, "created_at": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
            "manifest_sha256": manifest_sha256, "modes": modes,
            "note": "Written by scripts/evaluate_fingerprint.py. Thresholds are for THIS engine and THIS image mix: re-run after any change. "
                    "Scores are SourceAFIS scores: not a percentage, and not comparable with other AFIS products."}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else (f"{x * 100:.3g}%" if x else "0%")


def format_table(mode: str, m: dict) -> str:
    lines = [f"\n{mode.upper()}  probes={m['probes']}  genuine trials={m['genuine_trials']}  impostor trials={m['impostor_trials']}"
             f" (of which same person, other finger: {m['impostor_same_person_other_finger']})  EER={_pct(m['eer'])} at score {m['threshold_eer']}",
             f"  genuine scores : {m['genuine_scores']}", f"  impostor scores: {m['impostor_scores']}",
             "  threshold |  false-accept rate | could be as high as (95%) | false-reject rate",
             "  ----------+--------------------+---------------------------+-------------------"]
    for r in m["table"]:
        lines.append(f"  {r['threshold']:>9d} | {_pct(r['far']):>7s} ({r['false_accepts']:>5d}/{r['impostor_trials']:<6d}) | {_pct(r['far_upper_95']):>25s} | "
                     f"{_pct(r['frr']):>7s} ({r['false_rejects']}/{r['genuine_trials']})")
    return "\n".join(lines)


def public_result(result: dict) -> dict:
    return {**{k: v for k, v in result.items() if k != "modes"},
            "modes": {mode: {k: v for k, v in m.items() if k not in ("genuine", "impostor")} for mode, m in result["modes"].items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--image-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path(fp.CALIBRATION_FILE))
    ap.add_argument("--report", type=Path, default=None, help="also write the full report (with the threshold tables) as JSON")
    ap.add_argument("--target-far", type=float, default=0.001, help="false-accept rate the operating threshold is set for (default 0.1%%)")
    ap.add_argument("--provenance", default="", help="where the data came from (dataset name, licence / data-sharing agreement reference): required to write a calibration")
    ap.add_argument("--threads", type=int, default=fp.MATCH_THREADS)
    ap.add_argument("--dry-run", action="store_true", help="report only; never write a calibration file")
    args = ap.parse_args()

    engine = fp.load_engine(os.getenv("FINGERPRINT_ENGINE", "fingerprint:JvmSourceAFISEngine"))
    reason = getattr(engine, "not_ready_reason", lambda: None)() if not isinstance(engine, fp.UnavailableEngine) else fp.UNAVAILABLE_MESSAGE
    if reason:
        print("Fingerprint engine not available:", reason)
        return 2
    rows, manifest_sha = load_manifest(args.manifest)
    entries, refused = prepare(rows, engine, args.image_root)
    print(f"{len(entries)} of {len(rows)} images usable ({len(refused)} refused by the service's own quality gate and minutiae floors)")
    for r in refused[:10]:
        print(f"  refused {r['path']} ({r['print_type']}): {r['reason']}")
    gallery = sum(1 for e in entries if e.print_type == "rolled")
    if gallery < 2:
        print("Need at least two usable rolled prints to evaluate anything.")
        return 2
    print(f"scoring {len(entries)} probes against {gallery} gallery prints (~{len(entries) * gallery * 0.0005 / max(args.threads, 1):.0f} s)")
    result = evaluate(entries, engine, args.threads)
    for mode, m in result["modes"].items():
        print(format_table(mode, m))
    if args.report:
        args.report.write_text(json.dumps({**public_result(result), "refused": refused, "manifest_sha256": manifest_sha, "provenance": args.provenance}, indent=2))
    ok, why = calibratable_modes(result)
    for mode, problems in why.items():
        print(f"\n{mode}: NOT calibrated, so the service keeps the default threshold for it:")
        for p in problems:
            print("  -", p)
    if not ok:
        print("\nINSUFFICIENT DATA: no calibration written.")
        return 2
    if args.dry_run:
        print(f"\n--dry-run: {', '.join(ok)} would be calibrated, nothing written.")
        return 0
    if not args.provenance.strip():
        print("\nRefusing to write a calibration without --provenance (where did this data come from? dataset, licence or data-sharing agreement).")
        return 2
    calibration = build_calibration(result, engine.name, manifest_sha, args.provenance.strip(), args.target_far)
    if calibration is None:
        print(f"\nToo few impostor trials to support a false-accept rate of {args.target_far:g}: no calibration written.")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(calibration, indent=2))
    print(f"\nCalibration written to {args.out} for: {', '.join(calibration['modes'])}. "
          "Have the forensic reviewer sign off on the tables above before relying on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
