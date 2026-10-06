# watchlist-sync

Plex and Trakt watchlist media management through Scryer:

- Add to watchlist → request and monitor the movie/show.
- Remove from both watchlists → unmonitor, keeping downloaded files.

Only Trakt's native movie/show watchlist is included. Custom lists, Backlog,
and history are excluded. A title stays monitored while present in either
source.

New shows monitor season 1 only; new movies are monitored. Every existing
title retains its policy, season and episode selections, and specials settings;
only title monitoring is toggled.

Watchlist membership controls the title-level monitored flag for adopted
identities. Manual changes to that flag are corrected on later polls; completed
episode/season selections are preserved across removal and re-addition.

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

- `TRAKT_CLIENT_ID`: enable Trakt alongside Plex; supply it from the existing
  1Password Media Environment. No client secret is required.
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
across container updates: `state.json` retains adopted identities, grace
deadlines and unfinished changes. Keep it to recover interrupted additions and
removals safely. Ownership is retained after removal so a late-created title
can still be unmonitored. Existing pending requests are left alone while absent;
if approval later creates a title, its monitoring is corrected automatically.

With Trakt enabled, authorize once at <https://auth.trakt.tv/activate> using
`pairing.user_code` in `CONFIG_DIR/trakt-auth.json`. For Docker, read it with:

```sh
docker compose -f docker/compose.yaml exec -w /config watchlist-sync \
  python -c \
'import json;print(json.load(open("trakt-auth.json"))["pairing"]["user_code"])'
```

Treat the code and authentication file as private.
Pairing continues across polls and restarts. Saved tokens refresh automatically;
revoked or unusable refresh tokens require authorization again. Keep the file
with the state volume. If it is corrupt or unwritable, restore valid storage
before restarting. Authorization codes and tokens never appear in service logs.

Polling provides eventual convergence, not an atomic add/remove transaction.
An in-flight addition may briefly monitor an item removed from the watchlist;
later successful polls correct it. Failed source reads preserve that source's
last complete membership. Trakt pagination must finish before its snapshot is
accepted. Scryer changes wait until every enabled source has supplied a complete
snapshot at least once. Grace deadlines survive restarts; additions wait for a
successful read that confirms membership before being released.

When upgrading an older unversioned state file, saved grace deadlines are
retained. Its snapshots cannot prove title ownership, so only current entries
are adopted; older absent entries are left untouched.

File logs: `CONFIG_DIR/watchlist-sync.log`.

For background operation:

```sh
docker compose -f docker/compose.yaml up --build -d
docker compose -f docker/compose.yaml logs -f
docker compose -f docker/compose.yaml stop
```

To build from Git, set `WATCHLIST_SYNC_BUILD_CONTEXT` to the repository's HTTPS
Git URL with `#<commit-or-tag>` appended.

## Release

From a clean working tree, publish the current commit as `latest`:

```sh
./scripts/release.sh
```

This moves and pushes only the `latest` tag, using your configured Git signing
and authentication. Deployments using `#latest` build that release on their
next deployment.

## Development

Requires Python 3.14+ and uv. Run from the repository root:

```sh
./scripts/build.sh # Ruff fixes, formatting, and package build
uv run --locked python -m app
```
