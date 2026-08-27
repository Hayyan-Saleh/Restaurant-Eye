from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # General
    INITIAL_ADMIN_EMAIL: str = "admin@restaurant.com"
    INITIAL_ADMIN_PASSWORD: str = "admin123"
    PROJECT_NAME: str = "Restaurant Floor Monitoring"

    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str


    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # "127.0.0.1" rather than "localhost": on this Windows/Memurai setup,
    # redis.asyncio's DNS resolution of "localhost" returns the IPv6 "::1"
    # candidate first, and Memurai only listens on IPv4 -- connect() then
    # hangs until timeout before ever trying the v4 address. Using the
    # literal loopback IP sidesteps the resolution order entirely.

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # Email / SMTP
    SMTP_HOST: str
    SMTP_PORT: int = 465
    SMTP_USER: str
    SMTP_PASSWORD: str
    SMTP_FROM: str

    # OTP
    OTP_EXPIRE_MINUTES: int = 10
    DEV_MODE: bool = False  # Set to true for development bypass

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()