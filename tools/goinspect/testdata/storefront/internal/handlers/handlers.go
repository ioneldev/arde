package handlers

import "storefront/internal/pricing"

type Handlers struct {
	rate float64
}

func (h *Handlers) listing() float64 {
	return h.total(1) + h.total(2)
}

func (h *Handlers) detail() float64 {
	return pricing.Convert(3, h.rate)
}

func (h *Handlers) ping() string {
	return "ok"
}

func (h *Handlers) total(quantity float64) float64 {
	return pricing.Convert(quantity, h.rate)
}
