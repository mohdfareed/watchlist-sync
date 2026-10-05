"""Resolve provider metadata and acquire titles through Scryer management APIs."""

import logging

from app.plex.models import PlexItem

from .client import ScryerClient, ScryerError
from .media import matches_identity
from .models import ExternalId, Facet, MediaRequest, ScryerModel

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Request operations
# =============================================================================


def add_title(client: ScryerClient, item: PlexItem) -> str:
    """Resolve exact metadata and add a monitored title to the matching default library."""
    # Use Scryer's metadata classification, never a guess based on a Plex show name.
    metadata = client.query(
        _METADATA_QUERY, {"query": item.title}, _MetadataResponse
    ).search_metadata_multi
    candidates: list[_Candidate] = []
    # Scryer includes anime in both buckets; its anime match is the specific classification.
    for facet, entries in (
        ("movie", metadata.movies),
        ("anime", metadata.anime),
        ("tv", metadata.series),
    ):
        if (facet == "movie") != (item.type == "movie"):
            continue
        candidates.extend(
            _Candidate(facet=facet, item=entry)
            for entry in entries
            if matches_identity(item, entry.external_ids)
        )
        if candidates:
            break
    if len(candidates) != 1:
        raise ScryerError(
            "Metadata search has no unique provider-ID match; retaining the event for retry."
        )
    facet = candidates[0].facet
    entry = candidates[0].item
    libraries = client.query(_LIBRARIES_QUERY, {}, _LibrariesResponse).libraries
    eligible = [library for library in libraries if library.facet == facet]
    defaults = [library for library in eligible if library.is_default]
    choices = defaults or eligible
    if len(choices) != 1:
        raise ScryerError("Set one default Scryer library for the matched media facet.")
    library = choices[0]
    policy = "MONITORED" if facet == "movie" else "ALL_EPISODES"
    added = client.query(
        _ADD_QUERY,
        {
            "input": {
                "libraryId": library.id,
                "facet": "SERIES" if facet == "tv" else facet.upper(),
                "name": entry.name,
                "monitored": True,
                "tags": [],
                "year": entry.year,
                "externalIds": [identity.model_dump() for identity in entry.external_ids],
                "options": {"monitorType": policy},
            }
        },
        _AddResponse,
    ).add_title
    _logger.info(
        "Scryer acquisition requested: %s title=%s library=%s hydration=%s reused=%s.",
        item.title,
        added.title.id,
        library.name,
        added.metadata_hydration_state,
        added.reused_existing_title,
    )

    return added.title.id


def approve_request(client: ScryerClient, request: MediaRequest) -> str:
    """Approve a pending request while retaining its quality and monitoring preferences."""
    if not request.requested_quality_profile_id:
        raise ScryerError("Pending request has no quality profile; select one in Scryer.")
    result = client.query(
        _APPROVE_QUERY,
        {
            "input": {
                "requestId": request.id,
                "qualityProfileId": request.requested_quality_profile_id,
            }
        },
        _ApproveResponse,
    ).approve_media_request
    _logger.info("Scryer request approved: %s title=%s.", request.title, result.title_id)
    if result.search_error:
        _logger.warning(
            "Scryer approved %s but could not queue its search; inspect Scryer Wanted.",
            request.title,
        )
    return result.title_id


# =============================================================================
# MARK: Response models
# =============================================================================


class _Library(ScryerModel):
    id: str
    name: str
    facet: Facet
    is_default: bool


class _LibrariesResponse(ScryerModel):
    libraries: list[_Library]


class _MetadataItem(ScryerModel):
    name: str
    year: int | None
    external_ids: list[ExternalId]


class _Candidate(ScryerModel):
    facet: Facet
    item: _MetadataItem


class _Metadata(ScryerModel):
    movies: list[_MetadataItem]
    series: list[_MetadataItem]
    anime: list[_MetadataItem]


class _MetadataResponse(ScryerModel):
    search_metadata_multi: _Metadata


class _TitleIdentity(ScryerModel):
    id: str


class _Added(ScryerModel):
    title: _TitleIdentity
    metadata_hydration_state: str
    reused_existing_title: bool


class _AddResponse(ScryerModel):
    add_title: _Added


class _Approved(ScryerModel):
    title_id: str
    search_error: str | None


class _ApproveResponse(ScryerModel):
    approve_media_request: _Approved


# =============================================================================
# MARK: GraphQL operations
# =============================================================================

_LIBRARIES_QUERY = """query ManagedLibraries {
  libraries(permission: MANAGE_TITLES) { id name facet isDefault }
}"""
_METADATA_QUERY = """query MatchMetadata($query: String!) {
  searchMetadataMulti(query: $query, limit: 100) {
    movies { name year externalIds { source value } }
    series { name year externalIds { source value } }
    anime { name year externalIds { source value } }
  }
}"""
_ADD_QUERY = """mutation AddWatchlistTitle($input: AddTitleInput!) {
  addTitle(input: $input) { title { id } metadataHydrationState reusedExistingTitle }
}"""
_APPROVE_QUERY = """mutation ApproveWatchlist($input: ApproveMediaRequestInput!) {
  approveMediaRequest(input: $input) { titleId searchError }
}"""
