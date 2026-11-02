# ARDE docs

A small wiki for people and agents working on this repo. Start with [AGENTS.md](../AGENTS.md) for commands and rules, then read the pages below. Every page is linked from here, and `make check` fails on a broken link or a path that does not exist. Phase specs may name future paths, because they are written before the phase starts.

## Understand it

- [Architecture](architecture.md): the steps, the agents, the layers and the stop conditions.
- [Glossary](glossary.md): what fact, finding, inventory, profile and the other terms mean here.
- [Limitations](limitations.md): what ARDE does not do, and what is not tested yet.
- [Deviations](deviations.md): where the design breaks its own rules, and what that costs.

## Decisions

Each file gives the decision, why, and what it costs.

- [0001 The orchestrator is code](decisions/0001-orchestrator-is-code.md)
- [0002 Wiki plus computed call graph](decisions/0002-wiki-plus-computed-call-graph.md)
- [0003 Code enforces the rules](decisions/0003-code-enforces-rules.md)
- [0004 The classifier only adds work](decisions/0004-classifier-only-adds-work.md)
- [0005 Discovery is separate from explore](decisions/0005-discovery-separate-from-explore.md)
- [0006 Python plus a Go helper](decisions/0006-python-plus-go-helper.md)
- [0007 swag v2 rc6](decisions/0007-swag-v2-rc6.md)
- [0008 No extra route](decisions/0008-no-extra-route.md)
- [0009 The container is the one command](decisions/0009-container-as-the-one-command.md)
- [0010 An agent returns a value or raises](decisions/0010-agent-contract.md)
- [0011 Every model request streams](decisions/0011-stream-every-request.md)

## Phase walkthroughs

What each phase built, why, and how to test it.

- [Phase 0 walkthrough](decisions/phase-0-walkthrough.md)

## Phase specs

One per build phase: status, what it delivers, how we know it is done, and how to test it by hand.

- [Phase 0: Foundations](specs/phase-0-foundations.md)
- [Phase 1: Reading Go](specs/phase-1-reading-go.md)

## Design and plan

- [Design](superpowers/specs/2026-11-01-arde-design.md): the approved design, with the reasoning and the measured facts.
- [Implementation plan](superpowers/plans/2026-11-01-arde.md): the tasks, in order.
