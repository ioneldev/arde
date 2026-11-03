<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Architecture and request flow

The service is a small Go HTTP server that serves a fixed product catalog. It has no database. Every reply is built from an in-memory store that is filled once, at startup.

## Code layout

| Location | What it holds |
|---|---|
| `cmd/server/main.go` | `main` and `run`: they read `PORT` (through `envOr`), build the store and the router, start the HTTP server and handle shutdown. |
| `internal/catalog/store.go` | `Store`, `NewStore`, `SeedProducts`, `Store.List`, `Store.Get` and `ErrNotFound`: the read-only catalog. |
| `internal/catalog/product.go` | `Product`, `Category` and its constants, the spec types `LaptopSpecs`, `BookSpecs` and `ApparelSpecs`, and `Product.UnitPrice` (the discounted price). |
| `internal/catalog/pricing.go` | `Currency`, `ParseCurrency`, `Convert`, `fxRates` and `fractionDigits`: currency parsing and conversion. |
| `internal/httpapi/router.go` | `NewRouter`: registers the routes on a Go `ServeMux`. |
| `internal/httpapi/handlers.go` | The `handlers` type with `handlers.index`, `handlers.product` and `handlers.healthz`, the `money` helper, and the constants `defaultQty` and `maxQty`. |
| `internal/httpapi/middleware.go` | `logRequests`, `recoverPanics` and `statusRecorder`. |
| `internal/httpapi/responses.go` | The reply types (`IndexResponse`, `ProductSummary`, `ProductResponse`, `Money`, `HealthResponse`, `ErrorResponse`, `ErrorBody`) and `writeError`. |

The split is simple. `internal/catalog` holds the data and the pricing rules. `internal/httpapi` turns HTTP requests into calls on the catalog and encodes the replies as JSON.

## The catalog store

- `run` builds a `Store` at startup from `SeedProducts`. There are three fixed products, sorted by ID: 1 is a laptop, 2 is a discounted book, and 3 is a T-shirt that is out of stock.
- Nothing changes the store after it is built. That is why handlers can read it from many requests at once without locking.
- `Store.List` returns every product in ascending ID order. `Store.Get` scans the fixed slice and returns either the product or `ErrNotFound`.
- Prices are stored as whole RON bani. Conversion to other currencies happens only when a reply is built. See [Business rules](business-rules.md).

The store's contents can be seen in GET /:

```json
{"count":3,"products":[{"id":1,"slug":"nimbus-14-laptop","name":"Nimbus 14 Laptop","category":"electronics","price":{"amount":3499.99,"currency":"RON"},"in_stock":true,"url":"/products/1"},{"id":2,"slug":"the-pragmatic-gopher","name":"The Pragmatic Gopher","category":"books","price":{"amount":71.92,"currency":"RON"},"in_stock":true,"url":"/products/2"},{"id":3,"slug":"basic-cotton-tee","name":"Basic Cotton Tee","category":"fashion","price":{"amount":59,"currency":"RON"},"in_stock":false,"url":"/products/3"}]}
```

## Routes

`NewRouter` registers three GET patterns. Each one also serves HEAD.

| Route | Handler | Notes |
|---|---|---|
| `GET /` | `handlers.index` | Registered as `GET /{$}`, so it matches only the exact root path. |
| `GET /products/{id}` | `handlers.product` | The single product, priced by currency and quantity. |
| `GET /healthz` | `handlers.healthz` | Liveness only. It always replies `{"status":"ok"}` and does not look at the store. |

When no route matches, `ServeMux` itself sends the reply. This covers an unknown path, a trailing slash, `/products/` with no id, and different letter case: all get a plain-text 404. A method other than GET or HEAD on a known path gets a 405. These replies do not use the JSON error envelope. See [Errors](errors.md).

## How a request travels

1. **Server.** The `net/http` server built in `run` accepts the connection. It uses fixed timeouts: 5s to read headers, 10s to read, 10s to write and 60s idle.
2. **`logRequests` (outer middleware).** Starts timing the request.
3. **`recoverPanics` (inner middleware).** Catches a panic in the code below it.
4. **Router.** The `ServeMux` from `NewRouter` picks the handler, or sends its own plain-text 404 or 405.
5. **Handler.** For `GET /products/{id}`, `handlers.product`:
   - checks `id`, then `currency` (through `ParseCurrency`), then `qty`. The first failure is answered with a 400 through `writeError`.
   - calls `Store.Get` only after all three checks pass. `ErrNotFound` becomes a 404 `product_not_found`. Any other error would become a 500 `internal`.
   - computes the discounted unit price with `Product.UnitPrice`, multiplies it by the quantity, and converts each amount with `money` and `Convert`.

   `handlers.index` lists `Store.List` with prices in RON. `handlers.healthz` replies directly.
6. **Back out.** If a handler panicked, `recoverPanics` logs it and answers with the 500 `internal` JSON error. Then `logRequests` writes the access-log line.

Because of the validation order, an invalid parameter is reported even for an unknown id. The rules are in [Business rules](business-rules.md), and the full parameters and replies are in the [API reference](api.md).

## What the middleware logs

- **Access log.** Every request writes one JSON line to stdout. Its message is `"request"` and it holds only `method`, `path`, `status` and `duration_ms`. Query strings and client IPs are left out on purpose, to keep collected data to a minimum. Keep it that way when you change the logger. `statusRecorder` is declared in the same file, but the facts do not describe what it does.
- **Panics.** `recoverPanics` logs a recovered panic at error level with the path, the panic value and the stack. It then sends the 500 `internal` JSON error.

With the current code, no request reaches `internal`. `Store.Get` returns only a product or `ErrNotFound`, and no handler path panics on any input. Both 500 paths are safety nets.

## Startup and shutdown

- `run` listens on all interfaces at the port from `PORT`, or 8080 when `PORT` is unset or empty. See [Configuration](configuration.md).
- On SIGINT or SIGTERM the server shuts down gracefully and waits up to 10s for in-flight requests.
- If `ListenAndServe` fails, the error is logged as `"server stopped"` and the process exits with status 1.

To build, run and test the service, see [Testing and shipping](testing-and-shipping.md).
