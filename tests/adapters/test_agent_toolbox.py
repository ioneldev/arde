"""The toolbox the cli hands the application, so agents get tools without naming an adapter."""

from pathlib import Path

from arde.adapters.agent_toolbox import local_toolbox
from arde.application.ports import ServiceHandle
from arde.application.probe_log import ProbeLog


def test_the_local_toolbox_supplies_the_repo_tools_and_the_probe(tmp_path: Path) -> None:
    toolbox = local_toolbox()
    service = ServiceHandle(base_url="http://127.0.0.1:1", port=1)

    repo_tools = toolbox.repo_tools(tmp_path)
    probe = toolbox.http_probe_tool(service, ProbeLog())

    assert [tool.definition.name for tool in repo_tools] == ["list_files", "read_file", "grep"]
    assert probe.definition.name == "http_probe"
