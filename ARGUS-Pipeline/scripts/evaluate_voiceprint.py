"""Measure the speaker engine on real recordings and, only if the data is sufficient, write the calibration file.

    docker compose exec api python scripts/evaluate_voiceprint.py --manifest /data/eval/manifest.csv --audio-root /data/eval
    docker compose exec api python scripts/evaluate_voiceprint.py --manifest ... --dry-run     # report only, never writes

manifest.csv columns (header required):
    path        audio file, relative to --audio-root
    speaker_id  ground truth: recordings with the same id are the same person
    language    e.g. hi, en, bn, ta (free text, lower-cased)
    channel     `phone` (telephone-channel: 8 kHz narrowband, real codecs) or `direct` (wideband)
    session     optional: recordings of one speaker from the same session are never paired as "genuine" (same-session
                pairs flatter the result)
    gender      optional: adds a gender breakdown

What is written. Every pair of recordings is scored (cosine similarity of ECAPA embeddings). Pairs of the same speaker from
different sessions are genuine trials, pairs of different speakers are impostor trials. The report gives the EER, the
false-reject rate at 1% / 0.1% / 0.01% false-accept, and the same broken down by language, channel pairing (direct->phone is the
hard case), duration and gender, with speaker-level bootstrap confidence intervals: an overall figure must not hide a bad
subgroup. A threshold is reported as null when there are too few impostor trials to support it (about 10/p trials for a
false-accept rate p).

The calibration file the service reads is written ONLY if the data meets voiceprint.CALIBRATION_MIN (>= 3 languages, both
channels, >= 10 speakers in every language x channel cell, >= 50 speakers, >= 10,000 impostor trials). Otherwise the script
prints what is missing, exits 2 and writes nothing, and the endpoints stay in ranking-only mode. A calibration made on
synthetic voices or a handful of colleagues is worse than none: this script cannot tell, so do not feed it those.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import voiceprint

FAR_POINTS = {"far_1pct": 0.01, "far_0_1pct": 0.001, "far_0_01pct": 0.0001}


# ---------------------------------------------------------------------------
# Pure metrics (unit-tested)
# ---------------------------------------------------------------------------

def pair_scores(embeddings: np.ndarray, meta: list[dict]) -> dict:
    """All unordered pairs with their cosine score and whether they are genuine, impostor or excluded."""
    n = len(meta)
    sim = embeddings @ embeddings.T
    i, j = np.triu_indices(n, 1)
    speaker = np.array([m["speaker_id"] for m in meta])
    session = np.array([m.get("session") or f"file-{k}" for k, m in enumerate(meta)])
    same = speaker[i] == speaker[j]
    genuine = same & (session[i] != session[j])
    impostor = ~same
    return {"i": i, "j": j, "score": sim[i, j], "genuine": genuine, "impostor": impostor, "sim": sim}


def eer_of(genuine: np.ndarray, impostor: np.ndarray) -> tuple[float, float]:
    """(EER, threshold at the EER). Returns (nan, nan) if either side is empty. O(n log n): FAR/FRR from sorted scores."""
    if len(genuine) == 0 or len(impostor) == 0:
        return float("nan"), float("nan")
    grid = np.unique(np.concatenate([genuine, impostor]))
    imp, gen = np.sort(impostor), np.sort(genuine)
    far = 1.0 - np.searchsorted(imp, grid, side="left") / len(imp)        # share of impostors scoring >= t
    frr = np.searchsorted(gen, grid, side="left") / len(gen)              # share of genuine scoring < t
    k = int(np.argmin(np.abs(far - frr)))
    return float((far[k] + frr[k]) / 2), float(grid[k])


def threshold_at_far(impostor: np.ndarray, far: float) -> float | None:
    """Lowest threshold whose false-accept rate is at most `far`; None if there are too few impostor trials to support it."""
    if len(impostor) < 10.0 / far:
        return None
    return float(np.nextafter(np.quantile(impostor, 1.0 - far, method="higher"), np.inf))


def frr_at(genuine: np.ndarray, threshold: float | None) -> float | None:
    return None if threshold is None or len(genuine) == 0 else float((genuine < threshold).mean())


def summarise(genuine: np.ndarray, impostor: np.ndarray) -> dict:
    eer, eer_t = eer_of(genuine, impostor)
    thresholds = {name: threshold_at_far(impostor, p) for name, p in FAR_POINTS.items()}
    return {"genuine_trials": int(len(genuine)), "impostor_trials": int(len(impostor)), "eer": None if np.isnan(eer) else round(eer, 4),
            "threshold_eer": None if np.isnan(eer_t) else round(eer_t, 4),
            "thresholds": {k: None if v is None else round(v, 4) for k, v in thresholds.items()},
            "frr_at_far": {k: (None if (r := frr_at(genuine, thresholds[k])) is None else round(r, 4)) for k in thresholds}}


def bootstrap_ci(embeddings: np.ndarray, meta: list[dict], reps: int, seed: int = 0) -> dict:
    """Speaker-level bootstrap (resample speakers, not trials: trials sharing a speaker are correlated) 95% intervals."""
    if reps <= 0:
        return {}
    rng = np.random.default_rng(seed)
    speakers = sorted({m["speaker_id"] for m in meta})
    by_speaker = {s: [k for k, m in enumerate(meta) if m["speaker_id"] == s] for s in speakers}
    sim = embeddings @ embeddings.T
    sess_names = [m.get("session") or f"file-{k}" for k, m in enumerate(meta)]
    sess = np.array([{n: q for q, n in enumerate(dict.fromkeys(sess_names))}[n] for n in sess_names])
    eers, far01 = [], []
    for _ in range(reps):
        picks = rng.choice(len(speakers), size=len(speakers), replace=True)
        idx, owner = [], []
        for slot, p in enumerate(picks):                     # a speaker drawn twice is two distinct "people" (slot), as in the standard bootstrap
            for k in by_speaker[speakers[p]]:
                idx.append(k)
                owner.append(slot)
        idx, owner = np.array(idx), np.array(owner)
        a, b = np.triu_indices(len(idx), 1)
        s = sim[idx[a], idx[b]]
        same = owner[a] == owner[b]
        different_session = sess[idx[a]] != sess[idx[b]]
        g, im = s[same & different_session], s[~same]
        e, _ = eer_of(g, im)
        if not np.isnan(e):
            eers.append(e)
        t = threshold_at_far(im, 0.001)
        if t is not None:
            far01.append(t)
    def ci(values):
        return None if len(values) < max(10, reps // 4) else [round(float(np.percentile(values, 2.5)), 4), round(float(np.percentile(values, 97.5)), 4)]
    return {"eer_95ci": ci(eers), "far_0_1pct_threshold_95ci": ci(far01), "replicates": reps}


def analyse(embeddings: np.ndarray, meta: list[dict], bootstrap: int = 0, seed: int = 0) -> dict:
    """Everything the report and the calibration need, from embeddings + ground truth."""
    ps = pair_scores(embeddings, meta)
    g, im = ps["score"][ps["genuine"]], ps["score"][ps["impostor"]]
    overall = summarise(g, im)
    i, j = ps["i"], ps["j"]
    lang = np.array([m["language"] for m in meta])
    chan = np.array([m["channel"] for m in meta])
    dur = np.array([m.get("net_speech_seconds", np.inf) for m in meta], dtype=float)
    gender = np.array([m.get("gender") or "" for m in meta])

    def slice_summary(mask):
        return summarise(ps["score"][ps["genuine"] & mask], ps["score"][ps["impostor"] & mask])

    breakdown = {"language": {}, "channel_pairing": {}, "duration_band": {}, "gender": {}}
    for L in sorted(set(lang)):
        breakdown["language"][L] = slice_summary((lang[i] == L) & (lang[j] == L))
    for a in sorted(set(chan)):
        for b in sorted(set(chan)):
            if a <= b:
                mask = ((chan[i] == a) & (chan[j] == b)) | ((chan[i] == b) & (chan[j] == a))
                breakdown["channel_pairing"][f"{a}+{b}"] = slice_summary(mask)
    shortest = np.minimum(dur[i], dur[j])
    for name, lo, hi in (("<5s", 0, 5), ("5-10s", 5, 10), (">=10s", 10, np.inf)):
        breakdown["duration_band"][name] = slice_summary((shortest >= lo) & (shortest < hi))
    if gender.any():
        for gname in sorted(set(gender) - {""}):
            breakdown["gender"][gname] = slice_summary((gender[i] == gname) & (gender[j] == gname))
    speakers = sorted({m["speaker_id"] for m in meta})
    cells: dict[str, set] = {}
    for m in meta:
        cells.setdefault(f"{m['language']}|{m['channel']}", set()).add(m["speaker_id"])
    counts = {"recordings": len(meta), "speakers": len(speakers), "languages": sorted(set(lang)), "channels": sorted(set(chan)),
              "speakers_per_cell": {k: len(v) for k, v in sorted(cells.items())}, "impostor_trials": overall["impostor_trials"],
              "genuine_trials": overall["genuine_trials"]}
    return {"overall": overall, "breakdown": breakdown, "counts": counts, "bootstrap": bootstrap_ci(embeddings, meta, bootstrap, seed)}


def worst_subgroups(result: dict, factor: float = 2.0) -> list[str]:
    """Subgroups whose EER is at least `factor` times the overall EER (a good average can hide these)."""
    overall = result["overall"]["eer"]
    out = []
    if overall is None:
        return out
    for family, groups in result["breakdown"].items():
        for name, s in groups.items():
            if s["eer"] is not None and s["genuine_trials"] >= 30 and s["eer"] >= max(overall * factor, overall + 0.02):
                out.append(f"{family}={name}: EER {s['eer']:.1%} vs overall {overall:.1%}")
    return out


def build_calibration(result: dict, model_id: str, manifest_sha256: str) -> dict:
    o = result["overall"]
    return {"model_id": model_id, "created_at": datetime.now(timezone.utc).isoformat(), "manifest_sha256": manifest_sha256,
            "thresholds": {"eer": o["threshold_eer"], **o["thresholds"]}, "eer": o["eer"], "counts": result["counts"],
            "frr_at_far": o["frr_at_far"], "bootstrap": result["bootstrap"],
            "note": "Written by scripts/evaluate_voiceprint.py. Thresholds are for THIS model and THIS audio mix: re-run after any change."}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    need = {"path", "speaker_id", "language", "channel"}
    if not rows or not need <= set(rows[0]):
        raise SystemExit(f"manifest needs columns {sorted(need)} (optional: session, gender)")
    for r in rows:
        r["language"], r["channel"] = r["language"].strip().lower(), r["channel"].strip().lower()
        if r["channel"] not in voiceprint.REQUIRED_CHANNELS:
            raise SystemExit(f"channel must be one of {voiceprint.REQUIRED_CHANNELS}, got {r['channel']!r} ({r['path']})")
    return rows, hashlib.sha256(raw).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--audio-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path(os.getenv("VOICEPRINT_CALIBRATION_FILE", "uploads/voiceprint-calibration.json")))
    ap.add_argument("--report", type=Path, default=None, help="also write the full report as JSON")
    ap.add_argument("--bootstrap", type=int, default=200, help="speaker-level bootstrap replicates (0 = skip)")
    ap.add_argument("--dry-run", action="store_true", help="report only; never write a calibration file")
    args = ap.parse_args()

    engine = voiceprint.load_engine(os.getenv("VOICEPRINT_ENGINE", "voiceprint:SpeechBrainEngine"))
    rows, manifest_sha = load_manifest(args.manifest)
    metas, vectors, skipped = [], [], []
    for r in rows:
        try:
            wave = voiceprint.decode_audio((args.audio_root / r["path"]).read_bytes(), Path(r["path"]).suffix)
            q = voiceprint.channel_quality(wave)
            if not q["passed"]:
                skipped.append((r["path"], q["reason"]))
                continue
            vectors.append(engine.embed(wave))
            metas.append({**r, "net_speech_seconds": q["net_speech_seconds"]})
        except (OSError, voiceprint.UnreadableAudioError) as exc:
            skipped.append((r["path"], str(exc)))
    print(f"embedded {len(metas)} of {len(rows)} recordings ({len(skipped)} skipped)")
    for path, why in skipped[:10]:
        print(f"  skipped {path}: {why}")
    if len(metas) < 4:
        print("Too few usable recordings to evaluate anything.")
        return 2
    result = analyse(np.vstack(vectors), metas, args.bootstrap)
    o = result["overall"]
    print(f"\nOVERALL  speakers={result['counts']['speakers']}  genuine={o['genuine_trials']}  impostor={o['impostor_trials']}  EER={o['eer']}")
    print(f"  thresholds: {o['thresholds']}   FRR at those FAR points: {o['frr_at_far']}")
    if result["bootstrap"]:
        print(f"  speaker-level bootstrap: {result['bootstrap']}")
    for family, groups in result["breakdown"].items():
        for name, s in groups.items():
            print(f"  {family:16s} {name:14s} genuine={s['genuine_trials']:6d} impostor={s['impostor_trials']:7d} EER={s['eer']}")
    for line in worst_subgroups(result):
        print("  WARNING subgroup much worse than average:", line)
    if args.report:
        args.report.write_text(json.dumps({"counts": result["counts"], "overall": o, "breakdown": result["breakdown"], "bootstrap": result["bootstrap"]}, indent=2))
    shortfalls = voiceprint.calibration_shortfalls(result["counts"])
    if shortfalls:
        print("\nINSUFFICIENT DATA: no calibration written. The voice endpoints must stay off (or in ranking-only mode).")
        for s in shortfalls:
            print("  -", s)
        return 2
    if args.dry_run:
        print("\n--dry-run: data is sufficient, nothing written.")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build_calibration(result, engine.model_id, manifest_sha), indent=2))
    print(f"\nCalibration written to {args.out}. Have the forensic reviewer sign off on the subgroup results before enabling the endpoints.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
