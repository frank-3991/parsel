from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

import pytest

from parsel.utils import extract_regex, iextract_regex, iflatten, shorten

if TYPE_CHECKING:
    from collections.abc import Iterator
    from re import Pattern


def test_iflatten_consumes_nested_iterators_lazily() -> None:
    consumed = []

    def values() -> Iterator[int]:
        for value in range(3):
            consumed.append(value)
            yield value

    flattened = iflatten([[values()]])
    assert consumed == []
    assert next(flattened) == 0
    assert consumed == [0]
    assert list(flattened) == [1, 2]
    assert consumed == [0, 1, 2]


@pytest.mark.parametrize(
    ("text", "width", "suffix", "expected"),
    [
        ("foobar", -1, "...", ValueError),
        ("foobar", 0, "...", ""),
        ("foobar", 1, "...", "."),
        ("foobar", 2, "...", ".."),
        ("foobar", 3, "...", "..."),
        ("foobar", 4, "...", "f..."),
        ("foobar", 5, "...", "fo..."),
        ("foobar", 6, "...", "foobar"),
        ("foobar", 7, "...", "foobar"),
        ("hello", 3, "…", "he…"),
        ("hello", 4, "…", "hel…"),
        ("test", 2, "->", "->"),
        ("test", 3, "->", "t->"),
        ("test", 4, "->", "test"),
        ("", 0, "...", ""),
        ("", 3, "...", ""),
    ],
)
def test_shorten(
    text: str, width: int, suffix: str, expected: str | type[Exception]
) -> None:
    if isinstance(expected, str):
        assert shorten(text, width, suffix=suffix) == expected
    else:
        with pytest.raises(expected):
            shorten(text, width, suffix=suffix)


@pytest.mark.parametrize(
    ("regex", "text", "replace_entities", "expected"),
    [
        (
            r"(?P<month>\w+)\s*(?P<day>\d+)\s*\,?\s*(?P<year>\d+)",
            "October  25, 2019",
            True,
            ["October", "25", "2019"],
        ),
        (
            r"(?P<month>\w+)\s*(?P<day>\d+)\s*\,?\s*(?P<year>\d+)",
            "October  25 2019",
            True,
            ["October", "25", "2019"],
        ),
        (
            r"(?P<extract>\w+)\s*(?P<day>\d+)\s*\,?\s*(?P<year>\d+)",
            "October  25 2019",
            True,
            ["October"],
        ),
        (
            r"\w+\s*\d+\s*\,?\s*\d+",
            "October  25 2019",
            True,
            ["October  25 2019"],
        ),
        (
            r"^.*$",
            "&quot;sometext&quot; &amp; &quot;moretext&quot;",
            True,
            ['"sometext" &amp; "moretext"'],
        ),
        (
            r"^.*$",
            "&quot;sometext&quot; &amp; &quot;moretext&quot;",
            False,
            ["&quot;sometext&quot; &amp; &quot;moretext&quot;"],
        ),
        (
            r"(?P<extract>\d+)",
            "no digits here",
            True,
            [],
        ),
    ],
)
def test_extract_regex(
    regex: str | Pattern[str],
    text: str,
    replace_entities: bool,
    expected: list[str],
) -> None:
    assert extract_regex(regex, text, replace_entities) == expected


@pytest.mark.parametrize(
    ("regex", "text", "replace_entities", "expected"),
    [
        (
            r"(?P<month>\w+)\s*(?P<day>\d+)\s*\,?\s*(?P<year>\d+)",
            "October  25, 2019",
            True,
            ["October", "25", "2019"],
        ),
        (
            r"(?P<extract>\w+)\s*(?P<day>\d+)\s*\,?\s*(?P<year>\d+)",
            "October  25 2019",
            True,
            ["October"],
        ),
        (
            r"\w+\s*\d+\s*\,?\s*\d+",
            "October  25 2019",
            True,
            ["October  25 2019"],
        ),
        (
            r"^.*$",
            "&quot;sometext&quot; &amp; &quot;moretext&quot;",
            False,
            ["&quot;sometext&quot; &amp; &quot;moretext&quot;"],
        ),
        (
            r"(?P<extract>\d+)",
            "no digits here",
            True,
            [],
        ),
    ],
)
def test_iextract_regex_matches_extract_regex(
    regex: str | Pattern[str],
    text: str,
    replace_entities: bool,
    expected: list[str],
) -> None:
    # The lazy iterator must expose the same strings as the list-returning API,
    # and must not run any matching before it is consumed.
    iterator = iextract_regex(regex, text, replace_entities)
    assert list(iterator) == expected
    assert list(iextract_regex(regex, text, replace_entities)) == extract_regex(
        regex, text, replace_entities
    )


class _CountingPattern:
    """Pattern wrapper counting how far finditer() iteration has progressed."""

    def __init__(self, pattern: Pattern[str]) -> None:
        self._pattern = pattern
        self.finditer_calls = 0
        self.matches_yielded = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pattern, name)

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        self.finditer_calls += 1
        for match in self._pattern.finditer(text):
            self.matches_yielded += 1
            yield match


@pytest.mark.parametrize(
    ("regex", "expected"),
    [
        # No groups: every whole match is yielded, one at a time.
        (r"\d+", ["1", "22", "333"]),
        # A single group mirrors re.findall(), including unmatched
        # optional groups as empty strings.
        (r"(\d+)?", ["1", "", "", "22", "", "", "", "333", ""]),
        # Multiple groups are flattened, with non-participating groups
        # normalized to empty strings like re.findall().
        (r"(\d+)(\w*)", ["1", "a", "22", "bb", "333", ""]),
        (r"(a)|(b)", ["a", "", "", "b", "", "b"]),
    ],
)
def test_iextract_regex_is_lazy(regex: str, expected: list[str]) -> None:
    pattern = _CountingPattern(re.compile(regex))
    iterator = iextract_regex(cast("Pattern[str]", pattern), "1a 22bb 333")
    assert pattern.finditer_calls == 0
    first = next(iterator)
    assert first == expected[0]
    # Only the match backing the first yielded value has been produced so far.
    assert pattern.finditer_calls == 1
    assert pattern.matches_yielded == 1
    assert list(iterator) == expected[1:]
    assert pattern.matches_yielded == len(
        list(re.compile(regex).finditer("1a 22bb 333"))
    )


def test_iextract_regex_named_group_stops_at_first_match() -> None:
    # The "extract" named group only ever looks at the first match, so the
    # iterator yields at most one value and never scans the rest of the text.
    class CountingSearchPattern:
        def __init__(self, pattern: Pattern[str]) -> None:
            self._pattern = pattern
            self.search_calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self._pattern, name)

        def search(self, text: str) -> re.Match[str] | None:
            self.search_calls += 1
            return self._pattern.search(text)

    pattern = CountingSearchPattern(re.compile(r"(?P<extract>\d+)"))
    iterator = iextract_regex(cast("Pattern[str]", pattern), "1 2 3")
    assert pattern.search_calls == 0
    assert list(iterator) == ["1"]
    assert pattern.search_calls == 1
    assert list(iextract_regex(r"(?P<extract>\d+)", "no digits")) == []
