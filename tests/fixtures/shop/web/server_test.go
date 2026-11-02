package web

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

type decodedQuote struct {
	Total int64 `json:"total_cents"`
}

func TestQuoteTotalsTheQuantityAtTheListedPrice(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/items/tea-100?qty=3", nil)
	recorder := httptest.NewRecorder()

	NewHandler().ServeHTTP(recorder, request)

	var quote decodedQuote
	if err := json.NewDecoder(recorder.Body).Decode(&quote); err != nil {
		t.Fatal(err)
	}
	if recorder.Code != http.StatusOK || quote.Total != 1350 {
		t.Fatalf("got status %d and total %d, want 200 and 1350", recorder.Code, quote.Total)
	}
}
