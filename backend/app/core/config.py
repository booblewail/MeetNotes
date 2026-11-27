from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "MeetNotes"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = (
        "postgresql+asyncpg://meetnotes:meetnotes_dev@localhost:5432/meetnotes"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "meetnotes-dev-secret-change-in-prod-9f8e7d6c5b4a3210"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 10080

    DEEPGRAM_API_KEY: str = "your_deepgram_key"
    GEMINI_API_KEY: str = "your_gemini_key"
    LLM_FAST_MODEL: str = "gemini-3.5-flash-lite"
    LLM_CONSOLIDATION_MODEL: str = "gemini-4-hydrogen"
    EMBEDDING_MODEL: str = "gemini-embedding-001"

    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_PUBLIC_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "meetnotes-audio"
    S3_REGION: str = "us-east-1"

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    AUDIO_SPOOL_DIR: str = "/tmp/meetnotes_audio"
    LOCAL_S3_FALLBACK_DIR: str = "/tmp/meetnotes_s3"

    PASS1_WORD_THRESHOLD: int = 100
    PASS1_ELAPSED_SECONDS: float = 60.0
    PASS1_MIN_TIMER_WORDS: int = 25
    PASS1_THINKING_BUDGET: int = 0
    PASS2_THINKING_BUDGET: int = 256
    PASS2_THINKING_LEVEL: str = "LOW"
    LOW_CONFIDENCE_THRESHOLD: float = 0.75

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_mock_stt(self) -> bool:
        val = (self.DEEPGRAM_API_KEY or "").strip()
        return val in ("", "your_deepgram_key", "mock")

    @property
    def is_mock_llm(self) -> bool:
        val = (self.GEMINI_API_KEY or "").strip()
        return val in ("", "your_gemini_key", "mock")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
