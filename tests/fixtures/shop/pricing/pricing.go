// Package pricing holds the shop's one pricing rule.
package pricing

// Total is the price of quantity units. There is no volume discount: ten units cost ten times
// the price of one.
func Total(unitCents int64, quantity int) int64 {
	return unitCents * int64(quantity)
}
