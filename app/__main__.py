"""Initialize the application and report its exit status."""

import logging

from pydantic import ValidationError

from app.plex.auth import AuthenticationError
from app.settings import load_settings
from app.state import StateError, StateStore
from app.sync import sync_sources
from app.worker import run

_logger = logging.getLogger(__package__)


# =============================================================================
# MARK: Application entrypoint
# =============================================================================


def main() -> int:
    """Initialize and run the sync worker, returning a process exit status."""
    try:  # Load and validate settings.
        settings = load_settings(_logger)
    except ValidationError, OSError:
        return 1

    try:  # Run the main worker.
        store = StateStore(settings.config_dir / "state.json")
        run(lambda stop: sync_sources(settings, stop, store), settings)

    # Report actionable authentication/state failures without exposing credentials.
    except (AuthenticationError, StateError) as error:
        _logger.error("%s", error)
        return 1

    # Report fatal errors without exposing request URLs, tokens, or response bodies.
    except Exception as error:
        _logger.error("Worker failed (%s).", type(error).__name__)
        return 1

    # Cover Ctrl+C outside the worker's signal-handling window.
    except KeyboardInterrupt:
        _logger.info("Stopped.")
        return 130

    return 0  # Controlled shutdown without error.


if __name__ == "__main__":
    raise SystemExit(main())
