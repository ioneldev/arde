package web

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

type decodedQuote struct {
	Charge int64 `json:"charge_cents"`
}

func TestQuoteChargesThePiecesAtTheListedPrice(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/items/tea-100?pieces=3", nil)
	recorder := httptest.NewRecorder()

	NewHandler().ServeHTTP(recorder, request)

	var quote decodedQuote
	if err := json.NewDecoder(recorder.Body).Decode(&quote); err != nil {
		t.Fatal(err)
	}
	if recorder.Code != http.StatusOK || quote.Charge != 1350 {
		t.Fatalf("got status %d and charge %d, want 200 and 1350", recorder.Code, quote.Charge)
	}
}
