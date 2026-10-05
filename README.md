# watchlist-sync

Plex-driven media management through Scryer:

- Add to watchlist → request and monitor the movie/show.
- Remove from watchlist → unmonitor, keeping downloaded files.

Movies and regular series episodes are monitored, including future episodes.
Specials settings and selections are preserved. Existing `ADVANCED` series
retain their custom policy and selections; only title monitoring is toggled.

File deletion is manual through Plex. This tool never deletes media.

## Configuration

Required environment variables:

- `SCRYER_URL`: Scryer base URL, without `/graphql`.
- `SCRYER_API_KEY`: API key whose owner has `View` and
  `ManageTitles` access to the target libraries. Create it under
  **Profile → API keys**.

The tool uses Scryer’s default library for the matched movie, series, or anime
facet and preserves its quality-profile defaults. New titles are added through
Scryer’s title-management API, which starts acquisition directly.

Optional:

- `CONFIG_DIR`: authentication, state, and logs; default `/config`.
  Set a writable directory for local runs.
- `LOG_LEVEL`: console verbosity; default `INFO`.
- `SYNC_INTERVAL_SEC`: seconds between polling attempts; default `30`.
- `WATCHLIST_GRACE_SEC`: addition delay in seconds; default `60`, including
  startup contents. Removing an item during the delay cancels its addition.

## Run

```sh
./scripts/docker.sh
```

Authorize Plex using the link in the first-run logs. Keep the `/config` volume
across container updates. File logs: `CONFIG_DIR/watchlist-sync.log`.

For background operation:

```sh
docker compose -f docker/compose.yaml up --build -d
docker compose -f docker/compose.yaml logs -f
docker compose -f docker/compose.yaml stop
```

To build from Git, set `WATCHLIST_SYNC_BUILD_CONTEXT` to the repository's HTTPS
Git URL with `#<commit-or-tag>` appended.

## Development

Requires Python 3.14+ and uv. Run from the repository root:

```sh
./scripts/build.sh # Ruff fixes, formatting, and package build
uv run --locked python -m app
```
