// Package routing holds the handlers of the tracker.
package routing

import (
	"encoding/json"
	"net/http"

	"example.com/parcels/ledger"
	_ "example.com/parcels/rollcall"
)

// Parcel is the reply to a tracking request.
type Parcel struct {
	Barcode string `json:"barcode"`
	Status  string `json:"status"`
}

// Register adds the tracker's route to the mux the program serves.
func Register(switchboard *http.ServeMux) {
	switchboard.HandleFunc("GET /parcels/{barcode}", trace)
}

func trace(w http.ResponseWriter, r *http.Request) {
	status := ledger.StatusOf(r.PathValue("barcode"))
	_ = json.NewEncoder(w).Encode(Parcel{Barcode: r.PathValue("barcode"), Status: status})
}
