"""The generated docs name only what the repo or a recorded probe holds.

Repo text reaches the docs through the agents, so a line planted in a comment or a reply can try
to put a link or a command in front of a reader. Every URL and e-mail address a page names, and
every host it gives with a port, must appear in the repo's text or in a probe body. Every command
in a code block must be a make target of the repo, be written in its README, a Dockerfile or a CI
file, or be one of the profile's commands: a comment in code is repo text too, and no place a
person runs commands from.
"""

import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from arde.application.checks_runner import CheckContext
from arde.domain.base import one_line
from arde.domain.findings import Finding

CHECK = "docs_grounding"
# Where the run writes its pages, from the repo root.
DOCS_FOLDER = "docs"
PAGE_SUFFIX = ".md"
# Far past any text file a person maintains. A larger file is passed over, as a binary one is.
FILE_BYTES_LIMIT = 1024 * 1024
# How much of a name or a command from a page a finding shows.
SHOWN_LIMIT = 300
# A block in one of these holds data, output or source, which no shell runs. Any other block,
# one with no language among them, is read as commands, so leaving the language out hides none.
NOT_SHELL_LANGUAGES = frozenset(
    {
        *("json", "jsonc", "json5", "yaml", "yml", "toml", "ini", "xml", "html", "csv", "http"),
        *("diff", "mermaid", "text", "txt", "plaintext"),
        *("go", "python", "py", "java", "javascript", "js", "typescript", "ts", "rust", "c"),
        *("cpp", "sql", "proto", "protobuf"),
    }
)
_CI_FOLDERS = (".github/workflows/", ".circleci/")
_CI_FILES = frozenset(
    {
        ".gitlab-ci.yml",
        ".travis.yml",
        ".drone.yml",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
        "jenkinsfile",
    }
)

_URL = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"'`()\[\]{}|\\^]+")
# What ends a sentence or a Markdown mark after a URL, rather than the URL itself.
_URL_TAIL = ".,;:!?*_~"
_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_HOST_AND_PORT = re.compile(
    r"(?<![\w./@:-])"
    r"(localhost|\d{1,3}(?:\.\d{1,3}){3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z][A-Za-z0-9-]*)"
    r":(\d{1,5})(?![\w:-]|\.\w)"
)
# A word or a dotted name, as a host is found among the repo's text.
_WORD = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_PROMPT = "$ "


class DocsGrounding:
    name = CHECK

    def run(self, ctx: CheckContext) -> list[Finding]:
        pages = list(_pages_the_run_wrote(input_repo=ctx.input_repo, output_repo=ctx.output_repo))
        if not pages:
            return []
        repo = _read_repo(ctx.input_repo)
        names = _KnownNames.found_in(repo.everything, *(probe.body for probe in ctx.wiki.probes))
        commands = _known_commands(ctx, written=repo.command_sources)
        found = [
            finding
            for page, text in pages
            for finding in (
                *_ungrounded_names(page, text, names=names),
                *_ungrounded_commands(page, text, commands=commands),
            )
        ]
        return sorted(set(found), key=lambda finding: (finding.subject, finding.message))


def _known_commands(ctx: CheckContext, *, written: str) -> "_KnownCommands":
    profile = (*ctx.profile.reported_test_commands, *ctx.profile.extra_test_commands)
    return _KnownCommands(
        make_targets=frozenset(ctx.inventory.make_targets),
        written=_one_line_of(written),
        profile=frozenset(_one_line_of(command) for command in profile),
    )


@dataclass(frozen=True)
class _RepoText:
    # Every file's path and text, for names to be found in.
    everything: str
    # The README, Dockerfile and CI files, which a person reads for the commands to run.
    command_sources: str


def _read_repo(root: Path) -> _RepoText:
    everything: list[str] = []
    command_sources: list[str] = []
    for relative, text in _text_files(root):
        everything += [relative, text]
        if _is_command_source(relative):
            command_sources.append(text)
    return _RepoText(everything="\n".join(everything), command_sources="\n".join(command_sources))


def _is_command_source(relative: str) -> bool:
    name = PurePosixPath(relative).name.lower()
    return (
        name.startswith("readme")
        or name == "dockerfile"
        or name.startswith("dockerfile.")
        or name.endswith(".dockerfile")
        or name in _CI_FILES
        or relative.startswith(_CI_FOLDERS)
    )


# A page the run left as the repo had it is the repo's own text, not something the run wrote.
def _pages_the_run_wrote(*, input_repo: Path, output_repo: Path) -> Iterator[tuple[str, str]]:
    for relative, text in _text_files(output_repo / DOCS_FOLDER):
        if not relative.endswith(PAGE_SUFFIX):
            continue
        page = f"{DOCS_FOLDER}/{relative}"
        before = input_repo / page
        if before.is_file() and not before.is_symlink() and _read_text(before) == text:
            continue
        yield page, text


