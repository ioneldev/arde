"""What a child printed, made safe to show. A repo's test or service chooses every byte of it.

An escape sequence can rewrite a terminal's title or clear the screen, and hide a failure.
"""

import re

# What a message keeps of a child's output. A failing command says what went wrong at the end.
MESSAGE_TAIL_BYTES = 2000

# Every control character but the line break and tab that Go's own errors are laid out with.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def bounded_text(text: str, limit: int) -> str:
    """The end of the text, in at most limit bytes once its control characters are shown."""
    tail = visible_text(text).encode("utf-8", errors="replace")[-limit:]
    # The cut can land inside a character, and "ignore" drops what is left of it.
    return tail.decode("utf-8", errors="ignore")


def visible_text(text: str) -> str:
    """The whole text, with each control character written out as \\xNN."""
    return _CONTROL_CHARACTER.sub(_written_out, text)


def _written_out(character: re.Match[str]) -> str:
    return f"\\x{ord(character[0]):02x}"
