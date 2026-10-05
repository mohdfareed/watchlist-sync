"""Provider-independent watchlist identities and complete snapshots."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# =============================================================================
# MARK: Source interface
# =============================================================================


class IdentityError(Exception):
    """An unresolved identity that must not cause a removal or duplicate title."""


class WatchlistItem(BaseModel):
    """A source-local entry with cross-provider movie or series identifiers."""

    model_config = ConfigDict(hide_input_in_errors=True)

    id: str = Field(min_length=1)
    type: Literal["movie", "show"]
    title: str = Field(min_length=1)
    year: int | None = None
    external_ids: dict[str, str]

    @field_validator("external_ids")
    @classmethod
    def _validate_ids(cls, values: dict[str, str]) -> dict[str, str]:
        ids: dict[str, str] = {}
        for source, value in values.items():
            if source not in {"tmdb", "tvdb", "imdb"}:
                continue
            value = value.strip()
            pattern = r"tt[0-9]+" if source == "imdb" else r"[1-9][0-9]*"
            if not re.fullmatch(pattern, value):
                raise ValueError("Invalid external identifier; keeping previous membership")
            ids[source] = value
        if not ids:
            raise ValueError("No supported external identifiers; keeping previous membership")
        return ids


class WatchlistSnapshot(BaseModel):
    """A complete authenticated source read; incomplete reads must raise instead."""

    source: str = Field(min_length=1)
    items: dict[str, WatchlistItem]

    @model_validator(mode="after")
    def _validate_keys(self) -> WatchlistSnapshot:
        if any(key != item.id for key, item in self.items.items()):
            raise ValueError("Snapshot keys must match source item IDs")
        return self


# =============================================================================
# MARK: Exact identity matching
# =============================================================================


def same_title(left: WatchlistItem, right: WatchlistItem) -> bool:
    """Match shared provider IDs within a media type, rejecting conflicting aliases."""
    if left.type != right.type:
        return False
    shared = left.external_ids.keys() & right.external_ids.keys()
    if not any(left.external_ids[key] == right.external_ids[key] for key in shared):
        return False
    if any(left.external_ids[key] != right.external_ids[key] for key in shared):
        raise IdentityError("Conflicting external IDs; correct the source metadata and retry.")
    return True
