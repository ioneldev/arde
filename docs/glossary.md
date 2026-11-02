# Glossary

Terms as ARDE uses them.

**Directive**: A comment that a compiler or a tool reads, such as `//go:build`, `//go:embed`, a cgo preamble or an example's `// Output:` line. Changing one can change behaviour, so a change that leaves every directive as it was, with the code each applies to, is the only kind that counts as comment-only.

**Fact**: One checked statement about the target service, with an id, evidence (a code quote or a citation of a recorded request and response) and links to the declarations, routes or files it depends on. Facts are what the docs agent writes from.

**Finding**: The result of a failed check. It names the check, who owns the repair (explore, docs or code), the subject, a message and the next action.

**Inline handler**: A route whose handler is not a plain name, such as a function written in place or a call that wraps another handler. The inventory stores it as `(inline)`. It has no fingerprint, so the stale rule counts it as changed on every run.

**Inventory**: The routes, params, error codes, response fields, env vars and make targets that code extracts from the source using the profile's patterns. It is computed on every run and never stored, so an agent cannot invent an item that is not in it.

**Profile**: What discovery learns about how to build, run and read one repo: the main package, the port variable, the main file for `swag`, and the patterns that find routes, params and error codes. It is stored, reused, and re-validated on every run.

**Snapshot**: The tree as it stood after the last successful run, including the agent's edits. The next run compares the new copy against it.

**Stale**: A fact is stale when any declaration, route or file it links to has changed, or when a stored request now gets a different response. Explore must keep, change or delete each stale fact.

**Wiki**: The stored facts for one target repo, one YAML file per topic. The docs agent sees only the wiki. This is not the `docs/` folder of this repo, which is the wiki about ARDE itself.

**Workspace**: The folder ARDE keeps for one target repo under `ARDE_HOME`. It holds the profile, the wiki, the snapshot and one folder per run. Deleting it makes the next run a full first run.

**Fingerprint**: A hash of every function a route can reach in the call graph. It changes when any of them changes, so an edit deep in a helper flags every route that uses it.

**Repair round**: One pass of sending a failed check's findings to the agent that owns the fix, then running the checks again. A run gets at most two.
