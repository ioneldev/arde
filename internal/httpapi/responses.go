package httpapi

import (
	"encoding/json"
	"net/http"
)

type Money struct {
	// Amount as a JSON number in the given currency (JSON amount), rounded half away from zero to 2 decimals for RON and EUR and to whole units for HUF. Trailing zeros are dropped (59, 704.2).
	Amount float64 `json:"amount"`
	// Upper-case ISO currency code of the amount (JSON currency): "RON", "EUR" or "HUF".
	Currency string `json:"currency"`
}

type ProductSummary struct {
	// Numeric product ID (JSON id); use it with GET /products/{id}.
	ID int `json:"id"`
	// URL-friendly product identifier (JSON slug).
	Slug string `json:"slug"`
	// Display name of the product (JSON name).
	Name string `json:"name"`
	// Product category (JSON category): "electronics", "books" or "fashion".
	Category string `json:"category"`
	// Discounted price of one unit, always in RON (JSON price); the index takes no currency param. E.g. the 20%-off book lists 71.92 RON.
	Price Money `json:"price"`
	// True when the product has at least one unit in stock (JSON in_stock).
	InStock bool `json:"in_stock"`
	// Relative path of the product detail (JSON url), "/products/<id>".
	URL string `json:"url"`
}

type IndexResponse struct {
	// Number of products in the listing (JSON count), equal to the length of products.
	Count int `json:"count"`
	// Every catalog product as a summary, ordered by ascending ID (JSON products). Always an array, never null.
	Products []ProductSummary `json:"products"`
}

type ProductResponse struct {
	// Numeric product ID (JSON id), the same value as the {id} path param after parsing.
	ID int `json:"id"`
	// URL-friendly product identifier (JSON slug), e.g. "nimbus-14-laptop". Informational only; the route takes the numeric id.
	Slug string `json:"slug"`
	// Display name of the product (JSON name).
	Name string `json:"name"`
	// Short free-text product description (JSON description). Only in the product detail, not in the index summary.
	Description string `json:"description"`
	// Product category (JSON category): one of "electronics", "books" or "fashion". It decides the shape of specs.
	Category string `json:"category"`
	// Price of one unit after any discount (JSON unit_price), converted to the requested currency. The discounted price is computed in RON bani with integer division (rounded down), then converted and rounded half away from zero.
	UnitPrice Money `json:"unit_price"`
	// Undiscounted price of one unit in the requested currency (JSON list_price). Present only when the product has a discount above 0; omitted otherwise.
	ListPrice *Money `json:"list_price,omitempty" binding:"optional"`
	// Promotion discount as a whole percentage (JSON discount_percent), e.g. 20. Present only when the product is discounted; omitted when there is no discount.
	DiscountPercent int `json:"discount_percent,omitempty" binding:"optional"`
	// The quantity the reply is priced for (JSON quantity): the qty query param, or 1 when it is missing or empty.
	Quantity int `json:"quantity"`
	// Price of quantity units (JSON total). Computed as the discounted RON-bani unit price times quantity, then converted once and rounded, so it can differ from unit_price.amount * quantity (e.g. laptop, EUR, qty 3: total 2112.59 while 704.2 * 3 = 2112.6).
	Total Money `json:"total"`
	// Units in stock (JSON stock); 0 for an out-of-stock product, which is still returned.
	Stock int `json:"stock"`
	// Whether stock covers the requested quantity (JSON available): stock >= quantity. A request for more than the stock still succeeds with available false (e.g. book with stock 3, qty 4).
	Available bool `json:"available"`
	// Average customer rating as a decimal number (JSON rating), e.g. 4.6.
	Rating float64 `json:"rating"`
	// Category-specific attributes (JSON specs): a laptop spec object for electronics, a book spec object for books, an apparel spec object for fashion.
	Specs any `json:"specs" oneOf:"catalog.LaptopSpecs,catalog.BookSpecs,catalog.ApparelSpecs"`
}

type HealthResponse struct {
	// Always "ok" (JSON status).
	Status string `json:"status"`
}

type ErrorResponse struct {
	// The error envelope (JSON error) that every handler error reply carries, holding code and message. Router-level 404 and 405 replies are plain text and do not use it.
	Error ErrorBody `json:"error"`
}

type ErrorBody struct {
	// Stable machine-readable error code (JSON code): invalid_id, invalid_currency, invalid_qty, product_not_found or internal.
	Code string `json:"code"`
	// Human-readable explanation (JSON message), e.g. "qty must be an integer between 1 and 10" or "product 99 does not exist". Not meant for matching; use code.
	Message string `json:"message"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	// The status line is already sent; an encoding error cannot be reported to the client.
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, ErrorResponse{Error: ErrorBody{Code: code, Message: message}})
}
