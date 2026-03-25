"""
Config — Typed application settings from environment variables.

All config is loaded once at import time. In production, set
env vars in your hosting platform. In dev, use a .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Environment
    ENV: str = os.getenv("ENV", "development")
    IS_PROD: bool = ENV == "production"

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 1024

    # Auth
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7
    PASSWORD_MIN_LENGTH: int = 8

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./insani.db")

    # CORS
    CORS_ORIGINS: list[str] = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")
    ]

    # Rate limiting
    LOGIN_RATE_LIMIT: str = "5/minute"
    SIGNUP_RATE_LIMIT: str = "3/minute"
    AI_RATE_LIMIT: str = "20/minute"


settings = Settings()
