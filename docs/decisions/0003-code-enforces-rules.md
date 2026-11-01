# 0003: Code enforces the rules

Status: approved 2026-11-01.

**Decision.** Code checks every rule: AST comparison, an allow-list of added lines, inventory before and after, live responses, and `go build`, `go vet` and `go test`. The LLM judge only tests usability. The LLM never writes a comment or a spec line.

**Why.** A prompt cannot guarantee a rule. No single check is enough either: `go test` passed on two real behaviour changes, the AST check caught both, and before and after responses caught only one. Measured.

**Cost.** More code, and each check needs a test proving it fires, using the measured changes as fixtures. The AST check cannot see directive comments, so an allow-list also limits what the agent may add.
