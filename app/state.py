"""Persist watchlist membership, grace deadlines, and outstanding title changes."""

import json
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, FiniteFloat, ValidationError

from app.watchlist import IdentityError, WatchlistItem, WatchlistSnapshot

# =============================================================================
# MARK: Persistent state
# =============================================================================


class StateError(Exception):
    """A state failure with recovery guidance safe to log."""


class ManagedTitle(BaseModel):
    """Retained ownership and unfinished addition work for one exact media identity."""

    item: WatchlistItem
    title_id: str | None = None
    scope_pending: bool = Field(
        default=True, validation_alias=AliasChoices("scope_pending", "dirty")
    )
    search_pending: bool = False


class SyncState(BaseModel):
    """Complete source membership, retained ownership, and unfinished addition work."""

    version: Literal[1] = 1
    watchlists: dict[str, dict[str, WatchlistItem]] = Field(default_factory=dict)
    pending: dict[str, dict[str, FiniteFloat]] = Field(default_factory=dict)
    managed: dict[str, ManagedTitle] = Field(default_factory=dict)


class StateStore:
    """Load and atomically checkpoint state before writes and after confirmed outcomes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.startup = True
        self._observed_sources: set[str] = set()
        self.state = SyncState()

        try:
            contents = path.read_text()
        except FileNotFoundError:
            return
        except OSError:
            raise StateError("Cannot read state.json; restore access before restarting.") from None

        try:
            # Legacy snapshots have no ownership evidence and may come from read-only runs.
            # Retain membership/deadlines, then adopt current entries on the first fresh read.
            payload = json.loads(contents)
            if isinstance(payload, dict) and "version" not in payload:
                legacy = _LegacyState.model_validate(payload)
                self.state.watchlists["plex"] = legacy.watchlist
                self.state.pending["plex"] = legacy.pending
                return
            self.state = SyncState.model_validate_json(contents)
        except ValueError, ValidationError:
            raise StateError(
                "Cannot load state.json; restore valid state before restarting."
            ) from None

    def observe(self, snapshots: list[WatchlistSnapshot], now: float) -> SyncState:
        """Stage complete membership while retaining unfinished work and grace deadlines."""
        state = self.state.model_copy(deep=True)
        for snapshot in snapshots:
            self._observe(state, snapshot, now)
        return state

    def save(self, state: SyncState, *, observed_sources: set[str] | None = None) -> None:
        """Replace the private state file before advancing the in-memory checkpoint."""
        if self.startup or state != self.state:
            try:
                self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = self._path.with_suffix(".tmp")
                temporary.touch(mode=0o600, exist_ok=True)
                temporary.chmod(0o600)
                temporary.write_text(state.model_dump_json(indent=2))
                temporary.replace(self._path)
            except OSError:
                raise StateError(
                    "Cannot save state.json; stop and restore writable storage."
                ) from None
        self.state = state.model_copy(deep=True)
        self.startup = False
        self._observed_sources.update(observed_sources or set())

    # =========================================================================
    # MARK: Snapshot staging
    # =========================================================================

    def _observe(
        self, state: SyncState, snapshot: WatchlistSnapshot, now: float
    ) -> None:
        previous = state.watchlists.get(snapshot.source, {})

        # A reused source ID must not silently replace the old title with another identity.
        for item_id, item in snapshot.items.items():
            if item_id not in previous:
                continue
            before = previous[item_id]
            shared = before.external_ids.keys() & item.external_ids.keys()
            if (
                before.type != item.type
                or not shared
                or any(
                    before.external_ids[source] != item.external_ids[source] for source in shared
                )
            ):
                raise IdentityError(
                    "Source item identity changed; review metadata before retrying."
                )

        deadlines = state.pending.setdefault(snapshot.source, {})
        for item_id in list(deadlines):
            if item_id not in snapshot.items:
                del deadlines[item_id]
        for item_id in snapshot.items:
            if snapshot.source not in self._observed_sources or item_id not in previous:
                deadlines.setdefault(item_id, now)
        state.watchlists[snapshot.source] = snapshot.items


# =============================================================================
# MARK: Legacy-state compatibility
# =============================================================================


class _LegacyState(BaseModel):
    pending: dict[str, FiniteFloat]
    watchlist: dict[str, WatchlistItem]
