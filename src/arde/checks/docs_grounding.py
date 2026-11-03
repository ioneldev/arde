"""The generated docs name only what the repo or a recorded probe holds.

Repo text reaches the docs through the agents, so a line planted in a comment or a reply can try
to put a link or a command in front of a reader. Every URL, link, e-mail address and host a page
names must appear in the repo's text or in a probe body, and a relative link must reach a file.
Every line of a code block that is not data must be a make target of the repo, be written in its
README, a Dockerfile or a CI file, or be a command the owner added to the profile: a comment in
code is repo text too, and no place a person runs commands from, and a command Discovery reports
is a model's output. Whatever cannot be read is a finding, never passed over.
"""

import html
import json
import os
import re
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import yaml

from arde.application.checks_runner import CheckContext
from arde.checks.markdown_text import CodeBlock, code_blocks, link_destinations
from arde.domain.base import one_line
from arde.domain.findings import Finding

CHECK = "docs_grounding"
# Where the run writes its pages, from the repo root.
DOCS_FOLDER = "docs"
PAGE_SUFFIX = ".md"
# Far past any text file a person maintains. A larger file of the repo is passed over, as a binary
# one is, and a larger page is a finding.
FILE_BYTES_LIMIT = 1024 * 1024
# How much of a name or a command from a page a finding shows.
SHOWN_LIMIT = 300
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
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_EMAIL = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_HOST_AND_PORT = re.compile(
    r"(?<![\w./@:-])"
    r"(localhost|\d{1,3}(?:\.\d{1,3}){3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z][A-Za-z0-9-]*)"
    r":(\d{1,5})(?![\w:-]|\.\w)"
)
_WWW_HOST = re.compile(r"(?<![\w./@:-])www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?<!\.)", re.I)
# A word or a dotted name, as a host is found among the repo's text.
_WORD = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
_PROMPT = "$ "
_MAILTO = "mailto:"


class DocsGrounding:
    name = CHECK

    def run(self, ctx: CheckContext) -> list[Finding]:
        pages = list(_pages_the_run_wrote(input_repo=ctx.input_repo, output_repo=ctx.output_repo))
        if not pages:
            return []
        repo = _read_repo(ctx.input_repo)
        names = _KnownNames.found_in(repo.everything, *(probe.body for probe in ctx.wiki.probes))
        commands = _known_commands(ctx, written=repo.command_sources)
        links = _Links(names=names, output_repo=ctx.output_repo)
        found = [
            finding
            for page in pages
            for finding in _check_page(page, links=links, commands=commands)
        ]
        return sorted(set(found), key=lambda finding: (finding.subject, finding.message))


@dataclass(frozen=True)
class _Page:
    path: str
    # None when the page cannot be read, and then problem says why.
    text: str | None
    problem: str = ""


def _check_page(page: _Page, *, links: "_Links", commands: "_KnownCommands") -> Iterator[Finding]:
    if page.text is None:
        yield _unreadable(page)
        return
    yield from _ungrounded_names(page.path, page.text, links=links)
    yield from _ungrounded_commands(page.path, page.text, commands=commands)


def _known_commands(ctx: CheckContext, *, written: str) -> "_KnownCommands":
    return _KnownCommands(
        make_targets=frozenset(ctx.inventory.make_targets),
        written=_one_line_of(written),
        owners=frozenset(_one_line_of(command) for command in ctx.profile.extra_test_commands),
    )


# The repo's text. A file that cannot be read as text is passed over here: it grounds nothing.


@dataclass(frozen=True)
class _RepoText:
    # Every file's path and text, for names to be found in.
    everything: str
    # The README, Dockerfile and CI files, which a person reads for the commands to run.
    command_sources: str


def _read_repo(root: Path) -> _RepoText:
    everything: list[str] = []
    command_sources: list[str] = []
    for relative, path in _files(root):
        text = None if path.is_symlink() else _text_of(path)
        if not isinstance(text, str):
            continue
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


# The pages. A page the run left as the repo had it is the repo's own text. Any other page that
# cannot be read is a finding, because what it holds could be anything.


def _pages_the_run_wrote(*, input_repo: Path, output_repo: Path) -> Iterator[_Page]:
    for relative, path in _files(output_repo / DOCS_FOLDER, links_to_folders=True):
        page = f"{DOCS_FOLDER}/{relative}"
        is_link = path.is_symlink()
        if not (is_link or relative.lower().endswith(PAGE_SUFFIX)):
            continue
        if _unchanged(input_repo / page, path):
            continue
        if is_link:
            yield _Page(
                path=page, text=None, problem="it is a link, which the check does not follow"
            )
            continue
        text = _text_of(path)
        if isinstance(text, _NotText):
            yield _Page(path=page, text=None, problem=text.reason)
        else:
            yield _Page(path=page, text=text)


def _unchanged(before: Path, after: Path) -> bool:
    try:
        if before.is_symlink() or after.is_symlink():
            both = before.is_symlink() and after.is_symlink()
            return both and os.readlink(before) == os.readlink(after)
        same_size = before.is_file() and before.stat().st_size == after.stat().st_size
        return same_size and before.read_bytes() == after.read_bytes()
    except OSError:
        return False


def _files(root: Path, *, links_to_folders: bool = False) -> Iterator[tuple[str, Path]]:
    """Every file under root, a link as itself. Sorted, and never inside .git or a linked folder.

    With links_to_folders, a link to a folder is given too, as one entry.
    """
    for folder, subfolders, files in os.walk(root):
        names = list(files)
        if links_to_folders:
            names += [name for name in subfolders if (Path(folder) / name).is_symlink()]
        subfolders[:] = sorted(name for name in subfolders if name != ".git")
        for name in sorted(names):
            path = Path(folder) / name
            yield path.relative_to(root).as_posix(), path


