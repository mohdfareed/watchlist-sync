"""Runtime configuration, validation, and initialization."""

import logging
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.logging import setup_console_logging, setup_file_logging

# =============================================================================
# MARK: Configuration loading
# =============================================================================


def load_settings(logger: logging.Logger) -> Settings:
    """Load settings from environment variables and validate them."""

    # Start logging before validation so configuration failures are visible.
    console_handler = setup_console_logging()

    try:  # Load settings from the environment
        settings = Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        # Report validation errors without including their supplied values.
        for issue in error.errors(include_input=False, include_context=False, include_url=False):
            field = ".".join(str(part) for part in issue["loc"]).upper()
            logger.error("%s: %s", field, issue["msg"])
        raise

    # Keep detailed file logs while respecting the requested console verbosity.
    console_handler.setLevel(settings.log_level)
    try:
        setup_file_logging(settings.config_dir)
    except OSError as error:
        logger.error("Cannot open the log file in CONFIG_DIR (%s).", type(error).__name__)
        raise

    return settings


# =============================================================================
# MARK: Settings and validation
# =============================================================================


class Settings(BaseSettings):
    """Validated service endpoints, credentials, and worker configuration."""

    model_config = SettingsConfigDict(hide_input_in_errors=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    config_dir: Path = Path("/config")
    sync_interval_sec: float = Field(default=60, ge=1, allow_inf_nan=False)

    scryer_url: AnyHttpUrl
    scryer_api_key: SecretStr = Field(min_length=1)
    trakt_client_id: SecretStr | None = Field(default=None, min_length=1)

    @field_validator("trakt_client_id", mode="before")
    @classmethod
    def _optional_trakt_client(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("scryer_url")
    @classmethod
    def _validate_service_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Use a base URL without credentials, query parameters, or a fragment")
        return value
