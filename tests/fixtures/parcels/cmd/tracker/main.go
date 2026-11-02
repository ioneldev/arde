// Command tracker follows parcels. It is the service; console is the other program in this repo.
package main

import (
	"log"
	"net/http"
	"os"

	"example.com/parcels/routing"
)

func main() {
	routing.Register(http.DefaultServeMux)
	log.Fatal(http.ListenAndServe(":"+os.Getenv("TRACKER_PORT"), nil))
}
