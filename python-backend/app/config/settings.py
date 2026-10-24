"""Typed environment configuration for the Python API."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FinCore Python API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://fincore:fincore@localhost:5432/fincore"
    jwt_issuer: str = "fincore"
    jwt_audience: str = "fincore-client"
    jwt_signing_key: SecretStr = SecretStr("development-only-change-this-key")
    cors_origins: str = "http://localhost:5173"
    default_limit_per_transaction_minor: int = 10_000_000
    default_limit_daily_minor: int = 50_000_000
    default_limit_weekly_minor: int = 100_000_000
    default_limit_monthly_minor: int = 200_000_000
    beneficiary_cooling_seconds: int = 86_400
    kyc_storage_path: str = "data/kyc-documents"
    report_storage_path: str = "data/reports"
    # "blob" stores KYC documents in Azure Blob Storage (Azurite locally); "filesystem" uses
    # kyc_storage_path and exists for tests and Docker-free runs.
    storage_provider: Literal["blob", "filesystem"] = "filesystem"
    azure_storage_connection_string: SecretStr = SecretStr("")
    kyc_blob_container: str = "kyc-documents"
    # "servicebus" publishes outbox events to an Azure Service Bus topic and consumes them from
    # a subscription; "inline" runs the event handlers directly inside the outbox worker.
    message_provider: Literal["servicebus", "inline"] = "inline"
    servicebus_connection_string: SecretStr = SecretStr("")
    servicebus_topic: str = "fincore-events"
    servicebus_subscription: str = "notifications"
    outbox_worker_enabled: bool = True
    outbox_poll_seconds: float = 2.0
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5
    rate_limit_enabled: bool = True
    # Fraud investigator agent (LangGraph + Gemini). Off unless GEMINI_API_KEY is set; the
    # agent only reads data and drafts a recommendation, a reviewer makes the decision.
    gemini_api_key: SecretStr = SecretStr("")
    agent_model: str = "gemini-2.5-flash"
    # Model turns per investigation; each turn can call several tools.
    agent_max_steps: int = 8
    agent_timeout_seconds: float = 90.0
    rate_limit_agent_per_minute: int = 5
    # Newline-separated list of passwords refused at registration (checked through a Bloom
    # filter). Empty uses the bundled list of common patterns; point it at a breach corpus for
    # real protection.
    common_passwords_path: str = ""
    # Largest accepted request body. The biggest legitimate request is a KYC upload: a 5 MiB
    # document is about 6.7 MiB once base64-encoded inside JSON.
    max_request_body_bytes: int = 8 * 1024 * 1024
    # Shared rate-limit counters; empty keeps them in-process (single replica, tests).
    redis_url: str = ""
    rate_limit_login_per_minute: int = 5
    # Login attempts per account (normalized email) per minute, whatever IP they come from, so
    # spreading a password-guessing run across many IPs does not multiply its budget.
    rate_limit_login_per_account_per_minute: int = 10
    rate_limit_register_per_minute: int = 10
    rate_limit_payment_per_minute: int = 10
    rate_limit_default_per_minute: int = 100
    admin_email: str = ""
    admin_password: SecretStr = SecretStr("")
    sanctions_names: str = ""
    refresh_token_lifetime_days: int = 30
    # Settlement fee applied per settled payment, in basis points of the amount (25 bps = 0.25%).
    settlement_fee_bps: int = 25
    # Periodic reconciliation by the background worker (spec section 49); the API endpoint
    # always allows manual runs.
    reconciliation_schedule_enabled: bool = False
    reconciliation_interval_seconds: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if self.app_env.lower() in {"production", "prod"} and len(
            self.jwt_signing_key.get_secret_value()
        ) < 32:
            raise ValueError("JWT_SIGNING_KEY must contain at least 32 characters in production")
        if self.message_provider == "servicebus" and not (
            self.servicebus_connection_string.get_secret_value()
        ):
            raise ValueError(
                "SERVICEBUS_CONNECTION_STRING is required when MESSAGE_PROVIDER=servicebus",
            )
        if self.storage_provider == "blob" and not (
            self.azure_storage_connection_string.get_secret_value()
        ):
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_PROVIDER=blob",
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
