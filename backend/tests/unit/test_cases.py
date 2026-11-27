"""Similarity search over case bases and storing cases into them."""

from pathlib import Path

import pytest
from cbrkit.spec import dumps
from exar.tools import Retry
from pydantic import JsonValue

from hivegent.tools.base import SearchPath
from hivegent.tools.cases import (
    CaseBaseCache,
    Limits,
    open_case_base,
    retain_cases,
    similar_cases,
)
from hivegent.tools.changes import ChangeReason, Changeset, Write
from hivegent.tools.changeset import AppliedChanges, ChangesetOutcome

_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d">
<process id="p"><startEvent id="s" name="Start"/><task id="t" name="{task}"/>
<endEvent id="e" name="End"/><sequenceFlow id="f1" sourceRef="s" targetRef="t"/>
<sequenceFlow id="f2" sourceRef="t" targetRef="e"/></process></definitions>"""

_CARS = "model,price,fuel\nGolf,20000,petrol\nPolo,15000,diesel\nPassat,35000,petrol\n"


def _open(tmp_path: Path, name: str, cache: CaseBaseCache):
    return open_case_base((SearchPath.of(tmp_path),), name, Limits(), cache=cache)


async def test_ranks_records_by_example_without_writing_and_caches_by_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "cars.csv").write_text(_CARS)
    paths = (SearchPath.of(tmp_path),)
    cache = CaseBaseCache()
    tool = similar_cases(paths, cache=cache)

    schema = await tool("cars.csv")
    output = await tool("cars.csv", query={"price": 21000, "fuel": "Petrol"}, limit=2)

    assert "| price | linear " in schema.return_value
    assert "configuration inferred from the data" in schema.return_value
    assert [match.key for match in output.metadata.matches] == [0, 2]
    assert (
        "| 1 | 0 | 0.97 | 20000 (0.94) | petrol (1.00) | Golf |" in output.return_value
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cars.csv"]

    first = _open(tmp_path, "cars.csv", cache)

    assert _open(tmp_path, "cars.csv", cache) is not first
    assert _open(tmp_path, "cars.csv", cache).cases is first.cases

    # A sidecar is never read, so it neither changes the configuration nor the cache key.
    (tmp_path / "cars.csv.cbrkit.json").write_text(dumps(first.config))

    assert _open(tmp_path, "cars.csv", cache).cases is first.cases

    with pytest.raises(Retry, match="does not fit"):
        await tool("cars.csv", query={"colour": "red"})


def test_the_cache_drops_the_least_recently_used_beyond_its_bytes(
    tmp_path: Path,
) -> None:
    """Each case base weighs at least its share of the bytes, so a cache of two entries keeps two."""
    cache = CaseBaseCache(max_bytes=2 * 1024 * 1024, max_entries=2)

    for name in ("a.csv", "b.csv", "c.csv"):
        (tmp_path / name).write_text(_CARS)

    a, b = (_open(tmp_path, name, cache) for name in ("a.csv", "b.csv"))

    assert _open(tmp_path, "a.csv", cache).cases is a.cases

    _ = _open(tmp_path, "c.csv", cache)

    assert _open(tmp_path, "a.csv", cache).cases is a.cases
    assert _open(tmp_path, "b.csv", cache).cases is not b.cases


async def test_ranks_graphs_of_a_folder_without_reading_sidecars_as_cases(
    tmp_path: Path,
) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "ship.bpmn").write_text(_BPMN.format(task="Ship order"))
    (flows / "bill.bpmn").write_text(_BPMN.format(task="Bill customer"))
    (tmp_path / "query.bpmn").write_text(_BPMN.format(task="Ship orders"))
    tool = similar_cases((SearchPath.of(tmp_path),))
    output = await tool("flows", query_path="query.bpmn")

    assert output.metadata.count == 2
    assert [match.key for match in output.metadata.matches] == [
        "ship.bpmn",
        "bill.bpmn",
    ]
    assert "node t -> t" in output.return_value

    (tmp_path / "broken.bpmn").write_text("<definitions><unclosed>")

    with pytest.raises(Retry, match="no BPMN or cbrkit graph file"):
        await tool("flows", query_path="broken.bpmn")


async def test_refuses_an_empty_query_and_notes_what_no_case_can_match(
    tmp_path: Path,
) -> None:
    rows = [(1, "petrol"), (2, "diesel")] * 2
    (tmp_path / "cars.csv").write_text(
        "price,fuel\n" + "".join(f"{p},{f}\n" for p, f in rows)
    )
    tool = similar_cases((SearchPath.of(tmp_path),))

    for query in ({}, {"fuel": " ", "price": None}):
        with pytest.raises(Retry, match="names no attribute"):
            await tool("cars.csv", query=query)

    output = (await tool("cars.csv", query={"fuel": "hybrid", "price": 9})).return_value

    assert output.startswith("No case shares anything with the query")
    assert "Note: fuel hybrid is none of its values (diesel, petrol)" in output
    assert "Note: price 9 is above the largest value 2 of the records." in output


async def test_retains_a_solution_as_a_planned_write(tmp_path: Path) -> None:
    cars = _CARS.replace("20000", "20000.50")
    (tmp_path / "cars.csv").write_text(cars)
    paths = (SearchPath.of(tmp_path),)
    committed: list[Changeset[str]] = []

    async def commit(changeset: Changeset[str]) -> ChangesetOutcome:
        committed.append(changeset)

        return AppliedChanges(("wrote cars.csv",))

    tool = retain_cases(paths, commit=commit)
    solution: dict[str, JsonValue] = {"model": "Up", "price": 12000, "fuel": "petrol"}

    planned = await tool("cars.csv", query={"price": 12000}, solutions=[solution])

    assert committed == []
    assert "add (solution)" in planned.return_value

    await tool("cars.csv", query={"price": 12000}, solutions=[solution], dry_run=False)
    [write] = committed[0].operations
    [reason] = committed[0].reasons

    assert isinstance(write, Write)
    assert reason == ChangeReason("add", None, reason.reason)
    assert reason.reason
    assert write.target == "cars.csv"
    assert write.content == cars + "Up,12000,petrol\n"
    assert (tmp_path / "cars.csv").read_text() == cars
