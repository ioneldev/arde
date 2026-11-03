package httpapi

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"

	"catalog/internal/catalog"
)

const (
	defaultQty = 1
	maxQty     = 10
)

type handlers struct {
	store *catalog.Store
}

func money(bani int64, c catalog.Currency) Money {
	return Money{Amount: catalog.Convert(bani, c), Currency: string(c)}
}

// @Summary Lists every product in the catalog, ordered by ID, with its discounted unit price in RON, whether it is in stock and its URL. Registered as "GET /{$}", so it matches only the exact root path; query params are ignored. HEAD is also served by the GET pattern.
// @Produce json
// @Success 200 {object} IndexResponse "The catalog listing: count and an array of product summaries."
// @Router / [get]
func (h *handlers) index(w http.ResponseWriter, _ *http.Request) {
	products := h.store.List()
	resp := IndexResponse{
		Count:    len(products),
		Products: make([]ProductSummary, 0, len(products)),
	}
	for _, p := range products {
		resp.Products = append(resp.Products, ProductSummary{
			ID:       p.ID,
			Slug:     p.Slug,
			Name:     p.Name,
			Category: string(p.Category),
			Price:    money(p.UnitPrice(), catalog.RON),
			InStock:  p.Stock > 0,
			URL:      fmt.Sprintf("/products/%d", p.ID),
		})
	}
	writeJSON(w, http.StatusOK, resp)
}

// @Summary Returns one product with its unit price, optional list price and discount, the requested quantity and the total, priced in the requested currency, plus stock, availability for that quantity, rating and category-specific specs. Validation runs in order id, currency, qty, and only then the product lookup, so an invalid param is reported even for an unknown id.
// @Produce json
// @Param id path int true "Product ID, parsed with strconv.Atoi; must be an integer >= 1. A leading plus sign or leading zeros are accepted ('+1', '01' and '%31' all mean 1). Otherwise invalid_id." minimum(1)
// @Param currency query string false "Currency of all prices in the reply. Matched case-insensitively after trimming surrounding whitespace ('eur', ' huf ' are accepted). Missing or empty means RON. When repeated, the first value is used. Any other value gives invalid_currency." Enums(RON, EUR, HUF) default(RON)
// @Param qty query int false "Quantity used for total and available, parsed with strconv.Atoi (no whitespace trimming, no decimals); must be 1..10. Missing or empty means 1. When repeated, the first value is used. Otherwise invalid_qty." minimum(1) maximum(10) default(1)
// @Success 200 {object} ProductResponse "The product priced in the requested currency for the requested quantity. Out-of-stock products are still returned, with available false."
// @Failure 400 {object} ErrorResponse "An invalid id, currency or qty; the first failing check in that order is reported."
// @Failure 404 {object} ErrorResponse "No product has this id; the message is 'product <id> does not exist'."
// @Failure 500 {object} ErrorResponse "Declared for an unexpected store error or a recovered panic; no request reaches it with the current in-memory store."
// @Router /products/{id} [get]
func (h *handlers) product(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil || id < 1 {
		writeError(w, http.StatusBadRequest, "invalid_id", "id must be a positive integer")
		return
	}

	query := r.URL.Query()

	currency := catalog.RON
	if raw := query.Get("currency"); raw != "" {
		if currency, err = catalog.ParseCurrency(raw); err != nil {
			writeError(w, http.StatusBadRequest, "invalid_currency", "currency must be one of RON, EUR, HUF")
			return
		}
	}

	qty := defaultQty
	if raw := query.Get("qty"); raw != "" {
		if qty, err = strconv.Atoi(raw); err != nil || qty < 1 || qty > maxQty {
			writeError(w, http.StatusBadRequest, "invalid_qty", fmt.Sprintf("qty must be an integer between 1 and %d", maxQty))
			return
		}
	}

	p, err := h.store.Get(id)
	if errors.Is(err, catalog.ErrNotFound) {
		writeError(w, http.StatusNotFound, "product_not_found", fmt.Sprintf("product %d does not exist", id))
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal", "internal server error")
		return
	}

	unit := p.UnitPrice()
	resp := ProductResponse{
		ID:          p.ID,
		Slug:        p.Slug,
		Name:        p.Name,
		Description: p.Description,
		Category:    string(p.Category),
		UnitPrice:   money(unit, currency),
		Quantity:    qty,
		Total:       money(unit*int64(qty), currency),
		Stock:       p.Stock,
		Available:   p.Stock >= qty,
		Rating:      p.Rating,
		Specs:       p.Specs,
	}
	if p.DiscountPercent > 0 {
		list := money(p.PriceMinor, currency)
		resp.ListPrice = &list
		resp.DiscountPercent = p.DiscountPercent
	}
	writeJSON(w, http.StatusOK, resp)
}

// @Summary Liveness check. Always replies 200 with {"status":"ok"}; it does not inspect the store. HEAD /healthz also replies 200 with the JSON content type and no body.
// @Produce json
// @Success 200 {object} HealthResponse "The service is up; status is 'ok'."
// @Router /healthz [get]
func (h *handlers) healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, HealthResponse{Status: "ok"})
}
