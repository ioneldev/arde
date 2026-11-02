// Package stacks serves the shelves.
package stacks

import "net/http"

const shelfPath = "/stacks/shelves"

// Mux holds the routes under /stacks/, written in full.
func Mux() *http.ServeMux {
	counter := http.NewServeMux()
	counter.HandleFunc("GET "+shelfPath, shelves)
	counter.HandleFunc("GET "+shelfPath+"/{id}", shelf)
	return counter
}

func shelves(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
}

func shelf(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusOK)
}
