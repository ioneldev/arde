// Command aviary serves bird sightings over HTTP.
package main

import (
	"log"
	"net/http"
	"os"

	"example.com/aviary/birds"
)

// @title Aviary API
// @version 0.0.0
func main() {
	log.Fatal(http.ListenAndServe(":"+os.Getenv("AVIARY_PORT"), birds.Routes()))
}
