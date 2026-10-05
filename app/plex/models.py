"""The Plex identity needed to handle watchlist changes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# MARK: Public models
# =============================================================================


class PlexItem(BaseModel):
    """A watchlist item's stable identity and matching metadata."""

    model_config = ConfigDict(hide_input_in_errors=True)

    id: str = Field(min_length=1)
    type: Literal["movie", "show"]
    title: str
    year: int | None = None
    guid: str
    external_ids: dict[str, str] = Field(default_factory=dict)
