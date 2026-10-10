"""Canonical root configuration. Shared with Role 3 at integration."""
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

REPO_ROOT = Path(__file__).resolve().parent


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    openai_api_key: SecretStr
    openai_model: str = Field(min_length=1)
    raw_data_dir: Path
    data_dir: Path
    output_dir: Path
    cache_dir: Path
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    api_host: str
    api_port: int = Field(ge=1, le=65535)
    api_base_url: str
    streamlit_server_address: str
    streamlit_server_port: int = Field(ge=1, le=65535)

    @field_validator("api_base_url")
    @classmethod
    def http_origin(cls, value: str) -> str:
        from urllib.parse import urlsplit
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

    def path(name: str, default: str) -> Path:
        value = Path(os.environ.get(name, default)).expanduser()
        return (REPO_ROOT / value).resolve() if not value.is_absolute() else value.resolve()

    port = os.environ.get("API_PORT", "8000")
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
        raw_data_dir=path("BMW_RAW_DATA_DIR", "./BMW-Data"),
        data_dir=path("BMW_DATA_DIR", "./var/processed"),
        output_dir=path("BMW_OUTPUT_DIR", "./var/exports"),
        cache_dir=path("BMW_CACHE_DIR", "./var/cache"),
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "bmwpassword"),
        api_host=os.environ.get("API_HOST", "0.0.0.0"), api_port=port,
        api_base_url=os.environ.get("API_BASE_URL", f"http://localhost:{port}"),
        streamlit_server_address=os.environ.get("STREAMLIT_SERVER_ADDRESS", "0.0.0.0"),
        streamlit_server_port=os.environ.get("STREAMLIT_SERVER_PORT", "8501"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
