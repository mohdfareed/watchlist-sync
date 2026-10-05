"""Selected fields from the Scryer v0.21.12 schema, with snake_case names.

Facet values are movie/tv/anime; other enum values retain Scryer's wire spelling.
Nullable fields are required in responses: missing data must not look like absence.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel

# =============================================================================
# MARK: Field types
# =============================================================================


# Pydantic resolves Facet during model construction, so its validator must exist first.
def _normalize_facet(value: object) -> object:
    if not isinstance(value, str):
        return value

    return {"MOVIE": "movie", "SERIES": "tv", "ANIME": "anime"}.get(value, value)


type Facet = Annotated[Literal["movie", "tv", "anime"], BeforeValidator(_normalize_facet)]
type RequestStatus = Literal["PENDING", "APPROVED", "REJECTED", "CANCELED"]
type MonitorType = Literal[
    "MONITORED",
    "UNMONITORED",
    "FUTURE_EPISODES",
    "MISSING_AND_FUTURE_EPISODES",
    "ALL_EPISODES",
    "ADVANCED",
    "NONE",
]


# =============================================================================
# MARK: Shared models
# =============================================================================


class ScryerModel(BaseModel):
    """Validate Scryer fields strictly with camelCase aliases and redacted error inputs."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, strict=True, hide_input_in_errors=True
    )


class ExternalId(ScryerModel):
    """An identifier and its external metadata source."""

    source: str
    value: str


# =============================================================================
# MARK: Catalog models
# =============================================================================


class EpisodeAvailability(ScryerModel):
    """Scryer-reported episode availability and primary quality."""

    state: Literal[
        "AVAILABLE", "PENDING_SCAN", "SCAN_FAILED", "REVIEW_REQUIRED", "MISSING", "UNMONITORED"
    ]
    primary_quality_label: str | None


class Episode(ScryerModel):
    """An episode's numbering, monitoring state, and media availability."""

    id: str
    season_number: str | None
    episode_number: str | None
    monitored: bool
    media_availability: EpisodeAvailability


class Collection(ScryerModel):
    """A title's episode grouping and collection-level monitoring state."""

    id: str
    collection_type: Literal["SEASON", "MOVIE", "ARC", "SPECIALS"]
    collection_index: str
    monitored: bool
    episodes: list[Episode]


class MediaFile(ScryerModel):
    """File evidence, not a derived playable/complete flag; paths are not queried."""

    id: str
    episode_id: str | None
    series_movie_link_ids: list[str]
    role: str
    scan_status: str
    quality_label: str | None


class SeriesMovieLink(ScryerModel):
    """A series-movie association's monitoring and metadata state."""

    id: str
    monitored: bool
    metadata_active: bool


class Title(ScryerModel):
    """A managed title with monitoring state and nested catalog records."""

    id: str
    library_id: str
    name: str
    facet: Facet
    external_ids: list[ExternalId]
    monitored: bool
    monitor_type: MonitorType | None
    metadata_fetched_at: str | None
    media_files: list[MediaFile]
    collections: list[Collection]
    series_movie_links: list[SeriesMovieLink]


# =============================================================================
# MARK: Request models
# =============================================================================


class MediaRequest(ScryerModel):
    """Approval lifecycle is separate from download or media availability."""

    id: str
    library_id: str
    title: str
    facet: Facet
    external_ids: list[ExternalId]
    status: RequestStatus
    created_title_id: str | None
    requested_monitor_type: MonitorType | None
    requested_quality_profile_id: str | None
