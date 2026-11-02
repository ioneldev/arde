package web

import (
	"encoding/json"
	"net/http"
)

// ItemView is one line of the price list.
type ItemView struct {
	SKU        string `json:"sku"`
	Title      string `json:"title"`
	PriceCents int64  `json:"price_cents"`
}

// Listing is the reply to the front page.
type Listing struct {
	Items []ItemView `json:"items"`
}

// Stamp says who produced a reply. Quote embeds it, so its field is promoted into the JSON object.
type Stamp struct {
	IssuedBy string `json:"issued_by"`
}

// Quote is the reply to a price request.
type Quote struct {
	Stamp
	SKU         string `json:"sku"`
	Pieces      int    `json:"pieces"`
	ChargeCents int64  `json:"charge_cents"`
	Note        string `json:"note,omitempty"`
	Details     any    `json:"details"`
	Internal    string `json:"-"`
	cacheKey    string
}

func respond(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
