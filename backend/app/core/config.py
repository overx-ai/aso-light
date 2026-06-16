from cryptography.fernet import Fernet
from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings

# Known placeholder values shipped as defaults. Rejected at startup so a prod
# deploy that forgets to override them fails fast instead of issuing forgeable
# JWTs / encrypting .p8 keys under a guessable secret.
_SECRET_PLACEHOLDERS = frozenset(
    {
        "change-me-in-production",
        "change-me-jwt-secret",
    }
)
_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./aso_light.db"
    SECRET_KEY: str = "change-me-in-production"
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FERNET_KEY: str = ""
    RATE_CACHE_API_URL: str = "https://api.overx.ai"
    ANTHROPIC_API_KEY: str | None = None
    # --- AI translation providers ---
    # OpenRouter (openrouter.ai) is OpenAI-compatible; we call it via httpx.
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_TRANSLATION_MODEL: str = "anthropic/claude-3.5-haiku"
    # Ordered, comma-separated provider chain tried by build_translator().
    # Providers without a configured API key are skipped; the rest run as an
    # automatic fallback chain (failover on any provider error).
    TRANSLATION_PROVIDER_CHAIN: str = "openrouter,anthropic"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @field_validator("SECRET_KEY", "JWT_SECRET_KEY")
    @classmethod
    def reject_weak_secret(cls, v: str, info: ValidationInfo) -> str:
        if not v or v in _SECRET_PLACEHOLDERS:
            raise ValueError(
                f"{info.field_name} is unset or uses a known placeholder; "
                "set a strong, unique value (>= 32 chars)."
            )
        if len(v) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"{info.field_name} must be at least {_MIN_SECRET_LENGTH} characters."
            )
        return v

    @field_validator("FERNET_KEY")
    @classmethod
    def require_valid_fernet_key(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "FERNET_KEY is required; generate one with "
                "Fernet.generate_key().decode()."
            )
        try:
            Fernet(v.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("FERNET_KEY is not a valid Fernet key.") from exc
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


settings = Settings()
