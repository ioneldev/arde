"""What a child process printed, made safe and short enough for a message."""

import pytest

from arde.adapters.child_text import MESSAGE_TAIL_BYTES, bounded_text, visible_text


def test_a_message_keeps_the_last_two_thousand_bytes() -> None:
    assert MESSAGE_TAIL_BYTES == 2000


def test_short_plain_text_is_kept_as_it_is() -> None:
    assert bounded_text("go: cannot find main module\n", 100) == "go: cannot find main module\n"


def test_line_breaks_and_tabs_are_kept_because_go_errors_use_them() -> None:
    assert bounded_text("a.go:1:\n\tbad", 100) == "a.go:1:\n\tbad"


def test_the_end_of_the_text_is_kept_because_a_failure_says_what_went_wrong_there() -> None:
    assert bounded_text("start" + "x" * 50 + "the end", 10) == "xxxthe end"


def test_the_limit_counts_bytes_and_no_character_is_left_in_pieces() -> None:
    kept = bounded_text("é" * 100, 11)

    assert len(kept.encode("utf-8")) <= 11
    assert set(kept) == {"é"}


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("\x1b[2J", "\\x1b[2J"),
        ("\x1b]0;OWNED\x07", "\\x1b]0;OWNED\\x07"),
        ("a\rb", "a\\x0db"),
        ("nul\x00", "nul\\x00"),
        ("del\x7f", "del\\x7f"),
        ("c1\x9b", "c1\\x9b"),
    ],
)
def test_a_control_character_is_shown_and_never_passed_on(raw: str, shown: str) -> None:
    assert bounded_text(raw, 100) == shown
    assert visible_text(raw) == shown


def test_the_limit_holds_after_control_characters_grow_into_escapes() -> None:
    kept = bounded_text("\x1b" * 5000, 2000)

    assert len(kept.encode("utf-8")) <= 2000
    assert "\x1b" not in kept


def test_visible_text_cuts_nothing() -> None:
    long = "x" * 5000 + "\n"

    assert visible_text(long) == long
