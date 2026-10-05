"""Detect Plex changes and retain snapshots and grace deadlines across restarts."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from app.plex.models import PlexItem

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: State and event models
# =============================================================================


class StateError(Exception):
    """A state failure with a message safe to log."""


class SyncState(BaseModel):
    """Persisted list snapshots and pending watchlist deadlines."""

    watchlist: dict[str, PlexItem] = Field(default_factory=dict)
    pending: dict[str, FiniteFloat] = Field(default_factory=dict)


@dataclass(frozen=True)
class PlexEvent:
    """An item-specific change observed in a Plex list."""

    kind: Literal["watchlist_added", "watchlist_removed"]
    item: PlexItem


@dataclass(frozen=True)
class PreparedChanges:
    """A proposed baseline and the events to handle before committing it."""

    state: SyncState
    events: list[PlexEvent]


# =============================================================================
# MARK: Change detection and persistence
# =============================================================================


class ChangeDetector:
    """Detect list changes and persist successfully handled snapshots."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.startup = True  # Whether this is the first run.
        self._state = SyncState()

        # A missing file is a first run; unreadable state must not silently reset history.
        try:
            self._state = SyncState.model_validate_json(self._path.read_text())
        except FileNotFoundError:
            pass
        except OSError, ValidationError:
            raise StateError(
                "Cannot load state.json; restore it or remove it to start fresh."
            ) from None

    def prepare(
        self,
        watchlist: dict[str, PlexItem],
        grace_seconds: float,
        now: float,
    ) -> PreparedChanges:
        """Prepare events without consuming them; commit only after handling succeeds."""
        events: list[PlexEvent] = []
        pending = self._state.pending.copy()

        # Apply observed removals before considering any grace deadline.
        for item_id in self._state.watchlist.keys() - watchlist.keys():
            pending.pop(item_id, None)
            # A startup replay can be pending even though the title is already monitored.
            events.append(PlexEvent("watchlist_removed", self._state.watchlist[item_id]))

        # Replay all current entries on startup; otherwise select only newly observed IDs.
        additions = (
            watchlist.keys() if self.startup else watchlist.keys() - self._state.watchlist.keys()
        )

        # Start the grace period for new additions without extending saved deadlines on restart.
        for item_id in additions:
            pending.setdefault(item_id, now + grace_seconds)

        # Recheck current membership so removed items cannot become requests after the delay.
        for item_id, deadline in list(pending.items()):
            if item_id not in watchlist:
                del pending[item_id]
                continue
            if now < deadline:
                continue

            # Grace period elapsed; release the addition and remove it from pending.
            events.append(PlexEvent("watchlist_added", watchlist[item_id]))
            del pending[item_id]

        # Stage the next baseline without consuming events; the caller commits after success.
        return PreparedChanges(
            state=SyncState(watchlist=watchlist, pending=pending),
            events=events,
        )

    def commit(self, state: SyncState) -> None:
        """Save successful work atomically, then advance the in-memory baseline."""
        if self.startup or state != self._state:
            try:
                self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary_path = self._path.with_suffix(".tmp")
                temporary_path.write_text(state.model_dump_json(indent=2))
                temporary_path.chmod(0o600)
                temporary_path.replace(self._path)

            except OSError:
                raise StateError(
                    "Cannot save state.json; previous state was not advanced."
                ) from None

        # Make grace-period scheduling and cancellation visible without an activity journal.
        for item_id in state.pending.keys() - self._state.pending.keys():
            _logger.info("Watchlist addition waiting for grace period: %s", item_id)
        for item_id in self._state.pending.keys() - state.watchlist.keys():
            _logger.info("Pending watchlist addition cancelled: %s", item_id)

        self._state = state
        self.startup = False
