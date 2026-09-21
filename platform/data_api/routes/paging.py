"""
Shared cursor-paging helpers for the scroll-back routes (candles, snapshots, indicator
series/values): find a page across data gaps and answer "is there anything older?" from the
catalog's own file ranges (`catalog_stats.data_file_ranges`), not fixed-size probe windows.
"""

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def has_older_data(ranges: list[tuple[int, int]], ns: int) -> bool:
    return bool(ranges) and ranges[0][0] < ns


def fetch_page(
    fetch: Callable[[int, int], list[T]],
    ranges: list[tuple[int, int]],
    before_ns: int,
    span_ns: int,
) -> list[T]:
    """
    First non-empty `fetch(start_ns, end_ns)` walking back from `before_ns` in `span_ns`
    windows, jumping over gaps straight to the last data before each empty window. `[]` only
    when nothing older exists at all.
    """
    end_ns = before_ns
    while True:
        start_ns = end_ns - span_ns
        result = fetch(start_ns, end_ns)
        if result:
            return result
        older_ends = [end for start, end in ranges if start < start_ns]
        if not older_ends:
            return []
        end_ns = min(max(older_ends), start_ns)  # min(): always progress, even mid-file
