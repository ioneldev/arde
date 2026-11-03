<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# API reference

ARDE writes this page from the service's checked facts, so it changes only when they do. It shows every endpoint with its request, its responses and the JSON body of a success, then the body of the error replies and every error code. A field is named by its path from the body's root: a dot leads into an object, `[]` into each item of an array, and `*` into each value of an object whose keys are data.

## Endpoints

### `GET /`

Lists every product in the catalog, ordered by ID, with its discounted unit price in RON, whether it is in stock and its URL. Registered as "GET /{$}", so it matches only the exact root path; query params are ignored. HEAD is also served by the GET pattern.

No parameters.

#### Responses

| Status | When | Error codes |
|---|---|---|
| 200 | The catalog listing: count and an array of product summaries. |  |

#### `200` response body

| Field | Type | Always present | Description |
|---|---|---|---|
| `count` | integer | yes | Number of products in the listing (JSON count), equal to the length of products. |
| `products` | array of objects | yes | Every catalog product as a summary, ordered by ascending ID (JSON products). Always an array, never null. |
| `products[].category` | string | yes | Product category (JSON category): "electronics", "books" or "fashion". |
| `products[].id` | integer | yes | Numeric product ID (JSON id); use it with GET /products/{id}. |
| `products[].in_stock` | boolean | yes | True when the product has at least one unit in stock (JSON in_stock). |
| `products[].name` | string | yes | Display name of the product (JSON name). |
| `products[].price` | object | yes | Discounted price of one unit, always in RON (JSON price); the index takes no currency param. E.g. the 20%-off book lists 71.92 RON. |
| `products[].price.amount` | number | yes | Amount as a JSON number in the given currency (JSON amount), rounded half away from zero to 2 decimals for RON and EUR and to whole units for HUF. Trailing zeros are dropped (59, 704.2). |
| `products[].price.currency` | string | yes | Upper-case ISO currency code of the amount (JSON currency): "RON", "EUR" or "HUF". |
| `products[].slug` | string | yes | URL-friendly product identifier (JSON slug). |
| `products[].url` | string | yes | Relative path of the product detail (JSON url), "/products/\<id>". |

#### Example `200` response

```json
{
  "count": 3,
  "products": [
    {
      "id": 1,
      "slug": "nimbus-14-laptop",
      "name": "Nimbus 14 Laptop",
      "category": "electronics",
      "price": {
        "amount": 3499.99,
        "currency": "RON"
      },
      "in_stock": true,
      "url": "/products/1"
    },
    {
      "id": 2,
      "slug": "the-pragmatic-gopher",
      "name": "The Pragmatic Gopher",
      "category": "books",
      "price": {
        "amount": 71.92,
        "currency": "RON"
      },
      "in_stock": true,
      "url": "/products/2"
    }
  ]
}
```

Arrays are cut to their first 2 items.

### `GET /healthz`

Liveness check. Always replies 200 with {"status":"ok"}; it does not inspect the store. HEAD /healthz also replies 200 with the JSON content type and no body.

No parameters.

#### Responses

| Status | When | Error codes |
|---|---|---|
| 200 | The service is up; status is "ok". |  |

#### `200` response body

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | Always "ok" (JSON status). |

#### Example `200` response

```json
{
  "status": "ok"
}
```

### `GET /products/{id}`

Returns one product with its unit price, optional list price and discount, the requested quantity and the total, priced in the requested currency, plus stock, availability for that quantity, rating and category-specific specs. Validation runs in order id, currency, qty, and only then the product lookup, so an invalid param is reported even for an unknown id.

#### Request

| Parameter | In | Type | Required | Rules | Description |
|---|---|---|---|---|---|
| `id` | path | integer | yes | at least 1 | Product ID, parsed with strconv.Atoi; must be an integer >= 1. A leading plus sign or leading zeros are accepted ("+1", "01" and "%31" all mean 1). Otherwise invalid_id. |
| `currency` | query | string | no | one of `RON`, `EUR`, `HUF`; default `RON` | Currency of all prices in the reply. Matched case-insensitively after trimming surrounding whitespace ("eur", " huf " are accepted). Missing or empty means RON. When repeated, the first value is used. Any other value gives invalid_currency. |
| `qty` | query | integer | no | 1 to 10; default `1` | Quantity used for total and available, parsed with strconv.Atoi (no whitespace trimming, no decimals); must be 1..10. Missing or empty means 1. When repeated, the first value is used. Otherwise invalid_qty. |

