"""The generated spec is a valid OpenAPI 3.1 document, as openapi-spec-validator reads one.

Code writes every annotation and the spec tool writes the spec, so an invalid one is a bug no
agent can repair, and code owns the finding.
"""

from openapi_spec_validator import OpenAPIV31SpecValidator

from arde.application.checks_runner import CheckContext
from arde.checks.spec_operations import read_spec
from arde.domain.base import visible_text
from arde.domain.findings import Finding

CHECK = "spec_valid"
# The validator's message quotes the part of the schema that failed, which can run long.
_SHOWN_LIMIT = 1_000
_VERSION = "3.1"


class SpecValid:
    name = CHECK

    def run(self, ctx: CheckContext) -> list[Finding]:
        spec = read_spec(ctx.spec_path)
        version = str(spec.get("openapi", ""))
        if not version.startswith(f"{_VERSION}."):
            return [
                self._finding(ctx, f"it declares OpenAPI {version or 'no version'}, not {_VERSION}")
            ]
        first = next(iter(OpenAPIV31SpecValidator(spec).iter_errors()), None)
        if first is None:
            return []
        return [self._finding(ctx, str(first.message))]

    def _finding(self, ctx: CheckContext, problem: str) -> Finding:
        shown = visible_text(problem)[:_SHOWN_LIMIT]
        subject = ctx.spec_path.relative_to(ctx.output_repo).as_posix()
        return Finding(
            check=self.name,
            owner="code",
            subject=subject,
            message=(
                f"The generated spec {subject} is not a valid OpenAPI {_VERSION} document: {shown}"
            ),
            next_action=(
                "This is a bug in ARDE's annotations or in the spec tool. Report it with the "
                "spec and this message."
            ),
        )
