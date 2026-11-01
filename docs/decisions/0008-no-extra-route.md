# 0008: No extra route

Status: approved 2026-11-01.

**Decision.** The brief allows one extra route in the target service. ARDE does not add one.

**Why.** Then the diff contains no new executable code, which is an easy claim for a reviewer to check. Any behaviour change to the target service is an anti-goal.

**Cost.** The service does not serve its own spec. The spec is a generated file only.