# Links are passed over: the copy step keeps each one inside the repo, and a file read through one
# is read again under its own name.
def _text_files(root: Path) -> Iterator[tuple[str, str]]:
    for folder, subfolders, files in os.walk(root):
        subfolders[:] = sorted(name for name in subfolders if name != ".git")
        for name in sorted(files):
            path = Path(folder) / name
            text = None if path.is_symlink() else _read_text(path)
            if text is not None:
                yield path.relative_to(root).as_posix(), text


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > FILE_BYTES_LIMIT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data else data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class _KnownNames:
    urls: frozenset[str]
    emails: frozenset[str]
    words: frozenset[str]

    @classmethod
    def found_in(cls, *texts: str) -> "_KnownNames":
        text = "\n".join(texts)
        return cls(
            urls=frozenset(_urls(text)),
            emails=frozenset(email.lower() for email in _EMAIL.findall(text)),
            words=frozenset(_WORD.findall(text.lower())),
        )


def _urls(text: str) -> Iterator[str]:
    for match in _URL.finditer(text):
        yield match.group().rstrip(_URL_TAIL)


def _ungrounded_names(page: str, text: str, *, names: _KnownNames) -> Iterator[Finding]:
    for url in _urls(text):
        if url not in names.urls:
            yield _unknown_name(page, "URL", url)
    for email in _EMAIL.findall(text):
        if email.lower() not in names.emails:
            yield _unknown_name(page, "e-mail address", email)
    for host, port in _HOST_AND_PORT.findall(text):
        if host.lower() not in names.words:
            yield _unknown_name(page, "host", f"{host}:{port}")


def _unknown_name(page: str, kind: str, name: str) -> Finding:
    shown = one_line(name, SHOWN_LIMIT)
    return Finding(
        check=CHECK,
        owner="docs",
        subject=f"{page}: {shown}",
        message=(
            f"{page} names the {kind} {shown}, which appears nowhere in the repo or in a recorded "
            "probe body."
        ),
        next_action=(
            f"Remove the {kind} from the section, or use one the repo or a probe holds as "
            "written, then run again."
        ),
    )


@dataclass(frozen=True)
class _KnownCommands:
    make_targets: frozenset[str]
    # The README, Dockerfile and CI files as one line, with every run of spaces made one.
    written: str
    profile: frozenset[str]

    def holds(self, command: str) -> bool:
        return (
            self._is_make_target(command)
            or command in self.profile
            or re.search(rf"(?<![\w-]){re.escape(command)}(?![\w-])", self.written) is not None
        )

    # Plain make runs the first target, so it is grounded when the repo has one.
    def _is_make_target(self, command: str) -> bool:
        program, *targets = command.split()
        known = all(target in self.make_targets for target in targets)
        return program == "make" and bool(self.make_targets) and known


def _ungrounded_commands(page: str, text: str, *, commands: _KnownCommands) -> Iterator[Finding]:
    for command in _commands(text):
        if not commands.holds(command):
            shown = one_line(command, SHOWN_LIMIT)
            yield Finding(
                check=CHECK,
                owner="docs",
                subject=f"{page}: {shown}",
                message=(
                    f"{page} gives the command {shown}, which is not a make target of the repo, "
                    "is not written in its README, a Dockerfile or a CI file, and is not one of "
                    "the profile's commands."
                ),
                next_action=(
                    "Remove the command, or give one from those places as it is written there, "
                    "then run again."
                ),
            )


@dataclass(frozen=True)
class _Block:
    language: str
    lines: tuple[str, ...]


def _commands(text: str) -> Iterator[str]:
    """Each command in the page's code blocks, with every run of spaces made one.

    In a block with a prompt, the lines after one are the commands and the rest is their output.
    """
    for block in _code_blocks(text):
        if block.language in NOT_SHELL_LANGUAGES:
            continue
        lines = [line.strip() for line in _joined(block.lines)]
        prompted = [line.removeprefix(_PROMPT) for line in lines if line.startswith(_PROMPT)]
        written = prompted or [line for line in lines if line and not line.startswith("#")]
        yield from (" ".join(line.split()) for line in written)


def _code_blocks(text: str) -> Iterator[_Block]:
    lines = iter(text.splitlines())
    for line in lines:
        opening = _FENCE.match(line)
        if opening is None:
            continue
        fence, info = opening.groups()
        closing = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        body = _until(lines, closing)
        language = info.split()[0].lower() if info.split() else ""
        yield _Block(language=language, lines=tuple(body))


def _until(lines: Iterator[str], closing: re.Pattern[str]) -> list[str]:
    body: list[str] = []
    for line in lines:
        if closing.match(line):
            break
        body.append(line)
    return body


# A line that ends in a backslash goes on in the next one, as a shell reads it.
def _joined(lines: Iterable[str]) -> Iterator[str]:
    pending = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
        else:
            yield pending + line
            pending = ""
    if pending:
        yield pending


def _one_line_of(text: str) -> str:
    return " ".join(re.sub(r"\\\r?\n", " ", text).split())
