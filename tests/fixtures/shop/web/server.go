package web

import "net/http"

type item struct {
	sku        string
	name       string
	priceCents int64
}

type server struct {
	items []item
}

// NewHandler returns the shop's routes over a fixed in-memory price list.
func NewHandler() http.Handler {
	s := &server{items: []item{
		{sku: "tea-100", name: "Tea, 100 g", priceCents: 450},
		{sku: "mug-blue", name: "Blue mug", priceCents: 1200},
	}}
	app := router{mux: http.NewServeMux()}
	app.handle("GET /{$}", s.listing)
	app.get("/items/{sku}", s.quote)
	return app.mux
}

func (s *server) find(sku string) (item, bool) {
	for _, candidate := range s.items {
		if candidate.sku == sku {
			return candidate, true
		}
	}
	return item{}, false
}
