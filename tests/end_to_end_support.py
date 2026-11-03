"""What the end-to-end tests share: a git repo made from a fixture service, the scripted model
replies a first run on the shop needs, and the cli wired with the real Go adapter."""

import io
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from pipeline_support import a_prediction, git, judging
from scripted_model import calling, tool_call
from shop_facts import BAD_PIECES, LISTING, NOTHING, TEA, a_rule, covering_facts, quote
from shop_support import SHOP_INVENTORY, shop_profile

from arde.adapters.llm.fake import FakeLLM
from arde.application.ports import LanguageAdapter, LLMClient, LLMResponse, Tracer
from arde.cli import LOCAL_WIRING, Wiring, run
from arde.domain.facts import Fact, Links, ProbeRecord, RuleFact
from arde.domain.report import RunReport

NO_DISCOUNT = a_rule()
PORT_RULE = RuleFact(
    id="rule.port",
    topic="configuration",
    kind="rule",
    evidence=(quote('port := setting("SHOP_PORT", "8080")', file="main.go"),),
    links=Links(declarations=("main.go::func main",)),
    statement="The service listens on the port SHOP_PORT names, and on 8080 when it is unset.",
)
SHOP_FACTS: tuple[Fact, ...] = (*covering_facts(SHOP_INVENTORY), NO_DISCOUNT, PORT_RULE)
NO_DISCOUNT_SECTION = "Ten pieces cost ten times one piece: there is no volume discount."
PORT_SECTION = "The service reads its port from `SHOP_PORT`, and listens on 8080 when it is unset."


def a_repo_of(fixture: Path, root: Path) -> Path:
    """A copy of the fixture, committed once, as a repo the cli can read from a path or a URL."""
    shutil.copytree(fixture, root)
    git(root, "init", "--quiet")
    commit_all(root, "first")
    return root


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "--all")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", message)


def submitting_the_shop_profile() -> LLMResponse:
    profile = shop_profile().model_dump(mode="json", exclude={"extra_test_commands"})
    return calling(tool_call("submit_profile", **profile, suspected_injections=[]))


def exploring_the_shop(*facts: Fact, probes: tuple[ProbeRecord, ...]) -> list[LLMResponse]:
    return [
        calling(
            *(
                tool_call("http_probe", method=probe.method, path_and_query=probe.path_and_query)
                for probe in probes
            )
        ),
        calling(
            tool_call(
                "add_facts",
                upserts=[fact.model_dump(mode="json") for fact in facts],
                deletes=[],
                suspected_gaps=[],
            )
        ),
        calling(tool_call("finish_facts", suspected_injections=[])),
    ]


def writing_a_section(markdown: str, *fact_ids: str) -> LLMResponse:
    return calling(
        tool_call(
            "submit_section",
            markdown=markdown,
            used_fact_ids=list(fact_ids),
            suspected_injections=[],
        )
    )


def a_first_run_on_the_shop() -> list[LLMResponse]:
    return [
        submitting_the_shop_profile(),
        *exploring_the_shop(*SHOP_FACTS, probes=(TEA, NOTHING, BAD_PIECES, LISTING)),
        writing_a_section("What the shop is.", NO_DISCOUNT.id),
        writing_a_section(NO_DISCOUNT_SECTION, NO_DISCOUNT.id),
        writing_a_section(PORT_SECTION, PORT_RULE.id),
        writing_a_section("How to change the shop safely.", NO_DISCOUNT.id),
        *judging_the_shop(),
    ]


def judging_the_shop() -> list[LLMResponse]:
    """A judge that predicts the running shop right, trying each route, param and error code."""
    return judging(
        a_prediction("/", **{"items.0.sku": "tea-100", "items.0.price_cents": 450}),
        a_prediction("/items/tea-100?pieces=2", pieces=2, charge_cents=900),
        a_prediction("/items/tea-100?pieces=0", 400, code="bad_pieces"),
        a_prediction("/items/nothing", 404, code="unknown_sku"),
    )


@dataclass
class Arde:
    """The cli as the controller runs it, with the model's replies scripted."""

    adapter: LanguageAdapter
    clients: list[FakeLLM] = field(default_factory=list)

    def run(self, *argv: str, script: list[LLMResponse]) -> tuple[int, RunReport, str]:
        out, err = io.StringIO(), io.StringIO()
        code = run([*argv, "--json"], self._wiring(script), out=out, err=err)
        return code, RunReport.model_validate_json(out.getvalue()), err.getvalue()

    def _wiring(self, script: list[LLMResponse]) -> Wiring:
        def scripted(key: object, tracer: Tracer) -> LLMClient:
            client = FakeLLM(script)
            self.clients.append(client)
            return client

        return replace(LOCAL_WIRING, language_adapter=lambda _: self.adapter, model_client=scripted)
