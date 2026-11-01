# 0001: The orchestrator is code

Status: approved 2026-11-01.

**Decision.** Python code runs the sequence of steps. Agents run inside it only where the next action depends on what they find. Each agent is one generic loop with a system prompt, a tool list, a submit tool with a validator, and limits.

**Why.** The steps are predictable, and a rule like "never commit before checks pass" cannot be left to a prompt. A hand-written loop of about 40 lines keeps the loop, tools, trace and stop conditions visible, and gives agents no shell or write tools.

**Cost.** We write and test the loop ourselves instead of using a framework such as LangGraph or the Claude Agent SDK. Changing the order of steps means changing code.
