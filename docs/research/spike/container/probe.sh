echo "run count: $(( $(cat /data/runs 2>/dev/null || echo 0) + 1 ))"; echo $(( $(cat /data/runs 2>/dev/null || echo 0) + 1 )) > /data/runs
echo "user: $(id -u):$(id -g)  go: $(go version | cut -d' ' -f3)  python: $(uv python find 2>/dev/null | head -1 | sed 's/.*cpython-//' | cut -d- -f1)  swag: $(swag --version 2>&1 | head -1)"
git -C /repo ls-files --cached --others --exclude-standard > /tmp/files; G=$?
echo "git ls-files exit: $G, files: $(wc -l < /tmp/files)"
[ $G -ne 0 ] && git -C /repo status 2>&1 | head -3
mkdir -p /data/copy && cd /repo && cat /tmp/files | cpio -pdm /data/copy 2>/dev/null || tar cf - -T /tmp/files | tar xf - -C /data/copy
cd /data/copy
go build ./... && go vet ./... && go test ./... 2>&1 | tail -2
go build -o /data/svc ./cmd/server
PORT=18099 /data/svc >/data/svc.log 2>&1 & P=$!
for i in $(seq 1 300); do curl -sf localhost:18099/healthz >/dev/null 2>&1 && break; done
echo "live /healthz: $(curl -s -o /dev/null -w '%{http_code}' localhost:18099/healthz)"
kill -TERM $P; wait $P; echo "service exit: $?"
printf '// written from inside the container\n' > /repo/written_by_container.txt && echo 'write-back ok' || echo 'write-back FAILED'
