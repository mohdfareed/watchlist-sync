"""Browser pairing and saved Plex authentication."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from plexapi.exceptions import PlexApiException
from plexapi.myplex import MyPlexAccount, MyPlexJWTLogin
from requests import RequestException, Response, Session

from app.settings import Settings

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Public interface
# =============================================================================


class AuthenticationError(Exception):
    """An authentication failure with a message safe to log."""


def authenticate(config_dir: Path, session: Session, stop: Event) -> MyPlexAccount:
    """Authenticate with retry backoff, safe HTTP diagnostics, and cooperative shutdown."""
    # Observe only this authentication operation, including PlexAPI's pairing thread.
    diagnostics = _AuthDiagnostics()
    session.hooks["response"].append(diagnostics)
    delay = 60.0
    try:
        while not stop.is_set():
            diagnostics.retry_after = 0.0
            try:
                return _authenticate_once(config_dir, session, stop)
            except (AuthenticationError, PlexApiException, RequestException) as error:
                if stop.is_set():
                    break
                detail = (
                    str(error) if isinstance(error, AuthenticationError) else type(error).__name__
                )

            # Back off failed attempts without blocking cooperative shutdown.
            wait = max(delay, diagnostics.retry_after)
            _logger.error(
                "Plex authentication failed: %s Retrying in %g seconds; "
                "saved credentials retained.",
                detail,
                wait,
            )
            if stop.wait(wait):
                break
            delay = min(delay * 2, 300.0)
    finally:
        session.hooks["response"].remove(diagnostics)

    raise InterruptedError("Plex authentication cancelled")


def with_authentication[T](
    settings: Settings, func: Callable[[MyPlexAccount], T], stop: Event
) -> T:
    """Run the given function with an authenticated Plex account."""
    with Session() as session:
        account = authenticate(settings.config_dir, session, stop)
        if stop.is_set():
            raise InterruptedError("Plex read cancelled")

        username: str = str(account.username or "<token>")  # pyright: ignore
        _logger.debug("Authenticated with Plex using account: %s", username)

        return func(account)


# =============================================================================
# MARK: Device identity
# =============================================================================


def _load_login(directory: Path, session: Session) -> MyPlexJWTLogin:
    # Keep the device identity and signing keys together in a private directory.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    client_path = directory / "client-id"
    private_path = directory / "private.key"
    public_path = directory / "public.key"
    token_path = directory / "token"
    identity_exists = [path.exists() for path in (client_path, private_path, public_path)]
    saved_identity = all(identity_exists)

    if not saved_identity and (any(identity_exists) or token_path.exists()):
        raise AuthenticationError(
            "Plex authentication files are incomplete. Restore the plex authentication "
            "directory from backup, or remove it to pair again."
        )

    # Reuse the same client identity when verifying or refreshing saved credentials.
    client_id = client_path.read_text().strip() if saved_identity else str(uuid4())
    if not client_id:
        raise AuthenticationError(
            "Plex client-id is empty; restore it or remove the auth directory."
        )

    login = MyPlexJWTLogin(
        session=session,
        requestTimeout=20,
        oauth=True,
        headers={
            "X-Plex-Client-Identifier": client_id,
            "X-Plex-Product": "Watchlist Sync",
        },
        keypair=(str(private_path), str(public_path)) if saved_identity else (None, None),
        jwtToken=token_path.read_text().strip() if token_path.exists() else None,
    )

    # Save new signing keys before pairing so the resulting token can be refreshed.
    if not saved_identity:
        login.generateKeypair(  # pyright: ignore[reportUnknownMemberType]
            keyfiles=(str(private_path), str(public_path))
        )
        client_path.write_text(client_id)

    for path in (client_path, private_path, public_path):
        path.chmod(0o600)

    return login


# =============================================================================
# MARK: Browser pairing
# =============================================================================


def _pair(login: MyPlexJWTLogin, stop: Event) -> None:
    # Display the short-lived authorization link, never the resulting token or keys.
    try:
        # PlexAPI infers a literal type for this documented, configurable interval.
        login.POLLINTERVAL = 5  # pyright: ignore[reportAttributeAccessIssue]
        login.run(timeout=600)  # pyright: ignore[reportUnknownMemberType]
        url = login.oauthUrl()  # pyright: ignore[reportUnknownMemberType]
        _logger.warning("Authorize Plex in your browser: %s", url)
        _logger.info("Waiting up to 10 minutes for Plex authorization; checking every 5 seconds.")

        # Wake on shutdown rather than waiting for the whole pairing timeout.
        while not login.finished:
            if stop.wait(login.POLLINTERVAL):
                raise InterruptedError("Plex pairing cancelled")

        if stop.is_set():
            raise InterruptedError("Plex pairing cancelled")

        if not login.waitForLogin():
            raise AuthenticationError(
                "Plex pairing ended without a credential. See the preceding HTTP diagnostics; "
                "browser sign-in alone does not confirm that this client received access."
            )
    finally:
        login.stop()


# =============================================================================
# MARK: Credential verification and storage
# =============================================================================


def _obtain_credential(login: MyPlexJWTLogin, stop: Event) -> None:
    if not login.jwtToken:  # pyright: ignore[reportUnknownMemberType]
        _logger.info("No saved Plex credential; starting browser pairing.")
        _pair(login, stop)
        _logger.info("Plex returned a credential; verifying signature and account access.")
        return

    if login.verifyJWT():
        return

    _logger.info("Refreshing the saved Plex credential.")
    login.refreshJWT()


def _authenticate_once(config_dir: Path, session: Session, stop: Event) -> MyPlexAccount:
    # Reuse the device identity and obtain a new or refreshed credential if needed.
    directory = config_dir / "plex"
    login = _load_login(directory, session)
    _obtain_credential(login, stop)

    # Reject missing or invalid credentials before resolving account access.
    token = login.jwtToken  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(token, str) or not token.strip():
        raise AuthenticationError(
            "Plex pairing did not complete. Check connectivity and run the app again."
        )

    if not login.verifyJWT(refreshWithinDays=0):
        raise AuthenticationError(
            "Plex token verification failed; saved credentials were retained."
        )

    if stop.is_set():
        raise InterruptedError("Plex authentication cancelled")

    _logger.debug("Plex credential verified; checking account access.")
    account = MyPlexAccount(token=token, session=session, timeout=20)

    # Replace the saved token only after both verification steps succeed.
    token_path = directory / "token"
    temporary_path = directory / "token.tmp"
    temporary_path.touch(mode=0o600, exist_ok=True)
    temporary_path.chmod(0o600)
    temporary_path.write_text(token)
    temporary_path.replace(token_path)
    _logger.debug("Verified Plex credential saved atomically.")

    return account


# =============================================================================
# MARK: HTTP diagnostics
# =============================================================================


def _retry_after_seconds(value: str) -> float:
    # Retry-After accepts either seconds or an HTTP date.
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass

    try:
        deadline = parsedate_to_datetime(value)
        return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
    except ValueError, TypeError, OverflowError:
        return 0.0


@dataclass
class _AuthDiagnostics:
    # Retain the retry delay reported by PlexAPI's background pairing requests.
    retry_after: float = 0.0

    def __call__(self, response: Response, **kwargs: object) -> None:
        # Never log request queries, headers, response bodies, or raw library exceptions.
        url = urlsplit(response.url)
        endpoint = url.path
        if "/pins/" in endpoint:
            endpoint = endpoint.split("/pins/", 1)[0] + "/pins/<id>"
        _logger.debug(
            "Plex auth HTTP %s %s%s -> %d (%.2fs)",
            response.request.method,
            url.hostname,
            endpoint,
            response.status_code,
            response.elapsed.total_seconds(),
        )
        if response.ok:
            return

        self.retry_after = _retry_after_seconds(response.headers.get("Retry-After", ""))

        hint = {
            401: "Credential or pairing proof rejected.",
            403: "Access denied.",
            404: "Endpoint or pairing no longer available.",
            429: "Rate limited; pausing before another authentication attempt.",
        }.get(response.status_code, "Plex rejected the request or is unavailable.")
        _logger.error("Plex auth HTTP %d at %s: %s", response.status_code, endpoint, hint)
