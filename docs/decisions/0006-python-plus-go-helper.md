# 0006: Python plus a Go helper

Status: approved 2026-11-01.

**Decision.** The orchestrator is Python. A small Go helper, `goinspect`, does the AST work, declaration hashes, pattern matching and the call graph.

**Why.** The libraries for Go's AST and call graph exist only in Go. Four throwaway tools came to 354 lines. `golang.org/x/tools` is a dependency of the helper only, never of the target service.

**Cost.** A second language and a top-level tools directory. Tests that need the Go toolchain are marked `integration`, and the Go helper's own tests are written in Go.
