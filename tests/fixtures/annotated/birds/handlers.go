package birds

import (
	"encoding/json"
	"net/http"
	"strconv"
)

var sightings = map[int]Sighting{
	1: {ID: 1, Species: "wren", Details: Nest{Eggs: 4}},
	2: {ID: 2, Species: "starling", Note: "at dusk", Details: Flock{Size: 300, Heading: "west"}},
}

// sighting answers one sighting by its number.
//
// @Summary Get one sighting
// @Produce json
// @Param id path int true "Sighting number" minimum(1)
// @Success 200 {object} birds.Sighting
// @Failure 404 {object} birds.Problem
// @Router /sightings/{id} [get]
func sighting(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	found, ok := sightings[id]
	if err != nil || !ok {
		reply(w, http.StatusNotFound, Problem{Code: "no_sighting", Message: "no sighting has that number"})
		return
	}
	reply(w, http.StatusOK, found)
}

func reply(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
