# Known Issues

Bugs found and fixed, and limitations still open, newest first. The full reasoning and the code behind each fix are in [CHANGELOG.md](CHANGELOG.md) and the commit history; this file is the short index a reviewer can scan.

## Fixed

### The first request after an idle period returned a 500 in the deployment

Replit's managed PostgreSQL, which is Neon-backed, closes a connection left idle for a few minutes and terminates open ones when its compute autosuspends. The connection pool had no liveness check, so the next request drew a dropped connection and its first query failed with `psycopg.errors.AdminShutdown`; a reload then succeeded once the pool had replaced the connection. The engine now pre-pings a pooled connection on checkout and recycles it before the idle cutoff, so the deployment reconnects on its own. SQLite, used locally and in the tests, keeps its default pool, since it has no server to drop a connection.

### The reader toolbar buttons were three different heights on Safari

A button carrying a leading glyph rendered taller than a plain one because nothing fixed its line height, and Safari gave the `<button>` elements their own metrics on top of that; the pane-toggle glyph was clipped by its own tight line box. Every button now lays out through a flex box with a fixed line height and a cleared native appearance, so an `<a>`, `<span>`, and `<button>` all come out the same size. Most visible on a tablet, where the toolbar wraps onto two rows.

## Open

### OCR system binaries on Replit Autoscale

Whether `pdftoppm` and `tesseract` install on Replit Autoscale through Nix is unconfirmed. If they don't, a scanned PDF is rejected with a stated reason in the deployment, the same behaviour as a local machine without them, so nothing is mis-parsed.

### No schema migration tooling

`create_all` builds the schema the models declare, and a database from an earlier schema is rebuilt rather than migrated. Adopting Alembic is the plan for the first release with users.
