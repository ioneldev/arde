// Package birds serves sightings. Its swag annotations and its oneOf and binding tags are written
// by hand, in the shape ARDE renders them, so the spec can be generated without the renderer.
package birds

import "net/http"

// Routes serves every route of the service.
func Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sightings/{id}", sighting)
	return mux
}
