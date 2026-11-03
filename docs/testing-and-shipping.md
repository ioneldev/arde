<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Testing and shipping

This page covers how to build, run, test and package the service, and what the tests check.

## Make targets

The `Makefile` holds every everyday task:

| Target | What it does |
|---|---|
| `run` | Runs the server with `go run ./cmd/server`. It passes `PORT` through, and `PORT` defaults to 8080. |
| `build` | Builds a static binary (`CGO_ENABLED=0`) at `bin/catalog`. |
| `test` | Runs `go test ./...`. |
| `vet` | Runs `go vet ./...`. |
| `check` | Runs `vet`, then `test`. |
| `docker-build` | Builds the Docker image `catalog-poc:local`. |
| `docker-run` | Runs that image and maps the host `PORT` to port 8080 in the container. |
| `clean` | Deletes `bin`. |

Run `check` before you push. It runs the same two commands, vet first:

```
go vet ./...
go test ./...
```

## Running locally

The `run` target starts the server from `cmd/server`. The server listens on all interfaces, on the port in `PORT`. If `PORT` is unset or empty, it uses 8080. [Configuration](configuration.md) describes this setting, plus the server's fixed timeouts and graceful shutdown.

The catalog lives in memory and is built at startup from three fixed seed products. The service has no database, so you need nothing else to run it. Each request writes one JSON access-log line to stdout.

To check that the server is up, call `GET /healthz`. It always replies 200 with `{"status":"ok"}`. It does not look at the store.

## Tests

The tests use only the Go standard library. They live in two packages.

### `internal/catalog` (`pricing_test.go`)

- `TestUnitPriceRoundsDiscountDown`: the discounted unit price is rounded down.
- `TestUnitPriceWithoutDiscount`: the unit price of a product with no discount.
- `TestConvert`: currency conversion rounds correctly for each currency.
- `TestParseCurrency`: currency parsing ignores letter case and surrounding whitespace.

[Business rules](business-rules.md) explains the pricing and rounding rules these tests protect.

### `internal/httpapi` (`handlers_test.go`)

These tests run the full router on `httptest`, with the seed catalog and a logger that discards its output. Two helpers support them: `newTestServer` and `getJSON`.

- `TestIndex`: the catalog listing at `GET /`.
- `TestProduct`: priced product replies from `GET /products/{id}`.
- `TestProductErrors`: every error code a handler returns, except `internal`.
- `TestRouting`: the router's own 404 and 405 replies, including a 405 for `POST /`. These replies are plain text, not the JSON error envelope (see [Errors](errors.md)).

### Gaps in coverage

- No test triggers the `internal` error code, and no request can trigger it today. The handler sends it only when `Store.Get` returns an error other than `ErrNotFound`, and the in-memory store never does. `recoverPanics` also sends it after a panic, but no handler path panics.
- The facts list no tests for the access log, the `PORT` setting, the server timeouts or shutdown. Treat changes there as untested.

## Packaging and shipping

The service ships as a Docker image. `docker-build` tags it `catalog-poc:local`. The image sets `PORT=8080` and exposes port 8080. `docker-run` starts the image and maps the host `PORT` to port 8080 in the container.

The facts do not cover the Dockerfile's build stages, any CI pipeline, the image registry or how releases are deployed. Ask the team that owns the service before you rely on any of these.
