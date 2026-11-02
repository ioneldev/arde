// Command library lends books. Its front mux mounts two others, and it never strips a prefix: the
// mounted muxes register whole paths, as net/http requires when nothing strips them.
package main

import (
	"log"
	"net/http"
	"os"

	"example.com/library/loans"
	"example.com/library/stacks"
)

func withAudit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		log.Printf("%s %s", r.Method, r.URL.Path)
		next.ServeHTTP(w, r)
	})
}

func main() {
	front := http.NewServeMux()
	front.Handle("/stacks/", stacks.Mux())
	front.Handle("/loans/", withAudit(loans.Mux()))
	front.HandleFunc("GET /opening-hours", hours)
	log.Fatal(http.ListenAndServe(":"+os.Getenv("LIBRARY_PORT"), front))
}

func hours(w http.ResponseWriter, _ *http.Request) {
	_, _ = w.Write([]byte("9 to 5"))
}
