"""Selected fields from the Scryer v0.21.12 schema, with snake_case names.

Facet values are movie/tv/anime; other enum values retain Scryer's wire spelling.
Nullable fields are required in responses: missing data must not look like absence.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel

from app.watchlist import IdentityError, WatchlistItem

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


class Episode(ScryerModel):
    """An episode's numbering and monitoring state."""

    id: str
    season_number: str | None
    episode_number: str | None
    monitored: bool


class Collection(ScryerModel):
    """A title's episode grouping and collection-level monitoring state."""

    id: str
    collection_type: Literal["SEASON", "MOVIE", "ARC", "SPECIALS"]
    collection_index: str
    monitored: bool
    episodes: list[Episode]


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
    collections: list[Collection]

    def identity(self) -> WatchlistItem:
        """Return validated provider aliases without conflating movie and series namespaces."""
        ids: dict[str, str] = {}
        for entry in self.external_ids:
            if entry.source in ids and ids[entry.source] != entry.value:
                raise IdentityError(
                    "Scryer title has conflicting external IDs; resolve them first."
                )
            ids[entry.source] = entry.value
        return WatchlistItem(
            id=self.id,
            type="movie" if self.facet == "movie" else "show",
            title=self.name,
            external_ids=ids,
        )


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
