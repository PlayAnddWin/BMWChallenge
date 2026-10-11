"""Canonical root environment reader for the evidence and application layers."""

from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


REPO_ROOT = Path(__file__).resolve().parent


def resolve_path(value: str | Path) -> Path:
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
    neo4j_database: str = Field(default="neo4j", min_length=1)
    api_host: str
    api_port: int = Field(ge=1, le=65535)
    api_base_url: str
    api_prefix: str = "/api"
    backend_adapter_factory: str = ""
    streamlit_server_address: str
    streamlit_server_port: int = Field(ge=1, le=65535)

    @field_validator("neo4j_uri")
    @classmethod
    def graph_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"} or not parsed.hostname:
            raise ValueError("NEO4J_URI must be a Bolt or Neo4j URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("NEO4J_URI must not contain credentials, query, fragment, or a database path")
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

    @field_validator("api_prefix")
    @classmethod
    def route_prefix(cls, value: str) -> str:
        if value and (not value.startswith("/") or value.endswith("/") or "?" in value or "#" in value):
            raise ValueError("API_PREFIX must be empty or an absolute route prefix without a trailing slash")
        return value


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
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"), api_port=port,
        api_base_url=os.environ.get("API_BASE_URL", f"http://localhost:{port}"),
        api_prefix=os.environ.get("API_PREFIX", "/api"),
        backend_adapter_factory=os.environ.get("BACKEND_ADAPTER_FACTORY", ""),
        streamlit_server_address=os.environ.get("STREAMLIT_SERVER_ADDRESS", "0.0.0.0"),
        streamlit_server_port=os.environ.get("STREAMLIT_SERVER_PORT", "8501"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def select_llm_provider(environ=None, *, required=False):
    """Compatibility boundary: OpenAI is the only supported live provider."""
    env = os.environ if environ is None else environ
    requested = env.get("LLM_PROVIDER", "").strip().lower()
    if requested and requested != "openai":
        raise ValueError("OpenAI is the only supported provider; remove legacy LLM_PROVIDER")
    key = env.get("OPENAI_API_KEY", "")
    if not key.strip():
        if required:
            raise ValueError("Set OPENAI_API_KEY for live generation")
        return None
    return "openai"


# Existing CLI/API imports remain compatible; all values derive from this one reader.
_settings = get_settings()
DATA_DIR, RAW_DATA_DIR = _settings.data_dir, _settings.raw_data_dir
OUTPUT_DIR, CACHE_DIR = _settings.output_dir, _settings.cache_dir
NEO4J_URI, NEO4J_USER = _settings.neo4j_uri, _settings.neo4j_user
NEO4J_PASSWORD = _settings.neo4j_password.get_secret_value()
NEO4J_DATABASE = _settings.neo4j_database
OPENAI_API_KEY, OPENAI_MODEL = _settings.openai_api_key.get_secret_value(), _settings.openai_model
API_HOST, API_PORT, API_BASE_URL = _settings.api_host, _settings.api_port, _settings.api_base_url
API_PREFIX, BACKEND_ADAPTER_FACTORY = _settings.api_prefix, _settings.backend_adapter_factory


def market_reach_norm(model: str, market: str) -> float:
    """Explicit legacy-v0 reproduction only; never used for authoritative v1 reach."""
    volumes = {"F70": {"EU": 85000, "RoW": 28000, "CN": 0, "US": 0},
               "G60": {"EU": 115000, "RoW": 28000, "CN": 75000, "US": 78000},
               "G70": {"EU": 13000, "RoW": 7000, "CN": 10000, "US": 12000}}
    return round(volumes.get(model, {}).get(market, 0) / 115000 * 10, 2)
