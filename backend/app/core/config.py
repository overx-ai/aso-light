from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./aso_light.db"
    SECRET_KEY: str = "change-me-in-production"
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
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
