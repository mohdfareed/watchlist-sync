"""Change monitoring for one Scryer title without deleting media or canceling downloads."""

import logging
from threading import Event

from .client import ScryerClient, ScryerError
from .models import ScryerModel, Title

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Monitoring operations
# =============================================================================


def set_title_monitoring(client: ScryerClient, title: Title, monitored: bool) -> None:
    """Change only title monitoring, preserving policies, episode choices and downloads."""
    if title.monitored == monitored:
        return
    result = client.query(
        _MONITOR_QUERY,
        {"input": {"titleId": title.id, "monitored": monitored}},
        _MonitoringResponse,
    ).result
    if result.id != title.id or result.monitored != monitored:
        raise ScryerError("Scryer did not confirm title monitoring.")
    title.monitored = monitored
    _logger.info("Title monitoring: %s title=%s monitored=%s.", title.name, title.id, monitored)


def monitor_title(client: ScryerClient, title: Title, stop: Event) -> None:
    """Enable a title without changing its existing policy or season/episode selections."""
    if stop.is_set():
        raise InterruptedError("Scryer monitoring cancelled")
    set_title_monitoring(client, title, True)


# =============================================================================
# MARK: Response models and helpers
# =============================================================================


class _Monitored(ScryerModel):
    id: str
    monitored: bool


class _MonitoringResponse(ScryerModel):
    result: _Monitored


# =============================================================================
# MARK: GraphQL operations
# =============================================================================

_MONITOR_QUERY = """mutation MonitorTitle($input: SetTitleMonitoredInput!) {
  result: setTitleMonitored(input: $input) { id monitored }
}"""
