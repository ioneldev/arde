.PHONY: serve unit smoke lint

serve:
	go run .

unit:
	go test ./...

smoke:
	go test -tags integration ./web/...

lint:
	gofmt -l .
	go vet ./...
