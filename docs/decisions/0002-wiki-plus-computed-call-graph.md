# 0002: Wiki plus computed call graph

Status: approved 2026-11-01.

**Decision.** Facts are stored in a wiki, with evidence and links to the declarations, routes and files they depend on. The call graph is computed from the code on every run, and route fingerprints are derived from it. No knowledge graph is stored.

**Why.** Stored facts let a re-run update only what a change touches. A computed graph follows a change through calls, so an edit in a helper marks every route that reaches it. Fingerprints and declaration hashes are both needed: fingerprints follow calls, and declarations catch what calls do not reach. Measured.

**Cost.** A static call graph misses calls through interfaces and function values. Stored requests are the backstop. A stored knowledge graph would be reopened only for cross-repo questions.
