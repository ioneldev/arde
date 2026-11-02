package web

import "net/http"

// router wraps the standard mux so that registering a route reads as one call.
type router struct {
	mux *http.ServeMux
}

// handle registers a full pattern, with or without a method.
func (rt router) handle(pattern string, handler http.HandlerFunc) {
	rt.mux.HandleFunc(pattern, handler)
}

// get registers a path that answers GET only.
func (rt router) get(path string, handler http.HandlerFunc) {
	rt.mux.HandleFunc("GET "+path, handler)
}
