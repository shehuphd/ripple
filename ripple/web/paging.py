"""Server-side sort, search, and paging for the shared list pages.

Every screen has a URL, so the state of a list lives in its query string:
`?sort=cost&dir=desc&page=2&size=50&q=harbour`. The page renders only the
rows it shows, and a header, pager, or search box is a link to the next
URL rather than a display toggle over rows the browser already holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

PAGE_SIZES = (25, 50, 100)
DEFAULT_SIZE = 50


@dataclass
class Page:
    """One page of a list, with everything the template needs to link on."""

    items: list[dict[str, Any]]
    total: int
    matching: int
    page: int
    pages: int
    size: int
    sort: str | None
    ascending: bool
    q: str
    path: str
    default_sort: str | None = None
    fixed: dict[str, str] = field(default_factory=dict)
    columns: list[dict[str, Any]] = field(default_factory=list)

    def opens_ascending(self, key: str | None) -> bool:
        """The direction a column opens on when its link is followed: the
        first column newest or biggest first, a number biggest first, a date
        newest first, a word alphabetically."""
        return opens_ascending(key, self.default_sort, self.columns)

    @property
    def sort_is_default(self) -> bool:
        return self.sort == self.default_sort

    @property
    def dir_is_default(self) -> bool:
        return self.ascending == self.opens_ascending(self.sort)

    @property
    def first(self) -> int:
        return (self.page - 1) * self.size + 1 if self.matching else 0

    @property
    def last(self) -> int:
        return min(self.matching, self.page * self.size)

    def url(self, **changes: Any) -> str:
        """This list's URL with some of its state changed.

        Defaults are left out of the query string, so the plain path stays
        the canonical address of the list as it opens, and `?sort=cost` is
        cost biggest first without saying so.
        """
        state: dict[str, Any] = {
            "sort": self.sort,
            "dir": "asc" if self.ascending else "desc",
            "page": self.page,
            "size": self.size,
            "q": self.q,
            **self.fixed,
        }
        state.update(changes)
        query = {}
        opening = "asc" if self.opens_ascending(state["sort"]) else "desc"
        for key, value in state.items():
            if value in (None, ""):
                continue
            if key == "sort" and value == self.default_sort:
                continue
            if key == "dir" and value == opening:
                continue
            if key == "page" and int(value) == 1:
                continue
            if key == "size" and int(value) == DEFAULT_SIZE:
                continue
            query[key] = value
        encoded = urlencode(query)
        return f"{self.path}?{encoded}" if encoded else self.path

    def sort_url(self, column: dict[str, Any]) -> str:
        """The header link for a column: reverse when it is the sorted one,
        otherwise open on the reading people ask for first (the biggest
        number, the newest date, the first word)."""
        key = column["key"]
        if key == self.sort:
            ascending = not self.ascending
        else:
            ascending = self.opens_ascending(key)
        return self.url(sort=key, dir="asc" if ascending else "desc", page=1)

    def direction(self, column: dict[str, Any]) -> str:
        if column["key"] != self.sort:
            return "none"
        return "ascending" if self.ascending else "descending"


def opens_ascending(
    key: str | None, default_sort: str | None, columns: list[dict[str, Any]]
) -> bool:
    """Whether a column's first click orders it ascending."""
    if key is None or key == default_sort or key == "when":
        return False
    column = next((one for one in columns if one["key"] == key), None)
    return not (column or {}).get("numeric")


def _text_of(item: dict[str, Any]) -> str:
    """What the search box matches: every cell's text and sub-line."""
    parts = []
    for cell in (item.get("cells") or {}).values():
        if not cell:
            continue
        parts.append(str(cell.get("text") or ""))
        if cell.get("sub"):
            parts.append(str(cell["sub"]))
    for action in item.get("actions") or []:
        parts.append(str(action.get("label") or ""))
    return " ".join(parts).lower()


def paginate(
    params: Any,
    path: str,
    items: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    fixed: dict[str, str] | None = None,
) -> Page:
    """Filter, sort, and slice `items` by the request's query string.

    `params` is the request's query parameters. `fixed` carries parameters
    the list keeps on every link, such as the findings page's script filter.
    Sorting reads each item's `sort` values, so a cost orders as a number
    and a severity by weight. An item marked `lead` stays ahead of the rest
    in every ordering.
    """
    sortable = [column for column in columns if column["key"] != "actions"]
    by_key = {column["key"]: column for column in sortable}
    default_sort = sortable[0]["key"] if sortable else None

    sort = params.get("sort") or default_sort
    if sort not in by_key:
        sort = default_sort
    direction = params.get("dir")
    if direction not in ("asc", "desc"):
        direction = "asc" if opens_ascending(sort, default_sort, sortable) else "desc"
    ascending = direction == "asc"

    size = _int(params.get("size"), DEFAULT_SIZE)
    if size not in PAGE_SIZES:
        size = DEFAULT_SIZE
    q = (params.get("q") or "").strip()
    needle = q.lower()

    total = len(items)
    hits = [item for item in items if not needle or needle in _text_of(item)]
    if sort is not None:
        numeric = bool(by_key[sort].get("numeric"))

        def value(item: dict[str, Any]) -> Any:
            raw = (item.get("sort") or {}).get(sort)
            if numeric:
                try:
                    return float(raw if raw not in (None, "") else 0)
                except (TypeError, ValueError):
                    return 0.0
            return str(raw or "").lower()

        hits.sort(key=value, reverse=not ascending)
    # A row marked `lead` needs a decision (a suspected duplicate pair), so
    # it stays at the top however the list is sorted.
    hits = [item for item in hits if item.get("lead")] + [
        item for item in hits if not item.get("lead")
    ]

    pages = max(1, -(-len(hits) // size))
    page = min(max(1, _int(params.get("page"), 1)), pages)
    start = (page - 1) * size
    return Page(
        items=hits[start : start + size],
        total=total,
        matching=len(hits),
        page=page,
        pages=pages,
        size=size,
        sort=sort,
        ascending=ascending,
        q=q,
        path=path,
        default_sort=default_sort,
        fixed=dict(fixed or {}),
        columns=sortable,
    )


def _int(raw: Any, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback
