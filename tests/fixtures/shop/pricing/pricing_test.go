package pricing

import "testing"

func TestTotalMultipliesTheUnitPrice(t *testing.T) {
	if got := Total(450, 3); got != 1350 {
		t.Fatalf("Total(450, 3) = %d, want 1350", got)
	}
}

func TestTotalHasNoVolumeDiscount(t *testing.T) {
	if got := Total(100, 50); got != 5000 {
		t.Fatalf("Total(100, 50) = %d, want 5000", got)
	}
}
