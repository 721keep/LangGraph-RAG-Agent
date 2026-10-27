from pathlib import Path

from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

LOGS_DIR = BASE_DIR / "logs"
if not LOGS_DIR.exists():
    LOGS_DIR.mkdir()

logger.add(
    sink=LOGS_DIR / "gui_{time:YYYYMMDD}.log",
    level="INFO",
    rotation="00:00",
    retention="7 days",
    compression="zip",
)


class Settings(BaseSettings):
    backend_base_url: str
    document_upload_connect_timeout: float = Field(default=10, gt=0, allow_inf_nan=False)
    document_upload_read_timeout: float = Field(default=300, gt=0, allow_inf_nan=False)
    model_names: list[str]

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="allow")


settings = Settings()  # type: ignore
