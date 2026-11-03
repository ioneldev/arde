<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Errors

Errors come from three places, and they do not all look the same:

| Source | When | Status | Body |
|---|---|---|---|
| Handler for `GET /products/{id}` | Bad `id`, `currency` or `qty`, or an unknown product | 400 or 404 | JSON error envelope |
| `recoverPanics` middleware, or the handler on an unexpected store error | A handler panic or a store error other than `ErrNotFound` | 500 | JSON error envelope, code `internal` (never sent today, see below) |
| Go `ServeMux` (the router built by `NewRouter`) | No route matches, or the method is not allowed | 404 or 405 | Plain text, **not** JSON |

`GET /` and `GET /healthz` declare no error responses. They reply only 200.

## The JSON error envelope

Every error reply from a handler is an `ErrorResponse`. Its only field, `error`, holds an `ErrorBody` with two fields. These types are declared in `internal/httpapi/responses.go`, next to `writeError`.

| JSON field | Go field | Meaning |
|---|---|---|
| `error.code` | `ErrorBody.Code` | A stable, machine-readable error code: `invalid_id`, `invalid_currency`, `invalid_qty`, `product_not_found` or `internal`. |
| `error.message` | `ErrorBody.Message` | A human-readable explanation. It is not meant for matching. Clients should branch on `code`. |

These replies have the content type `application/json; charset=utf-8`. For example, GET /products/99 answered 404 with:

```json
{"error":{"code":"product_not_found","message":"product 99 does not exist"}}
```

GET /products/1?qty=11 answered 400 with:

```json
{"error":{"code":"invalid_qty","message":"qty must be an integer between 1 and 10"}}
```

## Error codes

| Code | Status | Returned by | Note |
|---|---|---|---|
| `internal` |  |  | Never returned: The handler sends internal only when Store.Get returns an error other than ErrNotFound, but Store.Get, a scan of a fixed in-memory slice, returns only nil or ErrNotFound. recoverPanics also sends internal after a handler panic, and no handler path panics on any input. |
| `internal` | 500 | `GET /products/{id}` |  |
| `invalid_currency` | 400 | `GET /products/{id}` |  |
| `invalid_id` | 400 | `GET /products/{id}` |  |
| `invalid_qty` | 400 | `GET /products/{id}` |  |
| `product_not_found` | 404 | `GET /products/{id}` |  |

For each parameter's exact rules, see the [API reference](api.md) and [Business rules](business-rules.md). Some inputs that look invalid are accepted. In the `id` path segment, `+1`, `01` and `%31` all mean product 1. `currency` is trimmed and matched without regard to case. An empty `currency` or `qty` takes its default. `qty` is not trimmed, so `+2` (which decodes to " 2") and `1.5` give `invalid_qty`.

## Validation order

`GET /products/{id}` checks `id` first, then `currency`, then `qty`. It looks the product up only after all three pass. Only the first failing check is reported. So an invalid parameter is reported even for an id that does not exist, and `product_not_found` appears only when all the parameters are valid.

| Request | Reported code |
|---|---|
| GET /products/0?currency=USD | `invalid_id` |
| GET /products/99?currency=USD&qty=0 | `invalid_currency` |
| GET /products/1?currency=USD&qty=0 | `invalid_currency` |
| GET /products/99?qty=0 | `invalid_qty` |

For example, GET /products/99?qty=0 answered 400 with:

```json
{"error":{"code":"invalid_qty","message":"qty must be an integer between 1 and 10"}}
```

GET /products/abc answered 400 with:

```json
{"error":{"code":"invalid_id","message":"id must be a positive integer"}}
```

## Router replies (plain text)

The handlers never see a request that no route matches. Go `ServeMux` answers it directly with its own plain-text reply, and the JSON envelope is not used. A client that always parses error bodies as JSON will fail on these replies.

- **404**, with content type `text/plain; charset=utf-8` and the body `404 page not found`, for:
  - an unknown path, such as GET /does-not-exist
  - a trailing slash, such as GET /healthz/ or GET /products/1/
  - `/products/` with no id, as in GET /products/
  - a path in a different letter case, such as GET /Healthz
- **405** for a method other than GET or HEAD on a known path. `TestRouting` asserts this for POST /. No probe recorded the body of this reply.

## Codes that are never returned: `internal`

`internal` (500) is declared, but no request reaches it with the current code:

- The handler sends `internal` only when `Store.Get` returns an error other than `ErrNotFound`. `Store.Get` scans a fixed in-memory slice and returns only nil or `ErrNotFound`. The catalog is a read-only in-memory store with no database (see [Architecture and request flow](architecture.md)).
- `recoverPanics` also sends `internal` after a handler panic. It logs the panic at error level with the path, the panic value and the stack. No handler path panics on any input.

So the tests check every handler error code except `internal`, along with the router's 404 and 405 replies (see [Testing and shipping](testing-and-shipping.md)). If you add a store that can fail, such as one backed by a database, `internal` becomes reachable. It then needs a test of its own.