#### Responses

| Status | When | Error codes |
|---|---|---|
| 200 | The product priced in the requested currency for the requested quantity. Out-of-stock products are still returned, with available false. |  |
| 400 | An invalid id, currency or qty; the first failing check in that order is reported. | `invalid_id`, `invalid_currency`, `invalid_qty` |
| 404 | No product has this id; the message is "product \<id> does not exist". | `product_not_found` |
| 500 | Declared for an unexpected store error or a recovered panic; no request reaches it with the current in-memory store. | `internal` |

#### `200` response body

| Field | Type | Always present | Description |
|---|---|---|---|
| `available` | boolean | yes | Whether stock covers the requested quantity (JSON available): stock >= quantity. A request for more than the stock still succeeds with available false (e.g. book with stock 3, qty 4). |
| `category` | string | yes | Product category (JSON category): one of "electronics", "books" or "fashion". It decides the shape of specs. |
| `description` | string | yes | Short free-text product description (JSON description). Only in the product detail, not in the index summary. |
| `discount_percent` | integer | no | Promotion discount as a whole percentage (JSON discount_percent), e.g. 20. Present only when the product is discounted; omitted when there is no discount. |
| `id` | integer | yes | Numeric product ID (JSON id), the same value as the {id} path param after parsing. |
| `list_price` | object | no | Undiscounted price of one unit in the requested currency (JSON list_price). Present only when the product has a discount above 0; omitted otherwise. |
| `list_price.amount` | number | yes | Amount as a JSON number in the given currency (JSON amount), rounded half away from zero to 2 decimals for RON and EUR and to whole units for HUF. Trailing zeros are dropped (59, 704.2). |
| `list_price.currency` | string | yes | Upper-case ISO currency code of the amount (JSON currency): "RON", "EUR" or "HUF". |
| `name` | string | yes | Display name of the product (JSON name). |
| `quantity` | integer | yes | The quantity the reply is priced for (JSON quantity): the qty query param, or 1 when it is missing or empty. |
| `rating` | number | yes | Average customer rating as a decimal number (JSON rating), e.g. 4.6. |
| `slug` | string | yes | URL-friendly product identifier (JSON slug), e.g. "nimbus-14-laptop". Informational only; the route takes the numeric id. |
| `specs` | object, one of 3 shapes | yes | Category-specific attributes (JSON specs): a laptop spec object for electronics, a book spec object for books, an apparel spec object for fashion. |
| `stock` | integer | yes | Units in stock (JSON stock); 0 for an out-of-stock product, which is still returned. |
| `total` | object | yes | Price of quantity units (JSON total). Computed as the discounted RON-bani unit price times quantity, then converted once and rounded, so it can differ from unit_price.amount * quantity (e.g. laptop, EUR, qty 3: total 2112.59 while 704.2 * 3 = 2112.6). |
| `total.amount` | number | yes | Amount as a JSON number in the given currency (JSON amount), rounded half away from zero to 2 decimals for RON and EUR and to whole units for HUF. Trailing zeros are dropped (59, 704.2). |
| `total.currency` | string | yes | Upper-case ISO currency code of the amount (JSON currency): "RON", "EUR" or "HUF". |
| `unit_price` | object | yes | Price of one unit after any discount (JSON unit_price), converted to the requested currency. The discounted price is computed in RON bani with integer division (rounded down), then converted and rounded half away from zero. |
| `unit_price.amount` | number | yes | Amount as a JSON number in the given currency (JSON amount), rounded half away from zero to 2 decimals for RON and EUR and to whole units for HUF. Trailing zeros are dropped (59, 704.2). |
| `unit_price.currency` | string | yes | Upper-case ISO currency code of the amount (JSON currency): "RON", "EUR" or "HUF". |

##### `specs` as `LaptopSpecs`

