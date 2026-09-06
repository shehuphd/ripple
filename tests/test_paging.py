"""The list pages' query-string contract: sort, dir, page, size, q."""

from __future__ import annotations

from ripple.web.paging import DEFAULT_SIZE, paginate

COLUMNS = [
    {"key": "when", "label": "When"},
    {"key": "name", "label": "Name"},
    {"key": "cost", "label": "Cost", "numeric": True},
    {"key": "actions", "label": ""},
]


def _items(count: int) -> list[dict]:
    return [
        {
            "id": str(index),
            "cells": {
                "when": {"text": f"day {index}"},
                "name": {
                    "text": f"item {index:03d}",
                    "sub": "odd" if index % 2 else None,
                },
                "cost": {"text": f"${index}"},
            },
            "sort": {
                "when": f"2026-01-{index:02d}",
                "name": f"item {index:03d}",
                "cost": index,
            },
        }
        for index in range(1, count + 1)
    ]


class TestDefaults:
    def test_the_plain_list_opens_on_the_first_column_descending(self):
        page = paginate({}, "/things", _items(3), COLUMNS)
        assert page.sort == "when"
        assert page.ascending is False
        assert [item["id"] for item in page.items] == ["3", "2", "1"]
        assert page.size == DEFAULT_SIZE
        assert page.page == 1

    def test_defaults_are_left_out_of_links(self):
        page = paginate({}, "/things", _items(3), COLUMNS)
        assert page.url() == "/things"
        assert page.url(page=1, size=50) == "/things"
        assert page.url(page=2) == "/things?page=2"

    def test_an_empty_list_is_one_empty_page(self):
        page = paginate({}, "/things", [], COLUMNS)
        assert page.items == []
        assert page.pages == 1
        assert page.first == 0 and page.last == 0


class TestSorting:
    def test_a_bare_sort_key_opens_on_the_column_s_own_direction(self):
        items = _items(5)
        by_cost = paginate({"sort": "cost"}, "/things", items, COLUMNS)
        assert by_cost.ascending is False
        by_name = paginate({"sort": "name"}, "/things", items, COLUMNS)
        assert by_name.ascending is True
        assert by_cost.dir_is_default and by_name.dir_is_default
        assert not by_name.sort_is_default

    def test_a_text_column_opens_ascending_and_a_numeric_one_descending(self):
        page = paginate({}, "/things", _items(3), COLUMNS)
        assert page.sort_url(COLUMNS[1]) == "/things?sort=name"
        assert page.sort_url(COLUMNS[2]) == "/things?sort=cost"
        # The first column opens newest first, so its reverse is the one
        # direction that has to be said.
        assert page.sort_url(COLUMNS[0]) == "/things?dir=asc"

    def test_the_sorted_column_offers_its_reverse(self):
        page = paginate({"sort": "cost", "dir": "desc"}, "/things", _items(3), COLUMNS)
        assert page.sort_url(COLUMNS[2]) == "/things?sort=cost&dir=asc"
        assert page.direction(COLUMNS[2]) == "descending"
        assert page.direction(COLUMNS[1]) == "none"

    def test_numbers_sort_as_numbers(self):
        items = _items(12)
        page = paginate({"sort": "cost", "dir": "desc"}, "/things", items, COLUMNS)
        assert [item["id"] for item in page.items][:3] == ["12", "11", "10"]

    def test_an_unknown_key_or_direction_falls_back(self):
        page = paginate(
            {"sort": "bogus", "dir": "sideways"}, "/things", _items(2), COLUMNS
        )
        assert page.sort == "when"
        assert page.ascending is False

    def test_the_actions_column_never_sorts(self):
        page = paginate({"sort": "actions"}, "/things", _items(2), COLUMNS)
        assert page.sort == "when"


class TestLeadRows:
    def test_a_lead_row_stays_on_top_in_every_ordering(self):
        items = _items(6)
        items[2]["lead"] = True
        for params in ({}, {"sort": "cost", "dir": "asc"}, {"sort": "name"}):
            page = paginate(params, "/things", items, COLUMNS)
            assert page.items[0]["id"] == "3", params
        # Searching still applies to it.
        page = paginate({"q": "item 005"}, "/things", items, COLUMNS)
        assert [item["id"] for item in page.items] == ["5"]


class TestPaging:
    def test_pages_slice_the_sorted_rows(self):
        items = _items(60)
        first = paginate({"size": "25"}, "/things", items, COLUMNS)
        second = paginate({"size": "25", "page": "2"}, "/things", items, COLUMNS)
        third = paginate({"size": "25", "page": "3"}, "/things", items, COLUMNS)
        assert first.pages == 3
        assert (first.first, first.last) == (1, 25)
        assert (second.first, second.last) == (26, 50)
        assert (third.first, third.last) == (51, 60)
        seen = [item["id"] for page in (first, second, third) for item in page.items]
        assert sorted(seen, key=int) == [str(index) for index in range(1, 61)]

    def test_a_page_past_the_end_is_the_last_page(self):
        page = paginate({"size": "25", "page": "40"}, "/things", _items(60), COLUMNS)
        assert page.page == 3

    def test_a_size_outside_the_choices_is_the_default(self):
        page = paginate({"size": "7"}, "/things", _items(3), COLUMNS)
        assert page.size == DEFAULT_SIZE
        page = paginate({"size": "lots"}, "/things", _items(3), COLUMNS)
        assert page.size == DEFAULT_SIZE

    def test_links_keep_size_and_sort(self):
        page = paginate(
            {"size": "25", "sort": "name", "dir": "asc", "page": "2"},
            "/things",
            _items(60),
            COLUMNS,
        )
        assert page.url(page=3) == "/things?sort=name&page=3&size=25"


class TestSearch:
    def test_the_needle_matches_cell_text_and_sub_lines(self):
        items = _items(10)
        page = paginate({"q": "item 007"}, "/things", items, COLUMNS)
        assert [item["id"] for item in page.items] == ["7"]
        assert page.matching == 1 and page.total == 10
        odd = paginate({"q": "ODD"}, "/things", items, COLUMNS)
        assert odd.matching == 5

    def test_a_search_resets_nothing_it_did_not_ask_for(self):
        page = paginate(
            {"q": "item", "sort": "cost", "size": "25"}, "/things", _items(3), COLUMNS
        )
        assert page.url() == "/things?sort=cost&size=25&q=item"

    def test_fixed_parameters_ride_on_every_link(self):
        page = paginate({}, "/findings", _items(3), COLUMNS, fixed={"script": "abc"})
        assert page.url() == "/findings?script=abc"
        assert page.sort_url(COLUMNS[1]) == "/findings?sort=name&script=abc"
