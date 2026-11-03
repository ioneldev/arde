"""The tools agents get, behind the AgentToolbox port, so the application names no adapter."""

from pathlib import Path

from arde.adapters.http_tool import http_probe_tool
from arde.adapters.repo_tools import repo_tools
from arde.application.ports import AgentToolbox, ServiceHandle, Tool
from arde.application.probe_log import ProbeLog


class LocalToolbox(AgentToolbox):
    def repo_tools(self, root: Path) -> tuple[Tool, ...]:
        return repo_tools(root)

    def http_probe_tool(self, service: ServiceHandle, log: ProbeLog) -> Tool:
        return http_probe_tool(service, log)


# The return type is the static proof that LocalToolbox satisfies the port.
def local_toolbox() -> AgentToolbox:
    return LocalToolbox()
