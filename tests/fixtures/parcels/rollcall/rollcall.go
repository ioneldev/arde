// Package rollcall adds a route to the default mux from a folder no main names. It is served
// because the tracker serves that mux, and a blank import is the only sign of it.
package rollcall

import "net/http"

func init() {
	http.HandleFunc("GET /rollcall", answer)
}

func answer(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusNoContent)
}
