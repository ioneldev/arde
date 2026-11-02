package web

import (
	"net/http"
	"strconv"

	"example.com/shop/pricing"
)

const maxQuantity = 50

func (s *server) listing(w http.ResponseWriter, _ *http.Request) {
	views := make([]ItemView, 0, len(s.items))
	for _, candidate := range s.items {
		views = append(views, ItemView{SKU: candidate.sku, Name: candidate.name, PriceCents: candidate.priceCents})
	}
	respond(w, http.StatusOK, Listing{Items: views})
}

func (s *server) quote(w http.ResponseWriter, r *http.Request) {
	found, ok := s.find(r.PathValue("sku"))
	if !ok {
		refuse(w, "unknown_sku", http.StatusNotFound)
		return
	}
	quantity := 1
	if raw := r.URL.Query().Get("qty"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > maxQuantity {
			refuse(w, "bad_quantity", http.StatusBadRequest)
			return
		}
		quantity = parsed
	}
	respond(w, http.StatusOK, Quote{
		Stamp:      Stamp{IssuedBy: "shop"},
		SKU:        found.sku,
		Quantity:   quantity,
		TotalCents: pricing.Total(found.priceCents, quantity),
		Details:    map[string]string{"currency": "USD"},
	})
}
