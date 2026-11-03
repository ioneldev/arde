"""The one list of characters a message shows escaped and model text may not hold."""

import sys
import unicodedata

from arde.domain.base import REFUSED_RANGES, UNPRINTABLE_RANGES

UNPRINTABLE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}


def test_the_unprintable_characters_are_every_control_format_and_separator_code_point() -> None:
    listed = {code for first, last in UNPRINTABLE_RANGES for code in range(first, last + 1)}

    every = {
        code
        for code in range(sys.maxunicode + 1)
        if unicodedata.category(chr(code)) in UNPRINTABLE_CATEGORIES
    }

    assert listed == every, unicodedata.unidata_version


DIRECTION_CONTROLS = {*range(0x202A, 0x202F), *range(0x2066, 0x206A)}


def test_the_refused_characters_are_controls_separators_the_mark_and_direction_controls() -> None:
    listed = {code for first, last in REFUSED_RANGES for code in range(first, last + 1)}

    controls = {
        code for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)) == "Cc"
    }
    assert listed == controls | {0x2028, 0x2029, 0xFEFF} | DIRECTION_CONTROLS
