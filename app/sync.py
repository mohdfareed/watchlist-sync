"""Reconcile complete watchlists with adopted Scryer titles."""

import logging
from dataclasses import dataclass, field
from threading import Event
from time import time

from plexapi.exceptions import PlexApiException
from pydantic import ValidationError
from requests import RequestException

from app.plex.auth import AuthenticationError, with_authentication
from app.plex.lists import PlexReadError
from app.plex.lists import read_watchlist as read_plex_watchlist
from app.scryer.client import ScryerClient, ScryerError
from app.scryer.media import find_title, list_media_requests, list_titles, matching_items
from app.scryer.models import Title
from app.scryer.monitoring import monitor_title, set_title_monitoring
from app.scryer.requests import add_title, approve_request, retry_search
from app.settings import Settings
from app.state import ManagedTitle, StateStore, SyncState
from app.trakt.auth import TraktError
from app.trakt.lists import read_watchlist as read_trakt_watchlist
from app.watchlist import IdentityError, WatchlistItem, WatchlistSnapshot, same_title

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Watchlist synchronization
# =============================================================================


def sync_sources(settings: Settings, stop: Event, store: StateStore) -> None:
    """Fetch each source, then reconcile their union with failed snapshots retained."""
    snapshots: list[WatchlistSnapshot] = []
    try:
        items = with_authentication(
            settings, lambda account: read_plex_watchlist(account, stop), stop
        )
        snapshots.append(WatchlistSnapshot(source="plex", items=items))
    except (PlexReadError, ValidationError) as error:
        _logger.error(
            "Plex membership rejected (%s); previous state retained.", type(error).__name__
        )
    except AuthenticationError as error:
        _logger.error("%s Previous Plex membership retained.", error)
    except (PlexApiException, RequestException) as error:
        if not stop.is_set():
            _logger.error("Plex read failed (%s); previous state retained.", type(error).__name__)

    if settings.trakt_client_id is not None and not stop.is_set():
        try:
            items = read_trakt_watchlist(settings, stop)
            snapshots.append(WatchlistSnapshot(source="trakt", items=items))
        except TraktError as error:
            _logger.warning("%s Previous Trakt membership retained.", error)
        except (RequestException, ValidationError, ValueError) as error:
            _logger.error("Trakt read failed (%s); previous state retained.", type(error).__name__)
    if not snapshots or stop.is_set():
        return

    try:
        sync_watchlists(settings, stop, store, snapshots)
    except (ScryerError, IdentityError, ValidationError) as error:
        if not stop.is_set():
            message = (
                str(error) if not isinstance(error, ValidationError) else "Invalid service identity"
            )
            _logger.error(
                "%s Outstanding work retained; retry in %g seconds.",
                message,
                settings.sync_interval_sec,
            )


def sync_watchlists(
    settings: Settings, stop: Event, store: StateStore, snapshots: list[WatchlistSnapshot]
) -> None:
    """Stage successful reads together before applying union additions and removals."""
    now = time()
    previous = _selections(store.state, now)
    state = store.observe(snapshots, settings.watchlist_grace_sec, now)
    fresh_sources = {snapshot.source for snapshot in snapshots}
    selections = _selections(state, now, fresh_sources)
    required_sources = {"plex", "trakt"} if settings.trakt_client_id is not None else {"plex"}
    if not required_sources <= state.watchlists.keys():
        store.save(state, observed_sources=fresh_sources)
        _logger.info("Waiting for the first complete source snapshots before Scryer changes.")
        return

    # Reject conflicting aliases before accepting membership or changing any title.
    # Additions and routine polls change only title flags, preserving saved selections.
    for selection in selections:
        key = _managed_key(state, selection.item)
        if key is not None and not any(
            same_title(state.managed[key].item, entry.item) for entry in previous
        ):
            state.managed[key].scope_pending = True
    for managed in state.managed.values():
        if not any(same_title(managed.item, selection.item) for selection in selections):
            managed.scope_pending = False
            managed.search_pending = False
    if stop.is_set():
        return
    store.save(state, observed_sources=fresh_sources)
    if not state.managed and not any(selection.ready for selection in selections):
        return

    with ScryerClient(
        f"{str(settings.scryer_url).rstrip('/')}/graphql", settings.scryer_api_key
    ) as client:
        titles = list_titles(client, stop=stop)

        # Finish independent identities even if another operation needs recovery.
        for selection in selections:
            if stop.is_set():
                return
            try:
                _apply_addition(client, store, state, selection, titles, stop)
            except (ScryerError, IdentityError, ValidationError) as error:
                _log_retry(selection.item, error)

        # Retain absent ownership so late additions and stale remote writes are corrected.
        for managed in state.managed.values():
            if stop.is_set():
                return
            if any(same_title(managed.item, selection.item) for selection in selections):
                continue
            try:
                _apply_absence(client, store, state, managed, titles)
            except (ScryerError, IdentityError, ValidationError) as error:
                _log_retry(managed.item, error)


# =============================================================================
# MARK: Selection models
# =============================================================================


@dataclass(frozen=True)
class _Member:
    source: str
    id: str


@dataclass
class _Selection:
    item: WatchlistItem
    members: list[_Member] = field(default_factory=list)
    ready: bool = False
    confirmed: bool = False


