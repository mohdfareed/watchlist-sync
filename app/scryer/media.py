"""Read current catalog and request records; no retained state or reconciliation."""

from threading import Event

from app.plex.models import PlexItem

from .client import ScryerClient, ScryerError
from .models import ExternalId, MediaRequest, ScryerModel, Title

# =============================================================================
# MARK: Public API
# =============================================================================


def list_titles(client: ScryerClient, *, stop: Event) -> list[Title]:
    """Read every View-visible title, including unmonitored titles and all facets.

    Nested collections, episodes, files and series-movie links are unpaginated.
    Offset pages are not atomic; concurrent catalog edits can affect membership.
    """
    titles: list[Title] = []
    seen_ids: set[str] = set()
    offset = 0

    # Follow the service's continuation flag, not an assumed page length.
    while True:
        if stop.is_set():
            raise InterruptedError("Scryer catalog read cancelled")

        page = client.query(_TITLES_QUERY, {"offset": offset}, _TitlesResponse).titles
        page_ids = {title.id for title in page.items}
        if len(page_ids) != len(page.items) or not seen_ids.isdisjoint(page_ids):
            raise ScryerError("Scryer title pagination repeated records; retry the read.")
        if page.has_more and not page.items:
            raise ScryerError("Scryer title pagination made no progress.")

        titles.extend(page.items)
        seen_ids.update(page_ids)
        offset += len(page.items)
        if not page.has_more:
            return titles


def list_media_requests(client: ScryerClient) -> list[MediaRequest]:
    """Read all statuses in ManageTitles-visible libraries, not just owned requests.

    Scryer v0.21.12 returns the complete list and exposes no pagination arguments.
    Lack of ManageTitles permission can produce an empty list rather than an error.
    """
    return client.query(_REQUESTS_QUERY, {}, _RequestsResponse).media_requests


def get_version(client: ScryerClient) -> str:
    """Read the running version for diagnostics, without probing or changing settings."""
    return client.query("query Version { scryerVersion }", {}, _VersionResponse).scryer_version


def matches_identity(item: PlexItem, external_ids: list[ExternalId]) -> bool:
    """Require shared provider IDs to agree, rejecting conflicting identities."""
    ids = {entry.source: entry.value for entry in external_ids}
    shared = item.external_ids.keys() & ids.keys()
    return bool(shared) and all(item.external_ids[source] == ids[source] for source in shared)


def matching_items[T: (Title, MediaRequest)](item: PlexItem, records: list[T]) -> list[T]:
    """Match within the movie or show namespace using exact provider identifiers."""
    if not item.external_ids:
        raise ScryerError("Plex item has no provider IDs; matching cannot safely proceed.")
    return [
        record
        for record in records
        if (record.facet == "movie") == (item.type == "movie")
        and matches_identity(item, record.external_ids)
    ]


# =============================================================================
# MARK: Response models
# =============================================================================


class _TitlePage(ScryerModel):
    items: list[Title]
    has_more: bool


class _TitlesResponse(ScryerModel):
    titles: _TitlePage


class _RequestsResponse(ScryerModel):
    media_requests: list[MediaRequest]


class _VersionResponse(ScryerModel):
    scryer_version: str


# =============================================================================
# MARK: Queries
# =============================================================================

# Verified against api/graphql/schema.graphql at the scryer-v0.21.12 tag.
_TITLES_QUERY = """
query ManagedTitles($offset: Int!) {
  titles(limit: 100, offset: $offset, sort: {key: ADDED, direction: ASC}) {
    hasMore
    items {
      id libraryId name facet externalIds { source value }
      monitored monitorType metadataFetchedAt
      mediaFiles { id episodeId seriesMovieLinkIds role scanStatus qualityLabel }
      collections {
        id collectionType collectionIndex monitored
        episodes {
          id seasonNumber episodeNumber monitored
          mediaAvailability { state primaryQualityLabel }
        }
      }
      seriesMovieLinks { id monitored metadataActive }
    }
  }
}
"""
_REQUESTS_QUERY = """
query MediaRequests {
  mediaRequests {
    id libraryId title facet externalIds { source value }
    status createdTitleId requestedMonitorType requestedQualityProfileId
  }
}
"""
