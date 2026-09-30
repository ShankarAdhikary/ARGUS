"""Give faces enrolled before jurisdictions were recorded a jurisdiction.

Face searches are now scoped: a jurisdiction-scoped officer only matches faces enrolled in their own jurisdiction, and a
face with no jurisdiction counts as Unassigned, which only unscoped roles (admin, supervisor, analyst) can see. Without a
backfill every face enrolled earlier would disappear for scoped officers. This resolves each legacy face from its FIR
(the FIR node's jurisdiction in Neo4j); faces with no FIR ("N/A", e.g. bulk uploads) or an unknown FIR become Unassigned.

    docker compose exec api python scripts/backfill_face_jurisdiction.py           # dry run: report only
    docker compose exec api python scripts/backfill_face_jurisdiction.py --apply   # write the registry (vectors untouched)

Idempotent; faces that already have a jurisdiction are left alone. Bulk-uploaded faces stay Unassigned until an unscoped
officer decides which jurisdiction owns them.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase

from config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from face_index import FaceIndex


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    ap.add_argument("--dim", type=int, default=int(os.getenv("FACE_EMBEDDING_DIM", "512")))
    ap.add_argument("--dir", default=os.getenv("FACE_INDEX_DIR", "uploads/face_index"))
    args = ap.parse_args()

    index = FaceIndex(args.dim, args.dir)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    cache: dict[str, str | None] = {}

    def resolve(meta: dict) -> str | None:
        fir = meta.get("fir_id")
        if not fir or fir == "N/A":
            return None
        if fir not in cache:
            with driver.session() as session:
                row = session.run("MATCH (f:FIR {id: $id}) RETURN f.jurisdiction AS j", id=fir).single()
            cache[fir] = row["j"] if row and row["j"] else None
        return cache[fir]

    report = index.backfill_jurisdiction(resolve, apply=args.apply)
    print(("APPLIED" if args.apply else "DRY RUN (nothing written)"), "-", index.ntotal, "faces in the index")
    print(f"  already had a jurisdiction: {report['already']}")
    print(f"  resolved from their FIR:    {report['resolved']}")
    print(f"  left Unassigned:            {report['unassigned']}")
    for jurisdiction, n in sorted(report["by_jurisdiction"].items(), key=lambda kv: -kv[1]):
        print(f"    {jurisdiction}: {n}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
