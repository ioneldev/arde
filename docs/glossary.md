# Glossary

Terms as ARDE uses them.

**Allow-list**: The rule that a run changed only what ARDE writes. Each line the output gained is one the render reports writing, or a line of the input with only `oneOf` or `binding:"optional"` added or taken out. Each line it lost is an annotation, a line the last run's manifest lists, or an empty comment line.

**Anchor**: The one function a route's docs belong on, held in `Route.handler_id`: the handler when every call around it only wraps it, or else the outermost factory of the service that makes it. A route with no single such function has none, and gets facts and docs but no comment block. Neither has a route whose different handler resolves to the same function as another route's, and `Route.shares_anchor_with` names those routes.

**Attempt**: One output copy and the checks run on it. Attempt 0 is the first, and attempt n follows repair round n. Its service logs end in `-repair-n`.

**Baseline**: The repo's own build, vet and tests on the input copy, before any edit or paid call. A red baseline stops the run.

**Boundary case**: One value a live check sends for a param, with whether the spec says the service takes it.

**Closed schema**: A schema that refuses every key it doesn't list, at every depth. Plain validation accepts an undocumented field.

**Coverage**: Every route, param, error code and response field of the inventory has a fact. Explore cannot finish without it.

**Directive**: A comment that a compiler or a tool reads, such as `//go:build`, `//go:embed`, a cgo preamble or an example's `// Output:` line. Changing one can change behaviour, so the comment-only rule never takes one out of the comparison: it takes out only ordinary comment lines, a whole line that starts `// ` and is none of these, and the tags ARDE adds. A line like `//go:build` has no space after the slashes, which is how most directives are written. The Go tools also read `// +build`, `// #cgo` and `// Output:` with a space, so those are directives too.

**Evidence**: What a fact rests on. It is a code quote that holds code, not only a comment or one string literal, from a source file the language adapter reads, or a citation of a probe made in the same run.

**Fact**: One checked statement about the target service, with an id, evidence (a code quote or a citation of a recorded request and response) and links to the declarations, routes or files it depends on. Facts are what the docs agent writes from.

**Finding**: The result of a failed check. It names the check, who owns the repair (discovery, explore, docs or code), the subject, a message and the next action.

**Fingerprint**: A hash of every function a route's handler names, its roots in `Route.roots`, and of everything those reach in the call graph. The roots are the factory, the middleware and the handler alike. It changes when any of them changes, so an edit deep in a helper flags every route that uses it. It also covers the constants the handler expression holds, in `Route.constants_hash`. A route with one root and no constant keeps that root's own hash.

**Inline handler**: A route whose handler is a function written in place, or another expression with no plain name to show. The inventory stores it as `(inline)`. A handler that holds a function written in place anywhere, even as an argument to a factory, has no anchor and no fingerprint, because that function has no id to hash, so the stale rule counts it as changed on every run.

**Inventory**: The routes, params, error codes, response fields, env vars and make targets that code extracts from the source using the profile's patterns. It is computed on every run and never stored, so an agent cannot invent an item that is not in it. A call that matched a pattern but gave nothing is listed in its `dropped` lines: an argument was missing, empty or not a string literal, the call lies outside `main_package`, or it registers a route in a file this build leaves out. So a missing item can be told from a call that was never found.

**Manifest**: The comment lines ARDE wrote into the repo on the last run, by file, kept in `render_manifest.yaml`. A comment above a field carries no mark that tells it from a person's, so the next render removes the lines the manifest lists before it writes again.

**Profile**: What discovery learns about how to build, run and read one repo: the main package, the port variable, the main file for `swag`, and the patterns that find routes, params and error codes. It is stored, reused, and re-validated on every run. The owner corrects a value in `profile.override.yaml`, the only source of `extra_test_commands`. Commands Discovery finds go in `reported_test_commands`, saved and never run.

**Repair round**: One pass of sending a failed check's findings to the agent that owns the fix, then running the checks again. A run gets one by default.

**Run folder**: `runs/<run id>/` in the workspace: the input and output copies, the service logs, `report.json`, and `trace/` with `trace.jsonl` and `debug.log`. A URL run commits on the branch `arde/run-<run id>`.

**Section**: One page of the docs ARDE writes into the target's `docs` folder, one per topic. Code renders the API reference; the docs agent writes the others from facts. `sections.yaml` records the facts each rests on, so a later run rewrites only sections whose facts changed.

**Snapshot**: The tree as it stood after the last successful run, including the agent's edits. The next run compares the new copy against it.

**Stale**: A fact is stale when any declaration, route or file it links to has changed. A stored request that now gets a different response is not compared yet, so it does not make a fact stale. Explore must keep, change or delete each stale fact.

**Suspected injection**: A place where an agent saw text that tried to direct it instead of describing the service. Discovery and Explore name a file and line or a probe, and the docs agent names a fact or a probe. The agent reports it and never follows it, and code records it as a trace warning and a line in the report's warnings.

**Wiki**: The stored facts for one target repo, one YAML file per topic, and `probes.yaml` for the responses its facts cite. The docs agent sees only the wiki. This is not the `docs/` folder of this repo, which is the wiki about ARDE itself.

**Workspace**: The folder ARDE keeps for one target repo under `ARDE_HOME`. It holds the profile, the wiki, `sections.yaml`, the manifest, the snapshot, the clone of a URL source and one folder per run. Deleting it makes the next run a full first run.
