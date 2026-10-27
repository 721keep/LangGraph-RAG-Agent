import logging
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

LOGS_DIR = BASE_DIR / "logs"
if not LOGS_DIR.exists():
    LOGS_DIR.mkdir()

logging.basicConfig(
    level=logging.INFO,  # For displaying the default model calling logs
)

logger.add(
    sink=LOGS_DIR / "api_{time:YYYYMMDD}.log",
    level="INFO",
    rotation="00:00",
    retention="7 days",
    compression="zip",
)


class Settings(BaseSettings):
    chat_api_key: SecretStr = Field(
    alias="CHAT_API_KEY"
    )

    # Legacy fallback is restricted to the original DashScope HTTPS endpoint.
    dashscope_api_key: SecretStr | None = Field(
        default=None, alias="DASHSCOPE_API_KEY"
    )
    tavily_api_key: str
    model_provider: str
    model_names: list[str]
    model_base_url: str | None = None
    app_timezone: str = "UTC"
    embeddings_api_key: SecretStr = Field(default=SecretStr(""), alias="EMBEDDINGS_API_KEY")
    embeddings_provider: str = "openai"
    embeddings_model_name: str
    embeddings_base_url: str | None = None
    rerank_api_key: SecretStr = Field(default=SecretStr(""), alias="RERANK_API_KEY")
    rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    rerank_model_name: str = "qwen3-rerank"
    # Preserve the existing protocol; a future service may require /rerank.
    rerank_path: str = Field(default="/reranks", pattern=r"^/[^/?#][^?#]*$")
    token_bearer_url: str
    jwt_secret: str
    jwt_algorithm: str
    access_token_expiry_mins: int
    refresh_token_expiry_days: int
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str
    pgvector_collection_name: str

    @model_validator(mode="after")
    def resolve_model_api_keys(self):
        """Never reuse Chat credentials or send a legacy key to a new host."""
        legacy_key = self.dashscope_api_key
        for field, base_url, allow_legacy in (
            ("embeddings_api_key", self.embeddings_base_url, self.embeddings_provider == "openai"),
            ("rerank_api_key", self.rerank_base_url, True),
        ):
            if getattr(self, field).get_secret_value().strip():
                continue
            endpoint = urlsplit(base_url or "")
            if (
                allow_legacy
                and endpoint.scheme == "https"
                and endpoint.hostname == "dashscope.aliyuncs.com"
                and endpoint.port in (None, 443)
                and legacy_key is not None
                and legacy_key.get_secret_value().strip()
            ):
                setattr(self, field, legacy_key)
            else:
                raise ValueError(f"{field.upper()} is required for this model endpoint.")
        return self

    @property
    def database_uri(self) -> str:
        """Generate PostgreSQL connection string for sqlalchemy."""
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"

    @property
    def checkpointer_uri(self) -> str:
        """Generate PostgreSQL connection string for checkpointer."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}?sslmode=disable"

    @property
    def pgvector_connection(self) -> str:
        """Generate PostgreSQL connection string for PGVector."""
        return f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", extra="allow", hide_input_in_errors=True,
    )


settings = Settings()  # type: ignore
