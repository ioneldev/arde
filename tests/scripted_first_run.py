"""The arde command as a process of its own, playing a whole first run on the shop with the model's
replies scripted, so a test can run the real pipeline where only a command reaches, such as inside
the container image.

    python tests/scripted_first_run.py --repo <url or path> --json
"""

import sys
from dataclasses import replace

# Python puts this script's own folder first on the path, so the test helpers beside it import.
from end_to_end_support import a_first_run_on_the_shop

from arde.adapters.llm.fake import FakeLLM
from arde.application.ports import LLMClient, Tracer
from arde.cli import LOCAL_WIRING, run, signals_as_interrupts


def scripted(key: object, tracer: Tracer) -> LLMClient:
    return FakeLLM(a_first_run_on_the_shop())


def main() -> int:
    wiring = replace(LOCAL_WIRING, model_client=scripted)
    with signals_as_interrupts():
        return run(sys.argv[1:], wiring, out=sys.stdout, err=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
