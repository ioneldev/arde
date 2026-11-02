// Command console is an operator's tool, built apart from the tracker.
package main

import (
	"log"
	"net/http"
	"os"

	"example.com/parcels/console"
)

func main() {
	log.Fatal(http.ListenAndServe(":"+os.Getenv("CONSOLE_PORT"), console.NewConsole(os.Getenv("CONSOLE_TOKEN"))))
}
