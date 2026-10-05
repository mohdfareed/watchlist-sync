"""Handle Plex watchlist changes through Scryer, then save the successful baseline."""

import logging
from threading import Event
from time import time

from plexapi.exceptions import PlexApiException
from requests import RequestException

from app.plex.auth import with_authentication
from app.plex.events import ChangeDetector, PlexEvent
from app.plex.lists import PlexReadError, read_watchlist
from app.plex.models import PlexItem
from app.scryer import ScryerClient, ScryerError, get_version
from app.scryer.media import list_media_requests, list_titles, matching_items
from app.scryer.monitoring import monitor_title, unmonitor_title
from app.scryer.requests import add_title, approve_request
from app.settings import Settings

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Synchronization
# =============================================================================


def sync_plex(settings: Settings, stop: Event, changes: ChangeDetector) -> None:
    """Read complete membership and advance state only after successful Scryer actions."""
    try:
        watchlist = with_authentication(
            settings, lambda account: read_watchlist(account, stop), stop
        )
    except PlexReadError as error:
        _logger.error("%s", error)
        return
    except (PlexApiException, RequestException) as error:
        if not stop.is_set():
            _logger.error(
                "Plex poll failed (%s); state retained; retry in %g seconds.",
                type(error).__name__,
                settings.sync_interval_sec,
            )
        return

    sync_watchlist(settings, stop, changes, watchlist)


def sync_watchlist(
    settings: Settings,
    stop: Event,
    changes: ChangeDetector,
    watchlist: dict[str, PlexItem],
) -> None:
    """Reconcile a complete membership snapshot and save only successful event handling."""
    prepared = changes.prepare(watchlist, settings.watchlist_grace_sec, time())
    if stop.is_set():
        return
    if changes.startup or prepared.events:
        _logger.info(
            "Plex watchlist: %d items, %d pending, %d events.",
            len(watchlist),
            len(prepared.state.pending),
            len(prepared.events),
        )
        for item in watchlist.values():
            _logger.debug(
                "Watchlist: %s id=%s provider_ids=%s.", item.title, item.id, item.external_ids
            )
        try:
            with ScryerClient(
                f"{str(settings.scryer_url).rstrip('/')}/graphql", settings.scryer_api_key
            ) as client:
                if changes.startup:
                    _logger.info("Connected to Scryer %s.", get_version(client))
                for event in prepared.events:
                    if stop.is_set():
                        return
                    _logger.info(
                        "Handling %s: %s id=%s.", event.kind, event.item.title, event.item.id
                    )
                    _handle_event(client, event, stop)
        except ScryerError as error:
            _logger.error(
                "%s Previous Plex state retained; retry in %g seconds.",
                error,
                settings.sync_interval_sec,
            )
            return
    if stop.is_set():
        return
    changes.commit(prepared.state)
    _logger.debug(
        "Poll complete: %d watchlist items, %d pending, %d events handled.",
        len(watchlist),
        len(prepared.state.pending),
        len(prepared.events),
    )


# =============================================================================
# MARK: Watchlist decisions
# =============================================================================


def _handle_event(client: ScryerClient, event: PlexEvent, stop: Event) -> None:
    item = event.item
    titles = matching_items(item, list_titles(client, stop=stop))
    if len(titles) > 1:
        raise ScryerError("Multiple Scryer titles match; resolve the duplicate identities first.")
    if titles:
        if event.kind == "watchlist_removed":
            unmonitor_title(client, titles[0])
            return
        monitor_title(client, titles[0], stop)
        return

    pending = [
        request
        for request in matching_items(item, list_media_requests(client))
        if request.status == "PENDING"
    ]
    if event.kind == "watchlist_removed":
        if pending:
            raise ScryerError("Removal has a pending Scryer request; resolve it before retrying.")
        _logger.info("Already absent from Scryer: %s; nothing to unmonitor.", item.title)
        return
    if len(pending) > 1:
        raise ScryerError("Multiple pending requests match; resolve the duplicate requests first.")
    if pending:
        title_id = approve_request(client, pending[0])
    else:
        title_id = add_title(client, item)

    # Confirm the created catalog identity before applying its monitoring policy.
    title = next((title for title in list_titles(client, stop=stop) if title.id == title_id), None)
    if title is None:
        raise ScryerError("Approved title is not visible yet; retaining the event for retry.")
    monitor_title(client, title, stop)
