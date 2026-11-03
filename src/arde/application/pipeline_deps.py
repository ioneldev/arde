"""What the pipeline's steps need from outside, built once by cli.py and passed down."""

from collections.abc import Mapping
from dataclasses import dataclass

from arde.application.agent_loop import AgentDeps
from arde.application.models_config import Role, RoleConfig
from arde.application.ports import (
    AgentToolbox,
    LanguageAdapter,
    RunStore,
    ServiceClient,
    ServiceRunner,
)


# A dataclass, because Pydantic cannot build a schema for a Protocol field.
@dataclass(frozen=True)
class PipelineDeps:
    adapter: LanguageAdapter
    # The model client, the tracer and the budget every agent shares.
    agents: AgentDeps
    models: Mapping[Role, RoleConfig]
    store: RunStore
    service_runner: ServiceRunner
    service_client: ServiceClient
    toolbox: AgentToolbox
