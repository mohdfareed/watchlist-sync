"""Read the complete Plex watchlist and the identities needed to match them to Scryer."""

import logging
from threading import Event
from typing import Any

from plexapi.myplex import MyPlexAccount
from plexapi.video import Movie, Show

from app.watchlist import WatchlistItem

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Public interface
# =============================================================================


class PlexReadError(Exception):
    """A safe explanation for rejecting an incomplete or unsupported Plex read."""


def read_watchlist(account: MyPlexAccount, stop: Event) -> dict[str, WatchlistItem]:
    """Read a complete watchlist snapshot with stable identities and provider IDs."""
    # Build the cloud snapshot by stable GUID, not titles or list order.
    watchlist: dict[str, WatchlistItem] = {}
    for item in _read_watchlist(account):
        if stop.is_set():
            raise InterruptedError("Plex watchlist read cancelled")
        entry = _item(item)

        # Reject unsupported or repeated identities rather than silently losing entries.
        if entry.type not in {"movie", "show"} or not entry.id.startswith("plex://"):
            raise PlexReadError("Unsupported watchlist identity; keeping the previous snapshot.")
        if entry.id in watchlist:
            raise PlexReadError("Plex watchlist repeated an item; keeping the previous snapshot.")

        watchlist[entry.id] = entry

    _logger.debug("Plex watchlist read complete: %d items.", len(watchlist))
    return watchlist


# =============================================================================
# MARK: List retrieval and completeness
# =============================================================================


def _check_complete(items: Any, expected: int | None = None) -> None:
    # Use the known membership count, or the pagination count retained by PlexAPI.
    total = expected if expected is not None else items.totalSize
    if total is None:
        total = items.size

    # Reject incomplete reads so missing results cannot be mistaken for list removals.
    if total is None or len(items) != total:
        raise PlexReadError("Plex returned an incomplete list; keeping the previous snapshot.")


def _read_watchlist(account: MyPlexAccount) -> list[Movie | Show]:
    # Include external identifiers for matching against Scryer without relying on titles.
    items: Any = account.watchlist(includeGuids=1)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # Return only a complete read, leaving the caller's previous snapshot safe on failure.
    _check_complete(items)
    return items  # pyright: ignore[reportUnknownVariableType]


# =============================================================================
# MARK: Item identity
# =============================================================================


def _external_ids(item: Any) -> dict[str, str]:
    # Split provider GUIDs such as tmdb://123 into the identifiers we can match in Scryer.
    ids: dict[str, str] = {}
    for guid in item.guids:
        source, _, value = guid.id.partition("://")
        if source in {"tmdb", "tvdb", "imdb"} and value:
            if source in ids and ids[source] != value:
                raise PlexReadError(
                    "Plex item has conflicting provider IDs; keeping previous state."
                )
            ids[source] = value
    return ids


def _item(item: Any) -> WatchlistItem:
    # Require a media identity before admitting the item into a snapshot.
    if not isinstance(item.guid, str) or not item.guid:
        raise PlexReadError("Plex item has no GUID; keeping the previous snapshot.")

    return WatchlistItem(
        id=item.guid,
        type=item.type,
        title=item.title,
        year=item.year,
        external_ids=_external_ids(item),
    )
