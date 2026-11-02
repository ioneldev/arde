# 0010: An agent returns a value or raises

Status: approved 2026-11-02, from the Phase 0 reviews.

**Decision.** `run_agent` returns the value of an accepted submit and raises `AgentStopped` for everything else. A submit tool's `validate` returns `Accepted` or `Rejected`. Only a `pydantic.ValidationError` from a handler goes back to the model, and any other exception becomes `ToolCrashed` and stops the run.

**Why.** The first version returned a result object whose `submitted` could be empty, and acceptance was the default state of a tool outcome: forgetting `is_error` finished the agent. A caller now cannot read a value that was never accepted, and the type of that value reaches the caller through `T`. The catch-all that sent every exception to the model hid ARDE's own bugs and a full disk as model input, recorded them as corrections, and could still end as submitted.

**Cost.** Each caller handles an exception instead of branching on a field. A handler must raise `ValidationError` or return an error outcome for anything the model can fix, or the run stops. The contract types are frozen dataclasses rather than Pydantic models, because they hold callables and a value of any type.
