from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from w3lib.html import replace_entities as w3lib_replace_entities

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


def flatten(x: Iterable[Any]) -> list[Any]:
    """flatten(sequence) -> list
    Returns a single, flat list which contains all elements retrieved
    from the sequence and all recursively contained sub-sequences
    (iterables).
    Examples:
    >>> flatten([[[1,2,3], (42,None)], [4,5], [6], 7, (8,9,10)])
    [1, 2, 3, 42, None, 4, 5, 6, 7, 8, 9, 10]
    >>> flatten(["foo", "bar"])
    ['foo', 'bar']
    >>> flatten(["foo", ["baz", 42], "bar"])
    ['foo', 'baz', 42, 'bar']
    """
    return list(iflatten(x))


def iflatten(x: Iterable[Any]) -> Iterator[Any]:
    """iflatten(sequence) -> Iterator
    Similar to ``flatten()``, but returns iterator instead
    Examples:
    >>> list(iflatten([[1, 2], (3, 4)]))
    [1, 2, 3, 4]
    """
    for el in x:
        if _is_listlike(el):
            yield from iflatten(el)
        else:
            yield el


def _is_listlike(x: Any) -> bool:
    """
    >>> _is_listlike("foo")
    False
    >>> _is_listlike(5)
    False
    >>> _is_listlike(b"foo")
    False
    >>> _is_listlike([b"foo"])
    True
    >>> _is_listlike((b"foo",))
    True
    >>> _is_listlike({})
    True
    >>> _is_listlike(set())
    True
    >>> _is_listlike((x for x in range(3)))
    True
    >>> _is_listlike(range(5))
    True
    """
    return hasattr(x, "__iter__") and not isinstance(x, (str, bytes))


def iextract_regex(
    regex: str | re.Pattern[str], text: str, replace_entities: bool = True
) -> Iterator[str]:
    """Yield the strings extracted from *text* with *regex*, lazily.

    The extraction policies mirror those of :func:`extract_regex`:

    * if the regex contains a named group called "extract", the content of that
      group is taken from the first match and nothing else is yielded
    * if the regex contains multiple numbered groups, all those are yielded
      (flattened) for every match
    * if the regex doesn't contain any group, the entire regex match is yielded

    Character entity references are replaced by their corresponding character,
    except for ``&amp;`` and ``&lt;``, unless *replace_entities* is ``False``.
    Because iteration is lazy, callers that only need the first result never
    pay for matching the rest of *text*.
    """
    if isinstance(regex, str):
        regex = re.compile(regex, re.UNICODE)

    def process(string: str) -> str:
        # Character entity references are replaced by their corresponding
        # character, except for "&amp;" and "&lt;", unless replacements are
        # switched off.
        if replace_entities:
            return w3lib_replace_entities(string, keep=["lt", "amp"])
        return string

    if "extract" in regex.groupindex:
        # named group: only the first match is ever considered
        try:
            extracted = cast("re.Match[str]", regex.search(text)).group("extract")
        except AttributeError:
            return
        if extracted is not None:
            yield process(extracted)
        return

    # full regex or numbered groups. finditer() exposes the same matches as
    # findall(), but one at a time, and non-participating groups are
    # normalized to an empty string the way findall() does.
    group_count = regex.groups
    if group_count == 0:
        for match in regex.finditer(text):
            yield process(match.group(0))
    elif group_count == 1:
        for match in regex.finditer(text):
            yield process(match.group(1) or "")
    else:
        for match in regex.finditer(text):
            for group in match.groups():
                yield process(group or "")


def extract_regex(
    regex: str | re.Pattern[str], text: str, replace_entities: bool = True
) -> list[str]:
    """Extract a list of strings from the given text using the following policies:
    * if the regex contains a named group called "extract" that will be returned
    * if the regex contains multiple numbered groups, all those will be returned (flattened)
    * if the regex doesn't contain any group the entire regex matching is returned
    """
    return list(iextract_regex(regex, text, replace_entities))


def shorten(text: str, width: int, suffix: str = "...") -> str:
    """Truncate the given text to fit in the given width."""
    if len(text) <= width:
        return text
    if width > len(suffix):
        return text[: width - len(suffix)] + suffix
    if width >= 0:
        return suffix[len(suffix) - width :]
    raise ValueError("width must be equal or greater than 0")
