"""The struct tag pairs ARDE sets, read the way goinspect's normaliser reads a tag.

A later run compares the repo with these pairs taken out, so ARDE edits a tag only where that
comparison finds exactly the tag a person wrote: tools/goinspect/normalise.go holds the rule this
mirrors. A tag is a list of key:"value" pairs separated by spaces, with no key twice, and the pairs
ARDE adds are any oneOf and binding:"optional".
"""

from collections.abc import Sequence

ONE_OF_START = 'oneOf:"'
OPTIONAL_PAIR = 'binding:"optional"'


def one_of_pair(variants: Sequence[str]) -> str:
    # A pointer is encoded as what it points to, and swag reads only the type's name.
    names = (variant.replace("*", "") for variant in variants)
    return f'{ONE_OF_START}{",".join(names)}"'


def is_added_by_arde(pair: str) -> bool:
    return pair.startswith(ONE_OF_START) or pair == OPTIONAL_PAIR


def retagged(tag: str, ardes_pairs: Sequence[str]) -> str | None:
    """The tag with ARDE's own pairs replaced by the given ones, after the person's.

    The tag comes back unchanged when it needs no change, and as written when ARDE cannot read it
    and needs to add nothing. None means ARDE cannot read it and has pairs to add. A key the person
    already uses wins, because a second one would make the tag unreadable to the comparison.
    """
    pairs = _pairs_of(tag)
    if pairs is None:
        return None if ardes_pairs else tag
    kept = [pair for pair in pairs if not is_added_by_arde(pair)]
    used = {_key_of(pair) for pair in kept}
    for pair in ardes_pairs:
        if _key_of(pair) not in used:
            kept.append(pair)
            used.add(_key_of(pair))
    return tag if kept == pairs else " ".join(kept)


# goinspect reads a value with Go's own unquoting. A value with no backslash reads the same either
# way, so a tag with one is left to the person rather than read by a second, partial copy of Go.
def _pairs_of(tag: str) -> list[str] | None:
    if "\\" in tag:
        return None
    pairs: list[str] = []
    keys: set[str] = set()
    rest = tag.lstrip(" ")
    while rest:
        key_length = _key_length(rest)
        key = rest[:key_length]
        closing_quote = rest.find('"', key_length + 2)
        if key_length == 0 or not rest[key_length:].startswith(':"') or key in keys:
            return None
        if closing_quote == -1:
            return None
        keys.add(key)
        pairs.append(rest[: closing_quote + 1])
        rest = rest[closing_quote + 1 :]
        if rest and not rest.startswith(" "):
            return None
        rest = rest.lstrip(" ")
    return pairs


# A key runs to the first space, control character, colon or quote, as in reflect.StructTag.
def _key_length(text: str) -> int:
    length = 0
    while length < len(text) and text[length] > " " and text[length] not in ':"\x7f':
        length += 1
    return length


def _key_of(pair: str) -> str:
    return pair.split(":", 1)[0]
