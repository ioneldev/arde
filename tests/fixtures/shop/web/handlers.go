package web

import (
	"net/http"
	"strconv"

	"example.com/shop/pricing"
)

const maxPieces = 50

func (s *server) listing(w http.ResponseWriter, _ *http.Request) {
	views := make([]ItemView, 0, len(s.items))
	for _, candidate := range s.items {
		views = append(views, ItemView{SKU: candidate.sku, Title: candidate.title, PriceCents: candidate.priceCents})
	}
	respond(w, http.StatusOK, Listing{Items: views})
}

func (s *server) quote(w http.ResponseWriter, r *http.Request) {
	entry, ok := s.find(r.PathValue("sku"))
	if !ok {
		refuse(w, "unknown_sku", http.StatusNotFound)
		return
	}
	pieces := 1
	if raw := r.URL.Query().Get("pieces"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > maxPieces {
			refuse(w, "bad_pieces", http.StatusBadRequest)
			return
		}
		pieces = parsed
	}
	respond(w, http.StatusOK, Quote{
		Stamp:       Stamp{IssuedBy: "shop"},
		SKU:         entry.sku,
		Pieces:      pieces,
		ChargeCents: pricing.Charge(entry.priceCents, pieces),
		Details:     map[string]string{"vat": "included"},
		Traits:      entry.traits,
	})
}
