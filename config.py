"""Canonical root environment reader shared by the data and application layers."""

from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


REPO_ROOT = Path(__file__).resolve().parent


def resolve_path(value: str | Path) -> Path:
    """Resolve configured and CLI paths against the repository, not the caller."""
    path = Path(value).expanduser()
    return (REPO_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    openai_api_key: SecretStr
    openai_model: str = Field(min_length=1)
    raw_data_dir: Path
    data_dir: Path
    output_dir: Path
    cache_dir: Path
    neo4j_uri: str
    neo4j_user: str = Field(min_length=1)
    neo4j_password: SecretStr
    api_host: str
    api_port: int = Field(ge=1, le=65535)
    api_base_url: str
    streamlit_server_address: str
    streamlit_server_port: int = Field(ge=1, le=65535)

    @field_validator("neo4j_uri")
    @classmethod
    def graph_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"} or not parsed.hostname:
            raise ValueError("NEO4J_URI must be a Bolt or Neo4j URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("NEO4J_URI must not contain credentials, query, or fragment")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("NEO4J_URI port out of range")
        return value

    @field_validator("api_base_url")
    @classmethod
    def http_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("API_BASE_URL must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API_BASE_URL must not contain credentials, query, or fragment")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("API_BASE_URL port out of range")
        return value.rstrip("/")


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)

    port = os.environ.get("API_PORT", "8000")
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        raw_data_dir=resolve_path(os.environ.get("BMW_RAW_DATA_DIR", "./BMW-Data")),
        data_dir=resolve_path(os.environ.get("BMW_DATA_DIR", "./var/processed")),
        output_dir=resolve_path(os.environ.get("BMW_OUTPUT_DIR", "./var/exports")),
        cache_dir=resolve_path(os.environ.get("BMW_CACHE_DIR", "./var/cache")),
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "bmwpassword"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"),
        api_port=port,
        api_base_url=os.environ.get("API_BASE_URL", f"http://localhost:{port}"),
        streamlit_server_address=os.environ.get("STREAMLIT_SERVER_ADDRESS", "0.0.0.0"),
        streamlit_server_port=os.environ.get("STREAMLIT_SERVER_PORT", "8501"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


# Compatibility exports for backend and legacy pipeline consumers.
load_dotenv(REPO_ROOT / ".env", override=False)

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = resolve_path(os.getenv("BMW_DATA_DIR", "./var/processed"))

F70_FEEDBACK   = DATA_DIR / "f70_feedbacks_clean.json"
G60_FEEDBACK   = DATA_DIR / "g60_feedbacks_clean.json"
G70_FEEDBACK   = DATA_DIR / "g70_feedbacks_clean.json"
STUDIES_FILE   = DATA_DIR / "customer_studies_clean.json"
SALES_FILE     = DATA_DIR / "sales_volumes_clean.json"
OPTIONS_FILE   = DATA_DIR / "option_lists_clean.json"

# ── Neo4j ──────────────────────────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "bmwpassword")

# ── LLM API — OpenAI (primary) or Anthropic (fallback) ────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL      = os.getenv("OPENAI_MODEL", "gpt-4o-mini")   # gpt-4o for higher quality

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Active LLM provider: "openai" or "anthropic"
def select_llm_provider(environ=None, *, required=False):
    """Choose an available provider; fail truthfully for missing explicit keys."""
    env = os.environ if environ is None else environ
    requested = env.get("LLM_PROVIDER", "").strip().lower()
    if requested and requested not in {"openai", "anthropic"}:
        raise ValueError("LLM_PROVIDER must be openai or anthropic")
    available = {name: bool(env.get(key, "").strip()) for name, key in
                 (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"))}
    if requested:
        if not available[requested]:
            raise ValueError(f"{requested.upper()}_API_KEY is required for LLM_PROVIDER={requested}")
        return requested
    provider = next((name for name in ("openai", "anthropic") if available[name]), None)
    if required and provider is None:
        raise ValueError("Set OPENAI_API_KEY or ANTHROPIC_API_KEY for live generation")
    return provider


LLM_PROVIDER = select_llm_provider()
RAW_DATA_DIR = resolve_path(os.getenv("BMW_RAW_DATA_DIR", "./BMW-Data"))
OUTPUT_DIR = resolve_path(os.getenv("BMW_OUTPUT_DIR", "./var/exports"))
API_BASE_URL = os.getenv("API_BASE_URL", f"http://localhost:{os.getenv('API_PORT', '8000')}")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── Models in scope ────────────────────────────────────────────────────────
MODELS_IN_SCOPE = ["F70", "G60", "G68", "G70"]

# Legacy-v0 compatibility only; authoritative v1 scoring must use sourced forecasts.
# Sales volumes (units/year) — from sales_volumes_clean.json
SALES_VOLUMES = {
    # 2025 volumes from sales_volumes.xlsx
    "F70": {"EU": 85_000, "RoW": 28_000, "CN": 0,       "US": 0},
    "G60": {"EU": 115_000, "RoW": 28_000, "CN": 75_000, "US": 78_000},
    "G70": {"EU": 13_000,  "RoW": 7_000,  "CN": 10_000, "US": 12_000},
}

# Normalisation base: G60 EU is the largest market (115k → 10.0)
MARKET_REACH_BASE = 115_000

def market_reach_norm(model: str, market: str) -> float:
    vol = SALES_VOLUMES.get(model, {}).get(market, 0)
    return round(vol / MARKET_REACH_BASE * 10, 2)

# Historical unverified assumptions, for explicit legacy reproduction only.
LEGACY_SOURCE_CHANNEL = {
    "A": "GENERAL_SURVEY",
    "B": "STANDARD_SURVEY",
    "C": "COMPLAINT_CHANNEL",   # 82 % pain, bias correction required
    "D": "OPEN_ENDED",
}
LEGACY_SOURCE_CHANNEL_BIAS = {
    "C": 0.70,   # discount sentiment by 30 % for complaint-only channel
}
SOURCE_CHANNEL = {key: key for key in "ABCD"}
SOURCE_CHANNEL_BIAS = {}

# Evidence tiers
EVIDENCE_TIERS = {
    "STRONG":   {"confidence": 1.0, "min_feedbacks": 30},
    "MODERATE": {"confidence": 0.7, "min_feedbacks": 10},
    "WEAK":     {"confidence": 0.4, "min_feedbacks":  0},
}

# Decision gate thresholds
GATE_GREEN_MIN_CONFIDENCE = 0.80
GATE_AMBER_MIN_CONFIDENCE = 0.50

# BMW Priority weights
BMW_PRIORITY_WEIGHTS = {
    "impact":        0.40,
    "market_reach":  0.30,
    "effort_inverse": 0.30,
}

# ── FastAPI ────────────────────────────────────────────────────────────────
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# New governance contract prefix; legacy root routes remain available.
API_PREFIX = os.getenv("API_PREFIX", "/api")
BACKEND_ADAPTER_FACTORY = os.getenv("BACKEND_ADAPTER_FACTORY", "")
