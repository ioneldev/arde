// Package pricing holds the shop's one pricing rule.
package pricing

// Charge is the price of that many pieces. There is no volume discount: ten pieces cost ten
// times the price of one.
func Charge(unitCents int64, pieces int) int64 {
	return unitCents * int64(pieces)
}
