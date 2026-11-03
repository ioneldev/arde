<!-- ARDE wrote this page from checked facts, and its next run replaces it. -->

# Business rules

This page describes how the service prices products, checks requests and decides availability. For every field and parameter, see the [API reference](api.md). For the error replies, see [Errors](errors.md).

## Prices are stored in RON bani

Every price is stored as a whole number of RON bani, where 100 bani make 1 RON. For example, the laptop's price is 349999 bani, which is 3499.99 RON. The service converts a price to the requested currency only when it builds a reply, and it converts each amount once.

## Currency conversion

`Convert` in `internal/catalog/pricing.go` turns bani into an amount in the target currency. It uses fixed rates from `fxRates`:

| Currency | Rate per 1 RON | Decimals after rounding (`fractionDigits`) |
|---|---|---|
| `RON` | 1 | 2 |
| `EUR` | 0.2012 | 2 |
| `HUF` | 79.45 | 0 |

The steps are:

1. Divide the bani by 100.
2. Multiply by the rate.
3. Round half away from zero: to 2 decimals for RON and EUR, and to whole units for HUF.

For example, 349999 bani is 704.2 EUR and 278074 HUF.

Each amount is a JSON number in the `amount` field of a `Money` object. The JSON drops trailing zeros, so you will see `59` rather than `59.00`, and `704.2` rather than `704.20`. The `currency` field holds the upper-case code.

None of the facts give a setting that changes these rates. They are fixed values in `fxRates`.

## Discounts

Some products have a promotion discount, given as a whole percentage. `Product.UnitPrice` computes the discounted unit price in bani:

- unit price in bani = `PriceMinor * (100 - DiscountPercent) / 100`
- This uses integer division, so the result is rounded down. For example, 8990 bani at 20% off gives 7192 bani (71.92 RON).

How a discount appears in a product reply:

- `unit_price` is the price of one unit after the discount.
- `list_price` is the price of one unit before the discount. It appears only when the discount is above 0.
- `discount_percent` is the discount, such as `20`. It appears only when the product is discounted.

Without a discount, the reply has neither `list_price` nor `discount_percent`. For example, GET /products/1 has neither.

## Order of computation and why `total` can differ

`handlers.product` computes the prices in this order:

1. It computes the discounted unit price in bani, rounding down (see above).
2. It computes the total in bani: unit price × quantity.
3. It converts the unit price, the total and the list price to the requested currency, and rounds each one on its own.

Because the total is converted from bani, it is **not** always the rounded `unit_price.amount` × `quantity`. For the laptop in EUR with a quantity of 3, the total is 2112.59, while 704.2 × 3 = 2112.6.

GET /products/1?currency=EUR&qty=3:

```json
{"id":1,"slug":"nimbus-14-laptop","name":"Nimbus 14 Laptop","description":"14-inch ultralight laptop for everyday work.","category":"electronics","unit_price":{"amount":704.2,"currency":"EUR"},"quantity":3,"total":{"amount":2112.59,"currency":"EUR"},"stock":12,"available":true,"rating":4.6,"specs":{"brand":"Nimbus","cpu":"8-core 3.2 GHz","ram_gb":16,"storage_gb":512,"screen_inches":14,"warranty_months":24}}
```

The discounted book in HUF shows the discount and the rounding to whole units together. GET /products/2?currency=HUF&qty=3:

```json
{"id":2,"slug":"the-pragmatic-gopher","name":"The Pragmatic Gopher","description":"A practical guide to writing maintainable Go services.","category":"books","unit_price":{"amount":5714,"currency":"HUF"},"list_price":{"amount":7143,"currency":"HUF"},"discount_percent":20,"quantity":3,"total":{"amount":17142,"currency":"HUF"},"stock":3,"available":true,"rating":4.8,"specs":{"author":"Jane Gopher","pages":312,"isbn":"978-0-00-000000-2","language":"en","format":"paperback"}}
```

## Index prices are always in RON

GET / lists each product's discounted unit price in the `price` field, always in RON. The index takes no `currency` parameter and ignores the query string, so GET /?currency=EUR still gives RON. For example, the book at 20% off is listed at 71.92 RON, and the tee at `59`.

## Stock and availability

- In a product reply, `available` is true when `stock` >= `quantity`.
- A request for more units than are in stock still succeeds with 200, but with `available` set to false. The same applies to an out-of-stock product: its `stock` is 0 and the reply still comes back.
- In the index, `in_stock` is true when the product has at least one unit in stock.

GET /products/2?qty=4 (the book has a stock of 3):

```json
{"id":2,"slug":"the-pragmatic-gopher","name":"The Pragmatic Gopher","description":"A practical guide to writing maintainable Go services.","category":"books","unit_price":{"amount":71.92,"currency":"RON"},"list_price":{"amount":89.9,"currency":"RON"},"discount_percent":20,"quantity":4,"total":{"amount":287.68,"currency":"RON"},"stock":3,"available":false,"rating":4.8,"specs":{"author":"Jane Gopher","pages":312,"isbn":"978-0-00-000000-2","language":"en","format":"paperback"}}
```

## Input rules for GET /products/{id}

| Input | Rule | Default | Error code |
|---|---|---|---|
| `id` (path) | Percent-decoded, then parsed with `strconv.Atoi`. Must be an integer >= 1. `+1`, `01` and `%31` all mean 1. | none | `invalid_id` |
| `currency` (query) | Surrounding whitespace is trimmed, then the value is matched without regard to case against `RON`, `EUR` and `HUF`. | `RON` when missing or empty | `invalid_currency` |
| `qty` (query) | Parsed with `strconv.Atoi`, without trimming whitespace and without decimals. Must be 1 to 10 (`defaultQty`, `maxQty`). | 1 when missing or empty | `invalid_qty` |

Edge cases:

- Query values are form-decoded, so a plus sign becomes a space. This means `currency=+eur+` is accepted. But `qty=+2` decodes to " 2", which is rejected with `invalid_qty`. `qty=1.5` is rejected too.
- When a parameter is repeated, only its first value counts. GET /products/1?currency=EUR&currency=USD prices in EUR.
- The reply's `quantity` field holds the quantity the reply was priced for.

**Validation order.** The handler checks `id` first, then `currency`, then `qty`. It looks the product up only after all three pass. Only the first failing check is reported. So GET /products/99?qty=0 reports `invalid_qty`, not `product_not_found`. An unknown but valid id gives 404 `product_not_found`.
