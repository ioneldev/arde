// Package ledger is used by both programs, so a change here moves a route in each.
package ledger

// StatusOf says where a parcel is.
func StatusOf(barcode string) string {
	if barcode == "" {
		return "unknown"
	}
	return "in transit"
}
