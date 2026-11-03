// Command shop serves a small price list over HTTP.
package main

import (
	"log"
	"net/http"
	"os"

	"example.com/shop/web"
)

func main() {
	port := setting("SHOP_PORT", "8080")
	if os.Getenv("SHOP_VERBOSE") != "" {
		log.Printf("listening on :%s", port)
	}
	log.Fatal(http.ListenAndServe(":"+port, web.NewHandler()))
}
