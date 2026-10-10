#!/usr/bin/env bash
# Run from any directory; .env is loaded by config.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON_BIN="${PYTHON_BIN:-python}"
"$PYTHON_BIN" - <<'PY'
from config import DATA_DIR, select_llm_provider
try:
    provider = select_llm_provider(required=True)
    if not DATA_DIR.is_dir():
        raise ValueError(f"Processed data directory does not exist: {DATA_DIR}. Convert raw inputs first.")
except ValueError as exc:
    raise SystemExit(str(exc))
print(f"Provider: {provider}; processed inputs: {DATA_DIR}")
PY
if [[ "${1:-}" == "--check-config" ]]; then
  exit 0
fi
steps=(00_preprocess 01_load_neo4j 02_build_kg 03_vfc_analysis 04_extract_signals 05_correlate_studies 06_generate_requirements)
for step in "${steps[@]}"; do
  if [[ ! -f "pipeline/$step.py" ]]; then
    echo "Missing pipeline/$step.py; integrate the responsible role's implementation before running." >&2
    exit 1
  fi
done
for step in "${steps[@]}"; do
  echo "Running $step"
  "$PYTHON_BIN" "pipeline/$step.py"
done
echo "Pipeline complete. Start API: python -m uvicorn services.api:app --port 8000"
echo "Export after API startup: python pipeline/09_export_report.py --output var/exports/requirements.json"