@dataclass(frozen=True)
class _NotText:
    reason: str


def _text_of(path: Path) -> str | _NotText:
    try:
        size = path.stat().st_size
        if size > FILE_BYTES_LIMIT:
            return _NotText(f"it holds {size} bytes, past the limit of {FILE_BYTES_LIMIT}")
        data = path.read_bytes()
    except OSError as error:
        return _NotText(f"it cannot be read ({error.strerror or type(error).__name__})")
    if b"\0" in data:
        return _NotText("it holds a NUL byte")
    return data.decode("utf-8", errors="replace")


def _unreadable(page: _Page) -> Finding:
    return Finding(
        check=CHECK,
        owner="docs",
        subject=page.path,
        message=f"{page.path} was written by the run and cannot be checked: {page.problem}.",
        next_action=(
            f"Write the section again as plain text of at most {FILE_BYTES_LIMIT} bytes, then "
            "run again."
        ),
    )


# Names: URLs, link destinations, e-mail addresses and hosts.


@dataclass(frozen=True)
class _KnownNames:
    urls: frozenset[str]
    emails: frozenset[str]
    words: frozenset[str]

    # Entities are decoded on both sides, so a name is compared as a reader sees it.
    @classmethod
    def found_in(cls, *texts: str) -> "_KnownNames":
        text = html.unescape("\n".join(texts))
        return cls(
            urls=frozenset((*_urls(text), *link_destinations(text))),
            emails=frozenset(email.lower() for email in _EMAIL.findall(text)),
            words=frozenset(_WORD.findall(text.lower())),
        )


@dataclass(frozen=True)
class _Links:
    names: _KnownNames
    output_repo: Path

    def leads_to_something_known(self, destination: str, *, page: str) -> bool:
        if destination.startswith("//") or _SCHEME.match(destination):
            return self._is_known_address(destination)
        target = destination.split("#", 1)[0].split("?", 1)[0]
        return not target or self._is_file_of_the_copy(unquote(target), page=page)

    def _is_known_address(self, destination: str) -> bool:
        if destination.lower().startswith(_MAILTO):
            address = destination[len(_MAILTO) :].split("?", 1)[0].lower()
            return address in self.names.emails
        return destination in self.names.urls

    # A path that starts with / is read from the repo root, as a code host shows it.
    def _is_file_of_the_copy(self, target: str, *, page: str) -> bool:
        root = os.path.normpath(self.output_repo)
        start = root if target.startswith("/") else os.path.dirname(os.path.join(root, page))
        joined = os.path.normpath(os.path.join(start, target.lstrip("/")))
        return joined.startswith(root + os.sep) and os.path.lexists(joined)


def _urls(text: str) -> Iterator[str]:
    for match in _URL.finditer(text):
        yield match.group().rstrip(_URL_TAIL)


def _ungrounded_names(page: str, text: str, *, links: _Links) -> Iterator[Finding]:
    shown_text = html.unescape(text)
    names = links.names
    for url in _urls(shown_text):
        if url not in names.urls:
            yield _unknown_name(page, "URL", url)
    for destination in link_destinations(shown_text):
        if not links.leads_to_something_known(destination, page=page):
            yield _unknown_link(page, destination)
    for email in _EMAIL.findall(shown_text):
        if email.lower() not in names.emails:
            yield _unknown_name(page, "e-mail address", email)
    for host, port in _HOST_AND_PORT.findall(shown_text):
        if host.lower() not in names.words:
            yield _unknown_name(page, "host", f"{host}:{port}")
    for host in _WWW_HOST.findall(shown_text):
        if host.lower() not in names.words:
            yield _unknown_name(page, "host", host)


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


def _unknown_link(page: str, destination: str) -> Finding:
    shown = one_line(destination, SHOWN_LIMIT)
    return Finding(
        check=CHECK,
        owner="docs",
        subject=f"{page}: {shown}",
        message=(
            f"{page} links to {shown}, which is neither an address the repo or a recorded probe "
            "body holds as written, nor a file of the output copy."
        ),
        next_action=(
            "Remove the link, or point it at a page or file that exists or an address the repo "
            "holds, then run again."
        ),
    )


# Commands.


@dataclass(frozen=True)
class _KnownCommands:
    make_targets: frozenset[str]
    # The README, Dockerfile and CI files as one line, with every run of spaces made one.
    written: str
    # The commands the repo's owner added to the profile, which no model can set.
    owners: frozenset[str]

    def holds(self, command: str) -> bool:
        return (
            self._is_make_target(command)
            or command in self.owners
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
                    "is not written in its README, a Dockerfile or a CI file, and is not one the "
                    "owner added to the profile."
                ),
                next_action=(
                    "Remove the line, or give a command from those places as it is written "
                    "there, then run again."
                ),
            )


def _commands(text: str) -> Iterator[str]:
    """Every line of every code block that is not data, with its prompt and extra spaces gone.

    A line of output or a comment is read too, because a reader can copy it as well.
    """
    for block in code_blocks(text):
        if _is_data(block):
            continue
        for line in _joined(block.lines):
            command = " ".join(line.strip().removeprefix(_PROMPT).split())
            if command:
                yield command


# Data counts only when it parses as its language says. A plain line is a whole document to a
# YAML parser, so YAML must be a mapping, and JSON an object or an array.
def _is_data(block: CodeBlock) -> bool:
    text = "\n".join(block.lines)
    try:
        if block.language == "json":
            return isinstance(json.loads(text), dict | list)
        if block.language in ("yaml", "yml"):
            return isinstance(yaml.safe_load(text), dict)
        if block.language == "toml":
            tomllib.loads(text)
            return True
    except (ValueError, RecursionError, yaml.YAMLError):
        return False
    return False


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
