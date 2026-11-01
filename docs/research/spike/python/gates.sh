S=$1
export GOCACHE=$S/spike/gocache GOPATH=$S/spike/gopath
rm -rf $S/spike/m5 $S/spike/m6
cp -R $S/spike/ann $S/spike/m5; cp -R $S/spike/ann $S/spike/m6
python3 - "$S/spike/m5/internal/httpapi/handlers.go" "$S/spike/m6/internal/httpapi/middleware.go" <<'PY'
import sys
p=sys.argv[1]; s=open(p,newline='').read(); n=s.replace('p.Stock >= qty','p.Stock > qty'); assert n!=s; open(p,'w',newline='').write(n)
p=sys.argv[2]; s=open(p,newline='').read(); n=s.replace('"path", r.URL.Path,','"path", r.URL.Path,\n\t\t\t"query", r.URL.RawQuery,',1); assert n!=s; open(p,'w',newline='').write(n)
PY
cd $S/spike/baseline && go build -o $S/spike/gate_base ./cmd/server
PORT=18090 $S/spike/gate_base > $S/spike/gate_base.log 2>&1 &
BASE=$!
for i in $(seq 1 300); do curl -sf localhost:18090/healthz >/dev/null 2>&1 && break; done
row() {
  name=$1; label=$2; dir=$S/spike/$name
  cd $dir
  if go test ./... >/dev/null 2>&1; then T=pass; else T=FAIL; fi
  A=$($S/spike/astcheck/astcheck $S/spike/baseline $dir | tail -1 | sed 's/.*identical: //')
  go build -o $S/spike/gate_$name ./cmd/server
  PORT=18091 $S/spike/gate_$name > $S/spike/gate_$name.log 2>&1 &
  PID=$!
  for i in $(seq 1 300); do curl -s -o /dev/null localhost:18091/ 2>/dev/null && break; done
  D=$(python3 $S/spike/py/differential.py 18090 18091)
  kill -TERM $PID; wait $PID; EC=$?
  printf '%-42s tests=%-5s ast_same=%-6s %s\n' "$label" "$T" "$A" "$D"
}
row ann "comments only"
row t10 "comments + oneOf and binding tags"
row m1  "M1 route removed"
row m2  "M2 qty param removed"
row m3  "M3 discount dropped on product"
row m4  "M4 rounding changed in Convert"
row m5  "M5 available uses > instead of >="
row m6  "M6 access log now includes query string"
echo '--- does M6 really log the query? ---'
curl -s -o /dev/null 'localhost:18090/products/1?qty=2'
PORT=18091 $S/spike/gate_m6 > $S/spike/gate_m6b.log 2>&1 &
PID=$!
for i in $(seq 1 300); do curl -s -o /dev/null localhost:18091/ 2>/dev/null && break; done
curl -s -o /dev/null 'localhost:18091/products/1?qty=2'
kill -TERM $PID; wait $PID; EC=$?
grep -c '"query":"qty=2"' $S/spike/gate_m6b.log
echo "--- clean shutdown on SIGTERM: exit=$EC, last log: $(tail -1 $S/spike/gate_m6b.log | cut -c1-90)"
kill -TERM $BASE; wait $BASE
