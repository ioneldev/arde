"""The probes an agent made in one run.

A fact cites a probe by its request, and code copies the response from here, so a model never
retypes one.
"""

from arde.domain.facts import ProbeRecord


class ProbeLog:
    def __init__(self) -> None:
        self._probes: dict[tuple[str, str], ProbeRecord] = {}

    # The latest record wins, because it is the response the model saw last.
    def record(self, probe: ProbeRecord) -> None:
        self._probes[(probe.method, probe.path_and_query)] = probe

    def find(self, method: str, path_and_query: str) -> ProbeRecord | None:
        return self._probes.get((method, path_and_query))
