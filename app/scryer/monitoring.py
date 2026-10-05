"""Change monitoring for one Scryer title without deleting media or canceling downloads."""

import logging
from threading import Event

from .client import ScryerClient, ScryerError
from .models import ScryerModel, Title

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Monitoring operations
# =============================================================================


def unmonitor_title(client: ScryerClient, title: Title) -> None:
    """Disable title acquisition while retaining files, downloads, and episode choices."""
    if not title.monitored:
        _logger.info("Already unmonitored: %s title=%s.", title.name, title.id)
        return
    response = client.query(
        _MONITOR_QUERY, {"input": {"titleId": title.id, "monitored": False}}, _MonitoringResponse
    ).result
    if response.id != title.id or response.monitored:
        raise ScryerError("Scryer did not confirm title unmonitoring.")
    _logger.info("Unmonitored: %s title=%s; files and downloads retained.", title.name, title.id)


def monitor_title(client: ScryerClient, title: Title, stop: Event) -> None:
    """Monitor regular series scopes while preserving specials and advanced selections."""
    if stop.is_set():
        raise InterruptedError("Scryer monitoring cancelled")

    # Advanced series retain their policy and all saved collection/episode selections.
    advanced = title.facet != "movie" and title.monitor_type == "ADVANCED"

    # Wait for ordinary series scopes before accepting an addition as complete.
    if not advanced and title.facet != "movie" and title.metadata_fetched_at is None:
        raise ScryerError(
            "Scryer show metadata is not ready; retaining the event for retry. "
            "If this persists, check metadata hydration in Scryer."
        )
    policy = "MONITORED" if title.facet == "movie" else "ALL_EPISODES"
    if not advanced and title.monitor_type != policy:
        result = client.query(
            _POLICY_QUERY,
            {"input": {"titleId": title.id, "options": {"monitorType": policy}}},
            _PolicyResponse,
        ).result
        if result.id != title.id or result.monitor_type != policy:
            raise ScryerError("Scryer did not confirm the requested monitoring policy.")
        _logger.info("Monitoring policy: %s -> %s.", title.name, policy)
    if not title.monitored:
        result = client.query(
            _MONITOR_QUERY,
            {"input": {"titleId": title.id, "monitored": True}},
            _MonitoringResponse,
        ).result
        if result.id != title.id or not result.monitored:
            raise ScryerError("Scryer did not confirm title monitoring.")

    if advanced:
        _logger.info("Monitored: %s title=%s; advanced selections retained.", title.name, title.id)
        return

    # Policy changes govern future hydration without resetting existing episode flags.
    # Preserve specials settings and selections by updating only regular seasons.
    for collection in title.collections:
        if collection.collection_type != "SEASON" or not _regular(collection.collection_index):
            continue
        if any(not _regular(episode.season_number) for episode in collection.episodes):
            raise ScryerError(
                "A regular season contains unknown/special episode scopes; review it."
            )
        if collection.monitored and all(episode.monitored for episode in collection.episodes):
            continue
        if stop.is_set():
            raise InterruptedError("Scryer monitoring cancelled")

        # Scryer enables the collection's episodes together, including individual exceptions.
        result = client.query(
            _COLLECTION_QUERY,
            {"input": {"collectionId": collection.id, "monitored": True}},
            _MonitoringResponse,
        ).result
        if result.id != collection.id or not result.monitored:
            raise ScryerError("Scryer did not confirm collection monitoring.")

    _logger.info("Monitored: %s title=%s; specials retained.", title.name, title.id)


# =============================================================================
# MARK: Response models and helpers
# =============================================================================


class _Monitored(ScryerModel):
    id: str
    monitored: bool


class _MonitoringResponse(ScryerModel):
    result: _Monitored


class _Policy(ScryerModel):
    id: str
    monitor_type: str | None


class _PolicyResponse(ScryerModel):
    result: _Policy


def _regular(number: str | None) -> bool:
    return number is not None and number.isdecimal() and int(number) > 0


# =============================================================================
# MARK: GraphQL operations
# =============================================================================

_MONITOR_QUERY = """mutation MonitorTitle($input: SetTitleMonitoredInput!) {
  result: setTitleMonitored(input: $input) { id monitored }
}"""
_POLICY_QUERY = """mutation MonitorPolicy($input: UpdateTitleInput!) {
  result: updateTitle(input: $input) { id monitorType }
}"""
_COLLECTION_QUERY = """mutation MonitorCollection($input: SetCollectionMonitoredInput!) {
  result: setCollectionMonitored(input: $input) { id monitored }
}"""
