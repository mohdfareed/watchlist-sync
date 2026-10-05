"""Authenticated HTTP transport for Scryer's queries and mutations."""

import logging
import re
from types import TracebackType
from typing import Self

from pydantic import BaseModel, SecretStr, ValidationError
from requests import RequestException, Session

_logger = logging.getLogger(__name__)

# =============================================================================
# MARK: GraphQL client
# =============================================================================


class ScryerError(Exception):
    """A safe diagnostic message without remote bodies, URLs, or credentials."""


class ScryerClient:
    """Run authenticated GraphQL operations with validated responses and safe errors."""

    def __init__(self, graphql_url: str, api_key: SecretStr) -> None:
        """Create a session using only the configured endpoint and API key."""
        self._url = graphql_url
        self._api_key = api_key
        self._session = Session()

        # Use only the configured API key, not implicit netrc credentials.
        self._session.trust_env = False

    def __enter__(self) -> Self:
        """Return this client for use within a managed session."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP session when leaving the context."""
        self._session.close()

    def query[T: BaseModel](self, query: str, variables: dict[str, object], model: type[T]) -> T:
        """Validate complete GraphQL data as model, raising ScryerError on failure."""
        operation = re.search(r"\b(?:query|mutation)\s+(\w+)", query)
        name = operation[1] if operation else "unnamed"
        # Bound requests and reject redirects so credentials stay at the configured endpoint.
        try:
            with self._session.post(
                self._url,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                timeout=(10, 30),
                allow_redirects=False,
            ) as response:
                _logger.debug(
                    "Scryer %s: HTTP %d (%.2fs).",
                    name,
                    response.status_code,
                    response.elapsed.total_seconds(),
                )
                if not 200 <= response.status_code < 300:
                    hint = (
                        "check SCRYER_API_KEY and its owner in Scryer Profile / API keys"
                        if response.status_code in {401, 403}
                        else "check Scryer connectivity and service logs"
                    )
                    raise ScryerError(
                        f"Scryer {name} failed (HTTP {response.status_code}); {hint}."
                    )
                payload = response.json()

        # Handle session exceptions.
        except RequestException:
            raise ScryerError(
                "Scryer HTTP request failed; check connectivity and the API key."
            ) from None
        except ValueError:
            raise ScryerError("Scryer returned invalid JSON.") from None

        # Never accept partial GraphQL data as a complete diagnostic read.
        if not isinstance(payload, dict):
            raise ScryerError("Scryer returned no GraphQL data; check the endpoint and API key.")
        if payload.get("errors"):
            raise ScryerError(
                f"Scryer {name} failed; check its service logs, permissions, and API version."
            )
        if not isinstance(payload.get("data"), dict):
            raise ScryerError(f"Scryer {name} returned no GraphQL data.")

        try:
            return model.model_validate(payload["data"])
        except ValidationError as error:
            for issue in error.errors(
                include_input=False, include_context=False, include_url=False
            ):
                _logger.debug(
                    "Scryer %s schema mismatch at %s (%s).", name, issue["loc"], issue["type"]
                )
            raise ScryerError(
                f"Scryer {name} response does not match the expected schema; see the DEBUG log."
            ) from None
