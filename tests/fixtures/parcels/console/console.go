// Package console holds the routes only the operator's tool serves.
package console

import (
	"encoding/json"
	"net/http"

	"example.com/parcels/ledger"
)

// Purged is the reply to a purge.
type Purged struct {
	Removed int `json:"removed"`
}

// NewConsole registers on a mux that shares its name with the tracker's on purpose: a pattern can
// match both, and only the import graph tells them apart.
func NewConsole(token string) http.Handler {
	switchboard := http.NewServeMux()
	switchboard.HandleFunc("POST /purge", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Token") != token || r.URL.Query().Get("force") == "" {
			http.Error(w, "refused", http.StatusForbidden)
			return
		}
		_ = json.NewEncoder(w).Encode(Purged{Removed: len(ledger.StatusOf("all"))})
	})
	return switchboard
}
