<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Configuration

The service has one setting, the `PORT` environment variable. Everything else is fixed in code: server timeouts, the shutdown grace period, currency rates and the catalog itself. To change any of those, you edit the code and rebuild.

## Settings

| Setting | Read in | Default | What it changes |
|---|---|---|---|
| `PORT` | `run` in `cmd/server/main.go`, through `envOr` | `8080` when unset or empty | The TCP port the server listens on. It listens on all interfaces, at the address `":" + PORT`. |

`PORT` is set in two other places:

- **Docker image.** The `Dockerfile` sets `PORT=8080` and exposes port 8080.
- **Makefile.** The `run` target passes `PORT` through and defaults it to 8080. The `docker-run` target maps the host's `PORT` to port 8080 inside the container. So when you run the image this way, `PORT` picks the host port, and the container keeps listening on 8080. See [Testing and shipping](testing-and-shipping.md) for all the targets.

## Fixed server behaviour

`run` and `main` in `cmd/server/main.go` set these values. No setting overrides them.

| Behaviour | Value |
|---|---|
| Read header timeout | 5s |
| Read timeout | 10s |
| Write timeout | 10s |
| Idle timeout | 60s |
| Graceful shutdown on `SIGINT` or `SIGTERM` | waits up to 10s for in-flight requests |

If `ListenAndServe` fails, for example because the port is already taken, the error is logged as `"server stopped"` and the process exits with status 1.

## Other fixed values

These values also live in code rather than in settings:

- **Currency rates.** `fxRates` in `internal/catalog/pricing.go` holds fixed rates per 1 RON: RON 1, EUR 0.2012, HUF 79.45. Rounding per currency comes from `fractionDigits` in the same file. See [Business rules](business-rules.md).
- **Catalog data.** The catalog is a read-only in-memory `Store`. It is built at startup from the three seed products in `SeedProducts` (`internal/catalog/store.go`). There is no database, so there is no connection to configure.
- **Access logging.** Every request writes one JSON line to stdout, holding only method, path, status and duration. Query strings and client IPs are left out on purpose, to keep the data minimal. The facts record no setting that changes the log destination or level. See [Architecture and request flow](architecture.md).
