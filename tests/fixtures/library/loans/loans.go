// Package loans serves borrowing.
package loans

import "net/http"

// Mux holds the routes under /loans/, written in full.
func Mux() *http.ServeMux {
	counter := http.NewServeMux()
	counter.HandleFunc("POST /loans/{isbn}", borrow)
	return counter
}

func borrow(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusCreated)
}
