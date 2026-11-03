package main

import "os"

// setting reads an environment variable and falls back when it is empty.
func setting(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
