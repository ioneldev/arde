"""What the discovery agent must know about Go to fill in a profile. It reaches a model as it is.

It holds no fact about any repo, and it changes only when ARDE's reading of Go changes, because the
prompt it goes into is hashed into the trace.
"""

DISCOVERY_GUIDANCE = """\
This repo is a Go service built on net/http.

main_package is the folder that holds func main, written as . or ./path/to/folder. It names one \
package of this module. A folder inside a module of its own cannot be named, and neither can a \
folder whose name starts with a dot.

main_file is the file in that folder that holds func main, as a path from the repo root.

port_env is the environment variable the service reads its port from.

patterns are read from the source. Each has a kind, a call and args.

call is a regular expression in RE2 syntax, matched against the name of the function being called, \
as it is written, on one line, without type arguments: mux.HandleFunc, s.router.Get, \
r.URL.Query().Get. Anchor it with ^ and $, and escape each dot.

args gives the position of each argument ARDE reads, counted from 0. Each kind has exact names, \
and any other name is refused.

- route: `path` and `handler`, and `method` for a function that takes the method as an \
argument. Calls to Handle and HandleFunc of net/http are found with no pattern, whatever the \
receiver is called. Write a pattern for a function of this service that registers routes for its \
callers, or for another router. It is read at that function's calls, and the registration inside \
it is passed over. Such a function must register its path argument alone, after a constant method \
such as "GET ", or after its method argument and a space.
- param: `name`. One pattern per way the service reads a request value, such as \
^r\\.URL\\.Query\\(\\)\\.Get$, ^r\\.PathValue$ or a local variable that holds the query, as in \
^query\\.Get$. Calls to URL.Query().Get, PathValue, FormValue, PostFormValue and Header.Get on a \
request are read with no pattern, whatever the request is called.
- env: `name`. os.Getenv and os.LookupEnv are read with no pattern. Write one for a function of \
this service that wraps them.
- error: `code` and `status`. The function that writes an error reply with a named code, with the \
position of that code and of the HTTP status. The message passed to http.Error is not a code.

A param name, env name or error code must be a string literal at the call, and anything else is \
listed as dropped. A route path may be any constant the compiler knows.

A pattern that matches nothing is reported. A route ARDE cannot read stops the run, and narrowing \
a pattern never gets past that.

ARDE reads main_package and the packages of this module it imports, so a second main in the repo \
adds nothing.
"""
