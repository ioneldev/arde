<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Change guide

This page covers the changes the code invites most: currencies, quantity limits, catalog data, routes, pricing, validation and logging. For each one it says what to edit, what has to change with it, and what the tests would not catch. No fact in the documentation is written as a change guide, so this page is assembled from the facts behind the other pages. When those facts leave a detail open, the page says so.

## Before and after any change

The Makefile's `check` target runs vet and then the tests. Run them yourself with:

```
go vet ./...
go test ./...
```

The tests use only the standard library. See [Testing and shipping](testing-and-shipping.md) for what they cover.

## Add or change a currency

All currency logic lives in `internal/catalog/pricing.go`:

| What | Where |
|---|---|
| The currency codes | `const RON`, `const EUR`, `const HUF` (type `Currency`) |
| Rate per 1 RON (RON 1, EUR 0.2012, HUF 79.45) | `var fxRates` |
| Decimals kept after rounding (2 for RON and EUR, 0 for HUF) | `var fractionDigits` |
| Which `currency` values are accepted | `func ParseCurrency` |

When you add a currency, give it an entry in both `fxRates` and `fractionDigits`, and make sure `ParseCurrency` accepts it. `Convert` divides bani by 100, multiplies by the rate and rounds half away from zero. The facts don't say what `Convert` does with a currency that has no rate or no decimal count. So don't leave either one out.

Also update:

- `TestConvert` and `TestParseCurrency` in `internal/catalog/pricing_test.go`.
- The list of currencies on the [API reference](api.md) and in [Business rules](business-rules.md).

`GET /` always shows prices in RON and takes no `currency` parameter. A new currency only affects `GET /products/{id}`.

## Change the quantity limits

`const defaultQty` (1) and `const maxQty` (10) in `internal/httpapi/handlers.go` control `qty`. The error message for `invalid_qty` reads "qty must be an integer between 1 and 10". The facts don't say whether that text is built from `maxQty` or written out by hand. Check it when you change the limit, or the message will contradict the rule. `qty` is not trimmed and must be a whole number. Keep that in mind if you loosen the parsing.

## Add or change products

The catalog is a read-only in-memory `Store` built by `NewStore` at startup from `SeedProducts` in `internal/catalog/store.go`. There is no database. Nothing mutates the store, and that is why it is safe for concurrent use. Keep it that way: adding writes would need locking.

When you edit seed products:

- Write prices as integers in RON bani, which the code calls `PriceMinor`. For example, 349999 means 3499.99 RON.
- `DiscountPercent` is a whole percentage. With 0, the reply leaves out `list_price` and `discount_percent`.
- The store is sorted by ID, and `GET /` lists products in ascending ID order.
- A stock of 0 is allowed. The product is still returned, with `in_stock` false in the listing.
- The `internal/httpapi` tests run against the seed catalog and check the index and priced product replies. Expect to update them when seed data changes.

## Add a product category

The categories are `CategoryElectronics`, `CategoryBooks` and `CategoryFashion` (type `Category`) in `internal/catalog/product.go`. The category decides the shape of `specs`: `LaptopSpecs`, `BookSpecs` or `ApparelSpecs`. A new category needs a new specs type in `product.go`, and the [API reference](api.md) must describe it. The facts don't say how `Product` ties a category to its specs type, so read `product.go` before you add one.

## Add or change a route

Routes are registered in `NewRouter` in `internal/httpapi/router.go`, and handlers are methods on `type handlers` in `handlers.go`. Points to keep in mind:

- The index is registered as `"GET /{$}"`, so it matches only the exact root path. Without `{$}` it would match every path.
- A `GET` pattern also serves `HEAD`. Other methods get ServeMux's plain-text 405.
- A request that matches no route gets ServeMux's plain-text `404 page not found`, not the JSON error envelope. That includes trailing slashes and different letter case. See [Errors](errors.md).
- Handler errors should go through `writeError`, so the reply carries the `ErrorResponse` envelope with `code` and `message`. Clients are told to match on `code`, so treat existing codes as stable.
- Every handler is wrapped by `logRequests` (outer) and `recoverPanics` (inner). A new route gets logging and panic recovery only if it goes through the same wrapping. See [Architecture](architecture.md).
- Add a case to `TestRouting` for the new path and its method handling.

## Change pricing

Order of computation, as set out in [Business rules](business-rules.md):

1. `Product.UnitPrice` computes the discounted unit price in bani using integer division. This rounds down: 8990 at 20% gives 7192.
2. The total in bani is that unit price times the quantity.
3. The unit price, the total and the list price are each converted and rounded separately, in `handlers.product` and `money`.

This order is why `total` can differ from `unit_price.amount * quantity`. For the laptop in EUR at qty 3, the total is 2112.59, while 704.2 × 3 = 2112.6. Computing the total from the rounded unit amount would change replies. `TestUnitPriceRoundsDiscountDown` and `TestConvert` guard steps 1 and 3.

## Change validation

`handlers.product` checks `id`, then `currency`, then `qty`. It looks the product up only after all three pass. A request with several bad values therefore reports the first failure. For example, `/products/99?qty=0` gives `invalid_qty`, not `product_not_found`. Reordering the checks changes which code clients see. The tests check every handler error code except `internal`. The facts don't say whether they also check requests with more than one bad value, so add such cases if you touch the order.

## Change logging

`logRequests` in `internal/httpapi/middleware.go` writes one JSON line per request, with only `method`, `path`, `status` and `duration_ms`. Query strings and client IPs are left out on purpose, for data minimisation. Keep it that way when you add fields.

## What can break without a failing test

Based on what the tests cover:

- **Access-log content.** The handler tests use a discarded logger, so nothing checks the log fields. A leaked query string or client IP would go unnoticed.
- **The `internal` error.** It can't be reached today: `Store.Get` returns only nil or `ErrNotFound`, and no handler panics. The tests don't cover it. If you add a store that can fail, add a test for the 500 path and for `recoverPanics`.
- **Server settings.** The timeouts (5s read header, 10s read, 10s write, 60s idle), the 10s graceful shutdown and `PORT` handling are all in `cmd/server/main.go`. The test fact lists nothing about them. If you change the default port, note that the Dockerfile sets `PORT=8080` and exposes 8080, and the `docker-run` target maps to container port 8080. See [Configuration](configuration.md).
- **`GET /healthz`.** The test fact doesn't list it among the routes checked.
- **The docs.** Nothing ties the documented currencies, limits and messages to the code. Update the [API reference](api.md), [Business rules](business-rules.md) and [Errors](errors.md) yourself.
