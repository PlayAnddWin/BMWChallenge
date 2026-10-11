#!/usr/bin/env bash
# Run from any directory; .env is loaded by config.py.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON_BIN=.venv/bin/python
  else
    PYTHON_BIN=python3
  fi
fi
exec "$PYTHON_BIN" - "$@" <<'PY'
import argparse
import os
import subprocess
import sys

parser = argparse.ArgumentParser(
    description="Run the full data/generation pipeline, then serve the authoritative backend.",
)
parser.add_argument("--data-only", action="store_true",
                    help="Run Role 1 conversion/import/summaries and start the API without generation.")
parser.add_argument("--check-config", action="store_true",
                    help="Check canonical configuration and stage availability offline; do not run stages or servers.")
args = parser.parse_args()

try:
    from config import REPO_ROOT, get_settings, select_llm_provider
    settings = get_settings()
    if not settings.raw_data_dir.is_dir():
        raise ValueError(f"Raw data directory does not exist: {settings.raw_data_dir}")
    for label, path in (("Processed", settings.data_dir), ("Output", settings.output_dir),
                        ("Cache", settings.cache_dir)):
        if path == settings.raw_data_dir or settings.raw_data_dir in path.parents:
            raise ValueError(f"{label} directory must be outside the raw data directory")
        if path.exists() and not path.is_dir():
            raise ValueError(f"{label} destination is not a directory: {path}")
    if not settings.neo4j_user.strip() or not settings.neo4j_password.get_secret_value().strip():
        raise ValueError("Set NEO4J_USER and NEO4J_PASSWORD before backend/data startup")
    if settings.backend_adapter_factory:
        module, separator, attribute = settings.backend_adapter_factory.partition(":")
        if not separator or not module or not attribute or ":" in attribute:
            raise ValueError("BACKEND_ADAPTER_FACTORY must be module:function")
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Invalid runtime configuration: {exc}")

data_stages = ("00_preprocess", "01_load_neo4j", "02_build_kg", "03_vfc_analysis")
generation_stages = ("04_extract_signals", "05_correlate_studies", "06_generate_requirements")
missing_data = [stage for stage in data_stages if not (REPO_ROOT / "pipeline" / f"{stage}.py").is_file()]
missing_generation = [stage for stage in generation_stages if not (REPO_ROOT / "pipeline" / f"{stage}.py").is_file()]
if missing_data:
    raise SystemExit("Missing Role 1 stages: " + ", ".join(missing_data))

print(f"Mode: {'data-only' if args.data_only else 'full'}; provider contract: OpenAI only", flush=True)
print(f"Raw inputs: {settings.raw_data_dir}; processed destination: {settings.data_dir}", flush=True)
print(f"Backend listen: {settings.api_host}:{settings.api_port}; API origin: {settings.api_base_url}", flush=True)
if args.check_config:
    if missing_generation:
        print("Role 2 generation unavailable: " + ", ".join(missing_generation)
              + "; default full mode requires these implementations.")
    print("Configuration valid (offline; no conversion, database connection, or model call).")
    raise SystemExit(0)

stages = data_stages
if not args.data_only:
    if missing_generation:
        raise SystemExit("Full pipeline unavailable: missing Role 2 stages " + ", ".join(missing_generation)
                         + ". Integrate Role 2, or use --data-only for Role 1 and backend startup.")
    try:
        select_llm_provider(required=True)
    except ValueError as exc:
        raise SystemExit(str(exc))
    stages += generation_stages

# Native Streamlit settings must be present before any future UI CLI starts.
os.environ["STREAMLIT_SERVER_ADDRESS"] = settings.streamlit_server_address
os.environ["STREAMLIT_SERVER_PORT"] = str(settings.streamlit_server_port)

try:
    from neo4j import GraphDatabase
    import uvicorn
except ImportError as exc:
    raise SystemExit(f"Backend dependencies unavailable: {exc}; install requirements.txt with requirements.lock.txt")

# Check connectivity and the selected database before converting or writing inputs.
driver = None
try:
    driver = GraphDatabase.driver(settings.neo4j_uri,
                                  auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()))
    driver.verify_connectivity()
    with driver.session(database=settings.neo4j_database) as session:
        session.run("RETURN 1 AS ready").consume()
except Exception as exc:
    raise SystemExit(f"Backend database preflight failed ({type(exc).__name__}); check NEO4J_URI, credentials and NEO4J_DATABASE")
finally:
    if driver is not None:
        driver.close()
print(f"Backend database preflight passed: {settings.neo4j_database}", flush=True)

for stage in stages:
    print(f"Running {stage}", flush=True)
    try:
        subprocess.run([sys.executable, str(REPO_ROOT / "pipeline" / f"{stage}.py")],
                       cwd=REPO_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)

print("Pipeline complete. Starting services.api:app in the foreground (one worker).", flush=True)
uvicorn.run("services.api:app", host=settings.api_host, port=settings.api_port, workers=1)
PY