| Field | Type | Always present | Description |
|---|---|---|---|
| `specs.brand` | string | yes | Laptop manufacturer brand (JSON brand), in specs of electronics products. |
| `specs.cpu` | string | yes | Free-text processor description (JSON cpu), e.g. "8-core 3.2 GHz". |
| `specs.ram_gb` | integer | yes | Memory size in gigabytes (JSON ram_gb). |
| `specs.screen_inches` | number | yes | Screen diagonal in inches (JSON screen_inches), a number that may have decimals; a whole value is encoded without a fraction (14). |
| `specs.storage_gb` | integer | yes | Storage capacity in gigabytes (JSON storage_gb). |
| `specs.warranty_months` | integer | yes | Warranty length in months (JSON warranty_months). |

##### `specs` as `BookSpecs`

| Field | Type | Always present | Description |
|---|---|---|---|
| `specs.author` | string | yes | Book author name (JSON author), in specs of books products. |
| `specs.format` | string | yes | Binding or edition format (JSON format): paperback, hardcover or ebook. |
| `specs.isbn` | string | yes | ISBN as a hyphenated string (JSON isbn), e.g. "978-0-00-000000-2". |
| `specs.language` | string | yes | Language code of the book (JSON language), e.g. "en". |
| `specs.pages` | integer | yes | Page count (JSON pages). |

##### `specs` as `ApparelSpecs`

| Field | Type | Always present | Description |
|---|---|---|---|
| `specs.color` | string | yes | Colour name (JSON color), e.g. "navy". |
| `specs.material` | string | yes | Fabric composition (JSON material), e.g. "100% cotton". |
| `specs.sizes` | array of strings | yes | Available sizes as an array of strings (JSON sizes), e.g. ["S","M","L","XL"], in specs of fashion products. |

#### Example `200` response

```json
{
  "id": 1,
  "slug": "nimbus-14-laptop",
  "name": "Nimbus 14 Laptop",
  "description": "14-inch ultralight laptop for everyday work.",
  "category": "electronics",
  "unit_price": {
    "amount": 3499.99,
    "currency": "RON"
  },
  "quantity": 1,
  "total": {
    "amount": 3499.99,
    "currency": "RON"
  },
  "stock": 12,
  "available": true,
  "rating": 4.6,
  "specs": {
    "brand": "Nimbus",
    "cpu": "8-core 3.2 GHz",
    "ram_gb": 16,
    "storage_gb": 512,
    "screen_inches": 14,
    "warranty_months": 24
  }
}
```

## Error replies

The 400, 404 and 500 responses above reply with `ErrorResponse`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `error` | object | yes | The error envelope (JSON error) that every handler error reply carries, holding code and message. Router-level 404 and 405 replies are plain text and do not use it. |
| `error.code` | string | yes | Stable machine-readable error code (JSON code): invalid_id, invalid_currency, invalid_qty, product_not_found or internal. |
| `error.message` | string | yes | Human-readable explanation (JSON message), e.g. "qty must be an integer between 1 and 10" or "product 99 does not exist". Not meant for matching; use code. |

### Error codes

| Code | Status | Returned by | Note |
|---|---|---|---|
| `internal` |  |  | Never returned: The handler sends internal only when Store.Get returns an error other than ErrNotFound, but Store.Get, a scan of a fixed in-memory slice, returns only nil or ErrNotFound. recoverPanics also sends internal after a handler panic, and no handler path panics on any input. |
| `internal` | 500 | `GET /products/{id}` |  |
| `invalid_currency` | 400 | `GET /products/{id}` |  |
| `invalid_id` | 400 | `GET /products/{id}` |  |
| `invalid_qty` | 400 | `GET /products/{id}` |  |
| `product_not_found` | 404 | `GET /products/{id}` |  |

## Notes

- The {id} path segment is percent-decoded and then parsed with strconv.Atoi: "%31", "01" and "+1" all resolve to product 1, while "-1", "0" and non-numbers give 400 invalid_id.
- Query values are form-decoded (a plus is a space, percent-encoding is decoded), and only the first value of a repeated param counts. currency is trimmed and upper-cased, so "+eur+" or "%20huf%20" are accepted; qty is not trimmed, so "+2" (decoded " 2") is invalid_qty, as are decimals like 1.5. An empty value counts as missing and takes the default.
