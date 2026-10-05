"""Pair once with Trakt and persist rotating tokens without logging credentials."""

import logging
import os
from pathlib import Path
from threading import Event
from time import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, SecretStr, field_serializer
from requests import Response, Session

from app.settings import Settings
from app.state import StateError

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Public interface
# =============================================================================


class TraktError(Exception):
    """A safe Trakt failure summary without remote bodies or credentials."""


def authenticate(
    settings: Settings, session: Session, stop: Event, *, force_refresh: bool = False
) -> SecretStr:
    """Return a saved access token, refreshing or advancing device pairing as needed."""
    if stop.is_set():
        raise InterruptedError("Trakt authentication cancelled")
    if settings.trakt_client_id is None:
        raise TraktError("Set TRAKT_CLIENT_ID to enable Trakt.")

    path = settings.config_dir / "trakt-auth.json"
    auth = _load(path)
    now = time()
    if auth.tokens is not None:
        tokens = auth.tokens
        if not force_refresh and now < tokens.created_at + tokens.expires_in - 60:
            return tokens.access_token

        # Refresh tokens are single-use; checkpoint their replacements before any read.
        with _post(
            session,
            "/oauth/token",
            {
                "client_id": settings.trakt_client_id.get_secret_value(),
                "refresh_token": tokens.refresh_token.get_secret_value(),
                "grant_type": "refresh_token",
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            },
        ) as response:
            if response.status_code == 200:
                auth.tokens = _Tokens.model_validate(response.json())
                _save(path, auth)
                _logger.debug("Trakt tokens refreshed and saved.")
                return auth.tokens.access_token
            payload = response.json() if response.status_code == 400 else None
            if not isinstance(payload, dict) or payload.get("error") != "invalid_grant":
                raise TraktError(
                    f"Trakt refresh failed (HTTP {response.status_code}); saved tokens retained."
                )
            auth.tokens = None
            _save(path, auth)
            _logger.warning("Trakt authorization expired or revoked; pairing is required again.")

    # Pairing spans ordinary polls and restarts, so it never blocks Plex indefinitely.
    pairing = auth.pairing
    if pairing is None or now >= pairing.expires_at:
        with _post(
            session,
            "/oauth/device/code",
            {"client_id": settings.trakt_client_id.get_secret_value()},
        ) as response:
            _require_success(response, "device authorization")
            code = _DeviceCode.model_validate(response.json())
        pairing = _Pairing(
            device_code=code.device_code,
            user_code=code.user_code,
            expires_at=now + code.expires_in,
            interval=code.interval,
            next_poll_at=now + code.interval,
        )
        auth.pairing = pairing
        _save(path, auth)
        _logger.info(
            "Authorize Trakt at https://auth.trakt.tv/activate; "
            "use pairing.user_code from %s. The code is not logged.",
            path,
        )
        raise TraktError("Waiting for Trakt user authorization.")
    if now < pairing.next_poll_at:
        raise TraktError("Waiting for Trakt user authorization.")

    # Save the next poll time before sending so a restart respects Trakt's interval.
    pairing.next_poll_at = now + pairing.interval
    _save(path, auth)
    with _post(
        session,
        "/oauth/device/token",
        {
            "client_id": settings.trakt_client_id.get_secret_value(),
            "code": pairing.device_code.get_secret_value(),
        },
    ) as response:
        if response.status_code == 200:
            auth.tokens = _Tokens.model_validate(response.json())
            auth.pairing = None
            _save(path, auth)
            _logger.info("Trakt authorized; tokens saved.")
            return auth.tokens.access_token
        if response.status_code == 400:
            raise TraktError("Waiting for Trakt user authorization.")
        if response.status_code == 429:
            pairing.interval += 5
            pairing.next_poll_at = time() + max(pairing.interval, _retry_after(response))
            _save(path, auth)
            raise TraktError("Trakt authorization rate limited; waiting before retry.")
        if response.status_code in {404, 409, 410, 418}:
            auth.pairing = None
            _save(path, auth)
            raise TraktError("Trakt pairing expired or was rejected; retry on the next poll.")
        _require_success(response, "device token")
    raise TraktError("Trakt did not confirm authorization.")


# =============================================================================
# MARK: Private credential models
# =============================================================================


class _Tokens(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    token_type: Literal["bearer"]
    created_at: int = Field(ge=0, strict=True)
    expires_in: int = Field(gt=0, strict=True)

    @field_serializer("access_token", "refresh_token")
    def _serialize_secret(self, value: SecretStr) -> str:
        return value.get_secret_value()


class _DeviceCode(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    device_code: SecretStr = Field(min_length=1)
    user_code: SecretStr = Field(min_length=1)
    expires_in: int = Field(gt=0, strict=True)
    interval: int = Field(gt=0, strict=True)


class _Pairing(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    device_code: SecretStr = Field(min_length=1)
    user_code: SecretStr = Field(min_length=1)
    expires_at: FiniteFloat
    interval: int = Field(gt=0, strict=True)
    next_poll_at: FiniteFloat

    @field_serializer("device_code", "user_code")
    def _serialize_secret(self, value: SecretStr) -> str:
        return value.get_secret_value()


class _Authentication(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    tokens: _Tokens | None = None
    pairing: _Pairing | None = None


# =============================================================================
# MARK: Credential storage and HTTP
# =============================================================================


def _load(path: Path) -> _Authentication:
    try:
        return _Authentication.model_validate_json(path.read_text())
    except FileNotFoundError:
        return _Authentication()
    except OSError, ValueError:
        raise StateError(
            "Cannot load trakt-auth.json; restore valid credentials and readable storage."
        ) from None


def _save(path: Path, auth: _Authentication) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.touch(mode=0o600, exist_ok=True)
        temporary.chmod(0o600)
        with temporary.open("w") as output:
            output.write(auth.model_dump_json(indent=2))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    except OSError:
        raise StateError(
            "Cannot save trakt-auth.json; stop and restore writable storage before retrying."
        ) from None


def _post(session: Session, endpoint: str, payload: dict[str, str]) -> Response:
    response = session.post(
        f"https://auth.trakt.tv{endpoint}",
        json=payload,
        headers={"Authorization": None},
        timeout=(10, 30),
        allow_redirects=False,
    )
    _logger.debug("Trakt auth %s: HTTP %d.", endpoint, response.status_code)
    return response


def _require_success(response: Response, operation: str) -> None:
    if response.status_code != 200:
        raise TraktError(
            f"Trakt {operation} failed (HTTP {response.status_code}); "
            "check connectivity and TRAKT_CLIENT_ID."
        )


def _retry_after(response: Response) -> float:
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdecimal() else 0.0
