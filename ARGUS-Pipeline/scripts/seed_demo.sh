#!/usr/bin/env bash
# seed_demo.sh — generate + load the trafficking-network demo dataset.
#
# Usage (from the ARGUS-Pipeline directory):
#   bash scripts/seed_demo.sh
#
# Or from the repo root:
#   bash ARGUS-Pipeline/scripts/seed_demo.sh
#
# Requires the Docker Compose stack to be running:
#   docker compose up -d
# Wait ~30 s for Elasticsearch to be healthy before running this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PIPELINE_DIR}"

echo "=== ARGUS Demo Seed ==="
echo "[1/2] Generating synthetic trafficking-network dataset..."
python generate_demo_dataset.py --firs 120

echo "[2/2] Loading dataset directly into Neo4j + Elasticsearch..."
python load_demo.py

echo ""
echo "=== Done ==="
echo "Demo accounts:"
echo "  admin@demo.com  / password123  (admin)"
echo "  INV001          / demo123       (investigator)"
echo "  ANL001          / demo123       (analyst)"
echo "  SUP001          / demo123       (supervisor)"
echo ""
echo "Start searching: 'Vikram Singh' (kingpin) or '9999988888' (his primary number)"
