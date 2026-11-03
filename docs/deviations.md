# Deviations

Where the work breaks a project rule or convention on purpose, and what that costs. The source of truth is the [Deviations section of the design](superpowers/specs/2026-11-01-arde-design.md#deviations). This page is a summary.

- **One-implementation interface.** `LanguageAdapter` has only `GoAdapter`. It was asked for, and it gives fast tests through a fake. The cost is an abstraction with one real use.
- **Large interface.** `LanguageAdapter` ends with twelve methods, more than interface segregation prefers. Nine exist today, and the other three arrive with the spec generator and the edit checks, each with its implementation. It can split once a second language shows the right seams.
- **Library in the domain.** Domain models use Pydantic. This avoids two parallel type systems, and the cost is a dependency in the inner layer.
- **Two languages.** The Go helper adds a language and a top-level tools directory, because the AST and call-graph libraries exist only in Go.
- **Pre-release tool.** `swag` v2.0.0-rc6 is the only option found that can describe the polymorphic `Specs` field. It is pinned.
- **More than comments in the target.** Struct tags on 3 fields go beyond comments. The allow-list limits them to `oneOf` and `binding`, and before and after responses confirm no effect.
- **No framework.** The agent loop is hand-written, although the brief lists frameworks, because a framework would add nothing this design needs.
- **Large image.** About 980 MB, because it holds both toolchains. That is the cost of needing nothing else on the host.
- **Paid evals.** The classifier eval costs a small amount and runs on demand, because agent behaviour cannot be covered by fast unit tests alone.
- **A fifth `goinspect` command.** The phase spec first listed four. `routes` lists every `net/http` registration through the type checker, because a regex on callee text cannot tell a missed spelling from no route. The cost is that `extract_inventory` now needs a repo that compiles, which the pipeline's baseline checks prove first.
- **Three positional arguments in two Go functions.** The rule is two. `run(args, stdout, stderr)` in `goinspect` is the usual shape of a testable Go `main`, and `parseWithoutComments` has the signature `packages.Config` asks for. A struct around either would hide a shape every Go reader knows, and the cost is two exceptions to a rule the rest of the code holds.

New deviations go here as the work introduces them, with the reason and the cost.
