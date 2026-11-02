package pricing

import "testing"

func TestChargeMultipliesTheUnitPrice(t *testing.T) {
	if got := Charge(450, 3); got != 1350 {
		t.Fatalf("Charge(450, 3) = %d, want 1350", got)
	}
}

func TestChargeHasNoVolumeDiscount(t *testing.T) {
	if got := Charge(100, 50); got != 5000 {
		t.Fatalf("Charge(100, 50) = %d, want 5000", got)
	}
}
