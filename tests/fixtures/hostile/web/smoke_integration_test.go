//go:build integration

package web

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAnUnknownItemIsRefused(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/items/nothing", nil)
	recorder := httptest.NewRecorder()

	NewHandler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNotFound {
		t.Fatalf("got status %d, want 404", recorder.Code)
	}
}
