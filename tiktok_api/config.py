from functools import lru_cache
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LH_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./lead_hunter_api.db"
    redis_url: str = "redis://localhost:6379/0"
    admin_key: SecretStr
    operator_key: SecretStr = SecretStr("")
    viewer_key: SecretStr = SecretStr("")
    encryption_keys: SecretStr
    client_key: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = "https://localhost:8443/api/v1/oauth/callback"
    max_accounts: int = Field(10, ge=1, le=100)
    queue_mode: str = "celery"
    experimental_search_enabled: bool = False
    experimental_ms_token: SecretStr = SecretStr("")
    research_enabled: bool = False
    research_project: str = ""
    research_client_key: str = ""
    research_client_secret: SecretStr = SecretStr("")
    api_requests_per_minute: int = Field(120, ge=10, le=1000)
    provider_requests_per_minute: int = Field(60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_secrets(self):
        keys = [x.get_secret_value() for x in (self.admin_key, self.operator_key, self.viewer_key)]
        present = [x for x in keys if x]
        if any(len(x) < 32 or x.startswith("CHANGE_ME") for x in present):
            raise ValueError("API keys must be random values of at least 32 characters")
        if len(set(present)) != len(present):
            raise ValueError("API keys for different roles must differ")
        for key in self.encryption_keys.get_secret_value().split(","):
            Fernet(key.strip().encode())
        uri = urlsplit(self.redirect_uri)
        if uri.scheme != "https" or not uri.hostname or uri.query or uri.fragment or uri.username:
            raise ValueError("TikTok Web redirect URI must be a static HTTPS URL")
        if self.queue_mode not in ("celery", "local"):
            raise ValueError("queue_mode must be celery or local")
        if self.research_enabled and not all(
            (self.research_project, self.research_client_key, self.research_client_secret.get_secret_value())
        ):
            raise ValueError("Research requires a separately approved project and credentials")
        return self


@lru_cache
def get_settings():
    return Settings()
