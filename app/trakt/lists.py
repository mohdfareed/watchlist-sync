"""Read complete native Trakt movie/show watchlists, excluding other list types."""

import logging
from threading import Event
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from requests import Response, Session

from app.settings import Settings
from app.watchlist import WatchlistItem

from .auth import TraktError, authenticate

_logger = logging.getLogger(__name__)


# =============================================================================
# MARK: Public interface
# =============================================================================


def read_watchlist(settings: Settings, stop: Event) -> dict[str, WatchlistItem]:
    """Return only a complete authenticated native watchlist with stable Trakt IDs."""
    if settings.trakt_client_id is None:
        raise TraktError("Set TRAKT_CLIENT_ID to enable Trakt.")
    with Session() as session:
        session.trust_env = False
        session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "watchlist-sync/0.1.0",
                "trakt-api-version": "2",
                "trakt-api-key": settings.trakt_client_id.get_secret_value(),
            }
        )
        token = authenticate(settings, session, stop)
        session.headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        watchlist: dict[str, WatchlistItem] = {}
        for media_type, endpoint in (
            ("movie", "https://api.trakt.tv/sync/watchlist/movies/added/asc"),
            ("show", "https://api.trakt.tv/sync/watchlist/shows/added/asc"),
        ):
            page_number = 1
            first_page: _Page | None = None
            count = 0
            while True:
                if stop.is_set():
                    raise InterruptedError("Trakt watchlist read cancelled")
                page = _get_page(settings, session, stop, endpoint, page_number)

                # Require stable pagination metadata and reject repeats or truncated pages.
                first_page = first_page or page
                if (page.limit, page.pages, page.total) != (
                    first_page.limit,
                    first_page.pages,
                    first_page.total,
                ):
                    raise TraktError("Trakt pagination changed during the read; retrying later.")
                for entry in page.items:
                    if entry.type != media_type:
                        raise TraktError("Trakt returned the wrong media type; snapshot rejected.")
                    media = entry.movie if entry.type == "movie" else entry.show
                    if media is None:
                        raise TraktError(
                            "Trakt returned missing media metadata; snapshot rejected."
                        )
                    item = WatchlistItem(
                        id=f"trakt://{entry.type}/{media.ids.trakt}",
                        type=entry.type,
                        title=media.title,
                        year=media.year,
                        external_ids={
                            source: str(value)
                            for source, value in media.ids.model_dump().items()
                            if source != "trakt" and value is not None
                        },
                    )
                    if item.id in watchlist:
                        raise TraktError("Trakt repeated a title; snapshot rejected.")
                    watchlist[item.id] = item
                count += len(page.items)
                if page_number >= page.pages:
                    break
                page_number += 1
            if count != first_page.total:
                raise TraktError("Trakt returned an incomplete list; snapshot rejected.")
    _logger.debug("Trakt watchlist read complete: %d items.", len(watchlist))
    return watchlist


# =============================================================================
# MARK: Authenticated page retrieval
# =============================================================================


def _get_page(
    settings: Settings, session: Session, stop: Event, endpoint: str, page_number: int
) -> _Page:
    params = {"page": page_number, "limit": 100}
    response = session.get(endpoint, params=params, timeout=(10, 30), allow_redirects=False)
    if response.status_code != 401:
        with response:
            return _read_page(response, page_number)

    response.close()
    token = authenticate(settings, session, stop, force_refresh=True)
    session.headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    with session.get(endpoint, params=params, timeout=(10, 30), allow_redirects=False) as retry:
        return _read_page(retry, page_number)


# =============================================================================
# MARK: Response models and completeness
# =============================================================================


class _Ids(BaseModel):
    trakt: int = Field(gt=0, strict=True)
    tmdb: int | None = Field(default=None, gt=0, strict=True)
    tvdb: int | None = Field(default=None, gt=0, strict=True)
    imdb: str | None = None


class _Media(BaseModel):
    title: str = Field(min_length=1)
    year: int | None = None
    ids: _Ids


class _Entry(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    type: Literal["movie", "show"]
    movie: _Media | None = None
    show: _Media | None = None


class _Page(BaseModel):
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    pages: int = Field(ge=0)
    total: int = Field(ge=0)
    items: list[_Entry]


def _read_page(response: Response, requested_page: int) -> _Page:
    _logger.debug("Trakt watchlist page %d: HTTP %d.", requested_page, response.status_code)
    if response.status_code != 200:
        raise TraktError(
            f"Trakt watchlist failed (HTTP {response.status_code}); "
            "check connectivity, account access and TRAKT_CLIENT_ID."
        )
    page = _Page(
        page=response.headers.get("X-Pagination-Page"),
        limit=response.headers.get("X-Pagination-Limit"),
        pages=response.headers.get("X-Pagination-Page-Count"),
        total=response.headers.get("X-Pagination-Item-Count"),
        items=response.json(),
    )
    if page.page != requested_page:
        raise TraktError("Trakt returned invalid watchlist pagination; snapshot rejected.")
    expected_pages = (page.total + page.limit - 1) // page.limit
    if page.pages != expected_pages and not (page.total == 0 and page.pages == 1):
        raise TraktError("Trakt pagination totals disagree; snapshot rejected.")
    expected_items = min(page.limit, max(0, page.total - (page.page - 1) * page.limit))
    if len(page.items) != expected_items:
        raise TraktError("Trakt returned a truncated page; snapshot rejected.")
    return page
