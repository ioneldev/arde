package pricing

func Convert(amount, rate float64) float64 {
	return round(amount * rate)
}

func round(value float64) float64 {
	return float64(int(value*100+0.5)) / 100
}
