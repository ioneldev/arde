package web

import "net/http"

type problem struct {
	Code string `json:"code"`
}

// refuse answers with a JSON body that names what went wrong.
func refuse(w http.ResponseWriter, code string, status int) {
	respond(w, status, problem{Code: code})
}
