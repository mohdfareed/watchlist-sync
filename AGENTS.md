# Working agreement

## Scope and budget

- One user, infrequent use, a small homelab automation—not a framework.
- Two-hour implementation budget; ten hours of lifetime maintenance.
- Work in small increments. Stop for feedback after each agreed checkpoint.
- No automated tests, test dependencies, or scaffolding unless requested.
- Use uv and Ruff. Validate with lint/format checks, builds, and manual checks.
- Compose builds directly from Git. No registry or publishing pipeline.
- The tool is named `watchlist-sync`; its Python package lives in `app/`.
  Keep container files at `docker/` and use the `python -m app` entrypoint.
- Docker runs as UID/GID 10001 with persistent authentication in `/config`.
  No media mounts or inbound ports.
- Do not deploy, publish, or mutate live service data without explicit approval.

## Behavior

- Watchlist addition → adopt an exact-ID existing title or request a new title
  in Scryer. Monitor movies and current/future regular series episodes.
- Preserve specials settings and existing special selections. Existing
  `ADVANCED` series retain their policy and custom selections; only toggle
  title monitoring for these titles.
- Watchlist removal → unmonitor; do not explicitly cancel downloads or delete
  files.
- Process existing contents on every startup, then poll for list changes.
  Also compare saved snapshots to detect net removals made while stopped.
- A small Plex change detector compares snapshots and emits item-specific
  events: watchlist additions/removals.
- Watchlist additions have a grace period, tracked by stable item ID. Removal
  during the grace period cancels the pending addition. Use
  `WATCHLIST_GRACE_SEC=60` by default, including startup contents. Preserve
  pending deadlines across restarts and check membership before releasing them.
- Persist the last snapshots and pending watchlist addition deadlines together
  in `/config/state.json`. Save atomically after successful event handling;
  failures retain the previous baseline for retry. No activity history or
  action journal.
- Plex is the event source. Query and write to Scryer as needed to handle
  events; do not build a Scryer snapshot/event loop or a generic event-bus
  framework.
- No continuous enforcement, blanket episode resets, or list-priority machinery.
- Delegate media operations to Scryer/Weaver. File deletion is manual in Plex;
  this tool never requests deletion or deletes files.
- Keep source fetching separate from membership reconciliation. A future
  Trakt source must reconcile union membership before emitting removals;
  do not implement Trakt or a speculative source framework now.
- Confirm behavioral departures first.

## Code and documentation

- Check existing patterns before writing code. Organize by concrete
  responsibility; repair existing mechanisms before adding replacements.
- Keep code and operational surface minimal: no speculative interfaces,
  trivial wrappers, unnecessary callbacks, progress bars, or reminder scripts.
- Keep `worker.py` limited to app lifecycle plumbing. Put sync business logic in
  `sync.py` and wire it into the worker in `__main__.py`.
- Put public entrypoints and interfaces before private helpers and
  implementation details so readers can see how to use a module first.
  Keep definition-time dependencies (such as base classes and validators)
  before their consumers; do not add indirection just to force this order.
- Keep the happy path flat throughout control flow. Handle alternatives,
  skips, and failures first with guards, then proceed without unnecessary
  nesting or `else`. Preserve cleanup and shared follow-up work.
- Use descriptive names and readable type hints. Use named models for
  structured results rather than opaque positional tuples; use type aliases
  only to shorten type expressions.
- Prefix module-private loggers, helpers, classes, and constants with `_`.
  Keep intentionally shared interfaces public; do not access another module's
  private implementation in application code.
- Document public functions, classes, and properties with concise docstrings.
  Use comments, not docstrings, for non-obvious private implementation details.
- Keep runtime references rename-safe with symbols or framework metadata and
  explicit package attributes. Keep external contracts literal; do not add
  a naming framework or dynamic export machinery.
- Separate meaningful steps with whitespace and brief, action-oriented recipe
  comments. Do not narrate individual statements or add sections needlessly.
- Use `MARK` sections throughout substantive modules to expose their public
  interface and group distinct responsibilities, not just in selected files.
  Use three comment lines: an `=` border, `# MARK: <Title>`, and the same
  border. Borders are exactly 79 characters, including indentation and
  comment prefix. Skip empty ceremony in docstring-only or re-export modules.
  Ordinary recipe comments and Markdown headings need no borders.
- Keep substantive Python in normal `.py` files, not shell strings, including
  one-off diagnostics. Preserve existing script phases, progress messages,
  command choices, and setup/update behavior during focused changes.
- Keep one-use static data inline unless extraction adds logic or real reuse.
- Keep code within 100 columns and Markdown within 80. Keep files small.
- Prefer service defaults. Mark provisional policy with `# REVIEW:` comments.
- Every dependency, configuration rule, and paragraph must serve a current need.
- Judge every addition by lifetime maintenance cost, not ease of writing it.
  If its value is uncertain, ask before adding it.
- Document usage, not development status, publishing reminders, or
  hypotheticals. Change README only when needed to configure, run, or use the
  tool—not automatically after each task. Keep implementation details here or
  in code.
- Use Rich console logging and rotating plain-text DEBUG logs under
  `CONFIG_DIR`, following `machine`'s 10 MiB / three-backup limits. No Rich
  traceback setup.
- Keep service output in the existing logging setup, not a separate reporting
  framework. Use concise failure summaries and actionable recovery context;
  retain safe technical details in plain-text logs. Never embed Rich markup
  in log messages or log credentials. Preserve complete URLs and single-line
  records in non-interactive console output; leave external output untouched.
- If tests are explicitly requested, keep them proportionate and focused on
  business decisions, data preservation, permissions, and failure handling.
  Do not test UI wording/layout, framework behavior, or personal configuration.
  Reuse existing checks; do not retain exploratory coverage by default.
- Keep this file and README consistent.
