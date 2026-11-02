"""Comments that are code. The AST comparison ignores every comment, so these are checked here."""

import re

# Wider than the compiler's own list, from the directive experiment: a pattern too wide only
# makes an edit count as real, and one too narrow lets a behaviour change pass as a comment edit.
# The lines it must catch: //go:build and the other //word:word forms, //line, //export,
# // +build, /*line, // #cgo, and the // Output: lines that decide whether an example runs.
_DIRECTIVE = re.compile(
    r"^\s*(//[a-z0-9]+:[a-z0-9]|//(line|export)\s|//\s*\+build|/\*line\s|//\s*#cgo"
    r"|//\s*(unordered\s+)?output:)",
    re.IGNORECASE,
)

# The comment before this import is C source that cgo compiles, so no comment in the file is safe.
_CGO_IMPORT = re.compile(
    r"""^\s*(?:import\s+)?(?:[A-Za-z_]\w*\s+)?(?:"C"|`C`)\s*(?://.*)?$""", re.M
)


def is_directive(line: str) -> bool:
    return _DIRECTIVE.match(line) is not None


# A directive means something only for the code it sits above: the same //go:embed line moved from
# one variable to the next embeds another file. So the line is kept with the code it applies to.
def anchored_directives(source: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in source.replace("\r\n", "\n").split("\n")]
    return [
        (line, _next_code_line(lines[number + 1 :]))
        for number, line in enumerate(lines)
        if is_directive(line)
    ]


def _next_code_line(lines: list[str]) -> str:
    return next((line for line in lines if line and not line.startswith("//")), "")


def imports_cgo(source: str) -> bool:
    return _CGO_IMPORT.search(source) is not None
