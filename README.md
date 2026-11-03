# catalog-poc

A small product catalog HTTP service in Go (standard library only).

## Run

```sh
make run                 # listens on :8080, override with PORT=9090 make run
curl localhost:8080/
```

## Test

```sh
make check               # go vet + go test
```

## Docker

```sh
make docker-build docker-run
```

New to this service? Read [its documentation](docs/).
