# 0004: The classifier only adds work

Status: approved 2026-11-01.

**Decision.** One Haiku call labels a change and points explore at what to re-check. Whatever it flags is added to the re-check list. It can never remove an item. If code and the classifier disagree, the explore agent resolves it.

**Why.** A wrong "nothing to check" would leave stale docs without anyone noticing. A wrong "check this" costs a fraction of a cent, about $0.0001 per typical change.

**Cost.** Some runs do extra explore work. The 22 out of 22 score was on small hand-made diffs run as agents, so real commits will score lower.
