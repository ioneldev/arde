<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Overview

## What the service does

This service is a small, read-only product catalog served over HTTP as JSON. It lists products, returns the details of one product, and prices it in one of three currencies for a quantity you choose.

The catalog is built in memory at startup. It holds three fixed seed products, sorted by ID:

| ID | Product | Category | Notable because |
|----|---------|----------|-----------------|
| 1 | laptop | `electronics` | no discount |
| 2 | book | `books` | discounted |
| 3 | tee | `fashion` | out of stock |

There is no database. Nothing changes the catalog after startup, so it is safe for concurrent use.

## What it offers

| Request | What it returns |
|---------|-----------------|
| `GET /` | Every product, ordered by ID. Each entry has its discounted unit price, always in RON, whether it is in stock, and its URL. |
| `GET /products/{id}` | One product. The reply holds its unit price, its list price and discount when it has one, the quantity asked for, the total, stock, whether stock covers that quantity, its rating and its category-specific `specs`. |
| `GET /healthz` | A liveness check. It always replies 200 with `status` set to `"ok"` and does not inspect the store. |

On `GET /products/{id}`, the optional `currency` query parameter picks `RON` (the default), `EUR` or `HUF`. The optional `qty` parameter must be from 1 to 10 and defaults to 1. The shape of `specs` depends on `category`: laptop specs for `electronics`, book specs for `books` and apparel specs for `fashion`.

## Things to know early

- **Prices are integers in RON bani.** They are converted to EUR or HUF at fixed rates and then rounded. The discount is applied first, rounding down. Because the total is converted and rounded once for the whole quantity, it can differ from the rounded unit price times the quantity. See [Business rules](business-rules.md).
- **Out-of-stock products are still returned.** Asking for more units than are in stock still succeeds, with `available` set to false.
- **Errors come in two forms.** Handler errors are JSON with an `error` object that holds a `code` and a `message`. Requests that match no route get Go's plain-text 404 or 405 instead. See [Errors](errors.md).
- **Access logs leave out some data on purpose.** Each request writes one JSON log line with only the method, path, status and duration. Query strings and client IPs are left out to keep logged data to a minimum.
- **The port comes from `PORT`.** It defaults to 8080 when unset or empty. See [Configuration](configuration.md).

## Where to read next

- [Architecture and request flow](architecture.md): how the code is laid out and how a request travels through it.
- [API reference](api.md): every endpoint, parameter, field and error code.
- [Business rules](business-rules.md): pricing, rounding, validation order and defaults.
- [Errors](errors.md): the error model.
- [Configuration](configuration.md): the settings the service reads.
- [Testing and shipping](testing-and-shipping.md): how to build, run, test and package the service.