# =============================================================================
# MARK: Title changes
# =============================================================================


def _apply_addition(
    client: ScryerClient,
    store: StateStore,
    state: SyncState,
    selection: _Selection,
    titles: list[Title],
    stop: Event,
) -> None:
    key = _managed_key(state, selection.item)
    managed = state.managed.get(key) if key is not None else None
    if not selection.ready:
        return
    # Retained membership can repair completed title flags, but additions need a fresh read.
    if not selection.confirmed and (
        managed is None or managed.scope_pending or managed.search_pending
    ):
        return

    item = _merge(managed.item, selection.item) if managed else selection.item
    target = find_title(item, managed.title_id if managed else None, titles)
    if not selection.confirmed and target is None:
        return
    if target is not None:
        item = _merge(item, target.identity())
        # Scryer can bridge two source entries that supplied disjoint provider IDs.
        key = _managed_key(state, item)
    if key is None:
        source, value = next(iter(sorted(item.external_ids.items())))
        key = f"{item.type}:{source}:{value}"
    managed = state.managed.get(key) or ManagedTitle(item=item)
    managed.item = item
    if target is not None:
        managed.title_id = target.id
    state.managed[key] = managed

    # Claim the identity before the first write. A timeout may have succeeded remotely;
    # retaining this target lets a later removal undo that uncertain addition safely.
    store.save(state)
    if target is None:
        if managed.title_id is not None:
            raise ScryerError("Adopted title is not visible; retaining ownership and retrying.")
        pending = [
            request
            for request in matching_items(item, list_media_requests(client))
            if request.status == "PENDING"
        ]
        if len(pending) > 1:
            raise IdentityError("Multiple pending requests match; resolve duplicates first.")
        if pending:
            # A timeout can leave an approved title whose initial search failed.
            managed.search_pending = True
            store.save(state)
            result = approve_request(client, pending[0])
            managed.title_id = result.title_id
            managed.search_pending = result.search_pending
        else:
            managed.title_id = add_title(client, item)
        store.save(state)
        titles[:] = list_titles(client, stop=stop)
        target = find_title(item, managed.title_id, titles)
        if target is None:
            raise ScryerError("Created title is not visible yet; retaining work for retry.")
        managed.item = _merge(item, target.identity())
        store.save(state)
    if managed.scope_pending:
        monitor_title(client, target, stop)
        managed.scope_pending = False
    else:
        set_title_monitoring(client, target, True)
    if managed.search_pending:
        retry_search(client, target.id)
        managed.search_pending = False
    for member in selection.members:
        state.pending[member.source].pop(member.id, None)
    store.save(state)


def _apply_absence(
    client: ScryerClient,
    store: StateStore,
    state: SyncState,
    managed: ManagedTitle,
    titles: list[Title],
) -> None:
    target = find_title(managed.item, managed.title_id, titles)
    if target is None:
        # An empty lookup does not settle an in-flight creation or approval.
        # Leave pending requests alone; a later-created title will be unmonitored.
        return
    managed.title_id = target.id
    managed.item = _merge(managed.item, target.identity())
    store.save(state)
    set_title_monitoring(client, target, False)


def _log_retry(item: WatchlistItem, error: Exception) -> None:
    # Validation errors can contain remote inputs; report only the safe identity summary.
    message = "Invalid service identity" if isinstance(error, ValidationError) else str(error)
    _logger.error("%s: %s Outstanding work retained for retry.", item.title, message)


# =============================================================================
# MARK: Membership and identity
# =============================================================================


def _selections(
    state: SyncState, now: float, fresh_sources: set[str] | None = None
) -> list[_Selection]:
    selections: list[_Selection] = []
    for source, items in state.watchlists.items():
        for item in items.values():
            matching = [entry for entry in selections if same_title(entry.item, item)]
            deadline = state.pending.get(source, {}).get(item.id)
            # Never release an expired grace deadline on the strength of a stale snapshot.
            selection = _Selection(
                item=item,
                members=[_Member(source, item.id)],
                ready=deadline is None
                or (deadline <= now and (fresh_sources is None or source in fresh_sources)),
                confirmed=fresh_sources is None or source in fresh_sources,
            )
            # Coalesce aliases transitively, so two provider entries produce one operation.
            for entry in matching:
                selection.item = _merge(selection.item, entry.item)
                selection.members.extend(entry.members)
                selection.ready |= entry.ready
                selection.confirmed |= entry.confirmed
                selections.remove(entry)
            selections.append(selection)
    return selections


def _merge(left: WatchlistItem, right: WatchlistItem) -> WatchlistItem:
    ids = left.external_ids | right.external_ids
    if any(ids[source] != value for source, value in left.external_ids.items()):
        raise IdentityError("Conflicting external IDs; correct metadata before retrying.")
    return right.model_copy(update={"external_ids": ids})


def _managed_key(state: SyncState, item: WatchlistItem) -> str | None:
    matches = [key for key, entry in state.managed.items() if same_title(entry.item, item)]
    if len(matches) > 1:
        raise IdentityError("Multiple adopted identities match; review state before retrying.")
    return next(iter(matches), None)
