"""The one list of characters a message shows escaped and model text may not hold."""

import sys
import unicodedata

from arde.domain.base import UNPRINTABLE_RANGES

UNPRINTABLE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}


def test_the_unprintable_characters_are_every_control_format_and_separator_code_point() -> None:
    listed = {code for first, last in UNPRINTABLE_RANGES for code in range(first, last + 1)}

    every = {
        code
        for code in range(sys.maxunicode + 1)
        if unicodedata.category(chr(code)) in UNPRINTABLE_CATEGORIES
    }

    assert listed == every, unicodedata.unidata_version
