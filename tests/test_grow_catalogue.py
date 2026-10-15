"""The catalogue growth tool, minus the network.

Everything here is the part that can be wrong quietly: a merge that drops a
model, a record that gets written without passing the same validation the app
applies at runtime, and a file that validates as an object but cannot be loaded
by the build that will read it.

The fetching is not tested and does not run in the suite. It makes outbound
requests, and a test that depends on somebody else's repository staying put is
a test that fails for reasons unrelated to this code.
"""

import asyncio
import json

import pytest

from desktop_app.cookbook import (CATALOG_VERSION, TASKS, CatalogError,
                                  load_catalog, parse_catalog)
from scripts.grow_catalogue import (build_catalogue, fetch_candidates,
                                    read_candidates, serialise)


def a_candidate(repo="a/b", builds="c/d", tasks=("coding",), name=""):
    return {"repo": repo, "artifact_repo": builds, "tasks": list(tasks),
            "name": name}


def write_candidates(tmp_path, entries):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def a_record(repo="a/b", artifacts="c/d", name="A Model", size=500_000_000):
    """A record shaped the way the Hub's own fetch produces one."""
    return {
        "repo": repo, "artifact_repo": artifacts, "name": name,
        "publisher": repo.split("/")[0], "source": "Hugging Face",
        "parameters": 1_000_000_000, "context": 8192, "layers": 24,
        "kv_heads": 4, "head_dim": 64, "license": "apache-2.0",
        "languages": ("en",), "vision": False, "tasks": ("coding",),
        "verified": False, "last_verified": "2026-10-10 00:00 UTC",
        "variants": [{"name": f"{repo.split('/')[-1]}-Q4_K_M.gguf",
                      "quantization": "Q4_K_M", "size": size}],
    }


def test_a_candidate_needs_the_things_only_a_person_can_supply():
    """Sizes and geometry come from the Hub. Tasks do not, and cannot.

    "coding" and "writing" are judgements about what a model is for, not
    published facts, so they are asked for rather than guessed. A candidate
    file missing them is refused before any request is made, so nobody waits
    on a fetch that was never going to be usable.
    """
    for missing in ("repo", "artifact_repo", "tasks"):
        entry = a_candidate()
        entry.pop(missing)
        with pytest.raises(ValueError, match=missing):
            read_candidates(write_candidates(_tmp(), [entry]))


def test_an_unknown_task_is_refused_rather_than_stored():
    """A filter nobody can select is not data.

    It would also make the empty-list problem worse: the page offers every task
    it knows, and a model tagged with something else silently never appears
    under any of them.
    """
    with pytest.raises(ValueError, match="teleport"):
        read_candidates(write_candidates(_tmp(), [a_candidate(tasks=["teleport"])]))


def test_a_candidate_file_written_on_windows_is_readable():
    """A byte-order mark is three invisible bytes and the whole file to json.loads.

    Notepad and PowerShell both write one, so a strict utf-8 read would fail
    the tool on the platform it is meant to run on -- for a reason that has
    nothing to do with any model.
    """
    path = _tmp() / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([a_candidate()]).encode("utf-8"))
    assert read_candidates(path)[0]["repo"] == "a/b"


def test_known_tasks_are_accepted():
    entries = read_candidates(write_candidates(
        _tmp(), [a_candidate(tasks=sorted(TASKS))]))
    assert entries[0]["tasks"] == sorted(TASKS)


def test_merging_keeps_what_was_shipped_and_puts_additions_on_the_end():
    """A catalogue diff should show one model changing, not the file reordering."""
    existing = load_catalog()
    incoming = [a_record(repo="new/one"), a_record(repo="new/two")]

    models, added, replaced = build_catalogue(existing, incoming)

    assert [model.repo for model in existing] == [
        model.repo for model in models[:len(existing)]], (
        "the models already shipped moved")
    assert added == ["new/one", "new/two"], added
    assert replaced == [], replaced
    assert len(models) == len(existing) + 2


def test_a_reread_model_replaces_its_own_figures_and_nothing_else():
    """A refreshed figure is better than the one it supersedes."""
    existing = load_catalog()
    target = existing[0]
    bigger = a_record(repo=target.repo, artifacts=target.artifact_repo,
                      name=target.display, size=999_000_000)

    models, added, replaced = build_catalogue(existing, [bigger])

    assert added == [], f"a re-read was treated as an addition: {added}"
    assert replaced == [target.repo], replaced
    assert len(models) == len(existing), "a re-read changed the number of models"
    kept = next(model for model in models if model.repo == target.repo)
    assert kept.variants[0].size == 999_000_000, (
        "the refreshed figure was not kept")


def test_a_record_that_fails_validation_is_refused_not_merged():
    """The tool writes through the same check the app applies at runtime.

    Otherwise a model could enter the shipped catalogue by a route the app's
    own refresh would have refused, and the difference would not be visible
    until a figure was wrong.
    """
    existing = load_catalog()
    broken = a_record()
    broken["context"] = -8

    with pytest.raises(CatalogError):
        build_catalogue(existing, [broken])


def test_a_model_with_no_downloadable_build_is_never_recommended():
    """Nothing to download is not a model anybody can act on.

    The Hub path refuses this before it can be stored -- ``fetch_entry`` raises
    rather than returning a record with an empty build list -- but the parser
    does not, because a hand-written or partially-fetched catalogue can still
    contain one. So the guarantee that matters is asserted where it actually
    holds: it parses, and then it never reaches anybody's screen.
    """
    from desktop_app.cookbook import detect_hardware, recommend

    existing = load_catalog()
    empty = a_record()
    empty["variants"] = []
    models, _, _ = build_catalogue(existing, [empty])

    stored = next(model for model in models if model.repo == empty["repo"])
    assert stored.variants == (), "the fixture did not reach the catalogue"

    recommended = recommend(models, detect_hardware())
    assert empty["repo"] not in {row["model"].repo for row in recommended}, (
        "a model with nothing to download was recommended, which sends "
        "somebody to a download that cannot load")


def test_the_file_written_is_one_this_build_can_actually_load():
    """Validated as an object is not the same as loadable.

    A catalogue that fails to parse is silently discarded on the next launch,
    taking every figure in it with it, so the text is built and read back
    before it is returned rather than trusted.
    """
    existing = load_catalog()
    models, _, _ = build_catalogue(existing, [a_record(repo="new/one")])
    text = serialise(models, "2026-10-11", "Every number here was read.")

    payload = json.loads(text)
    assert payload["version"] == CATALOG_VERSION
    assert payload["updated"] == "2026-10-11"
    assert "read" in payload["note"], "the note about provenance was dropped"

    parsed = parse_catalog(payload)
    assert len(parsed) == len(models), (
        "the file that would be written holds a different number of models "
        "than the one built")


def test_one_unreachable_repository_does_not_abandon_the_rest(monkeypatch):
    """A list of twenty with one dead entry should still widen by nineteen."""
    import desktop_app.cookbook_hub as hub

    entries = [a_candidate(repo="good/one"), a_candidate(repo="gone/two")]

    async def one_fails(client, repo, artifact_repo=None, base=None):
        if repo == "gone/two":
            raise OSError("name resolution failed")
        return a_record(repo=repo, artifacts=artifact_repo)

    monkeypatch.setattr(hub, "fetch_entry", one_fails)
    records, failures = asyncio.run(fetch_candidates(entries))

    assert [record["repo"] for record in records] == ["good/one"], (
        "one failure changed what the others produced")
    assert failures and failures[0][0] == "gone/two", failures
    assert "name resolution failed" in failures[0][1], (
        f"the reason was swallowed: {failures[0][1]!r}")


def test_the_tasks_a_person_supplied_reach_the_catalogue(monkeypatch):
    """The one field the Hub cannot answer has to survive the merge."""
    import desktop_app.cookbook_hub as hub

    async def one_reads(client, repo, artifact_repo=None, base=None):
        return a_record(repo=repo, artifacts=artifact_repo)

    monkeypatch.setattr(hub, "fetch_entry", one_reads)
    entries = [a_candidate(repo="new/one", tasks=["coding", "writing"],
                           name="Chosen Name")]
    records, failures = asyncio.run(fetch_candidates(entries))
    assert not failures

    models, _, _ = build_catalogue(load_catalog(), records)
    added = next(model for model in models if model.repo == "new/one")
    assert added.tasks == ("coding", "writing"), (
        f"the supplied tasks did not survive: {added.tasks}")
    assert added.display == "Chosen Name", (
        f"the supplied name did not survive: {added.display}")


def test_the_date_on_the_file_is_the_day_the_figures_were_read(monkeypatch, tmp_path):
    """--today was accepted by the parser and then thrown away.

    It was passed to build_catalogue, whose `updated` parameter its own body
    never used, and serialise was handed the catalogue's existing date
    instead. So the file always claimed to have been read on the day it was
    first written, however long after the figures were actually fetched -- and
    the date is the whole provenance of a catalogue whose point is that its
    numbers came from somewhere.
    """
    import json as _json
    import scripts.grow_catalogue as tool

    # One model read from a stubbed fetch, and a catalogue to merge it into.
    async def one_reads(client, repo, artifact_repo=None, base=None):
        return a_record(repo=repo, artifacts=artifact_repo)

    import desktop_app.cookbook_hub as hub
    monkeypatch.setattr(hub, "fetch_entry", one_reads)

    existing = json.loads(tool.CATALOG_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(tool, "CATALOG_PATH", tmp_path / "catalog.json")
    (tmp_path / "catalog.json").write_text(
        _json.dumps(dict(existing, updated="2020-01-01")), encoding="utf-8")

    candidates = write_candidates(tmp_path, [a_candidate(repo="new/one")])

    before = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert tool.main([str(candidates), "--dry-run",
                      "--today", "2026-10-11"]) == 0
    assert (tmp_path / "catalog.json").read_text(encoding="utf-8") == before, (
        "a dry run wrote to the catalogue")

    assert tool.main([str(candidates), "--today", "2026-10-11"]) == 0
    payload = _json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert payload["updated"] == "2026-10-11", (
        f"--today did not reach the file; it still says {payload['updated']!r}")

    # Without it, the existing date is kept rather than blanked -- a run that
    # added nothing should not claim to have re-read everything.
    candidates2 = write_candidates(tmp_path, [a_candidate(repo="new/two")])
    assert tool.main([str(candidates2)]) == 0
    payload = _json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert payload["updated"] == "2026-10-11", (
        "a run without --today discarded the date the file already had")
    assert payload["note"] == existing["note"], (
        "the note about where the figures came from was replaced")


def _tmp():
    import tempfile
    import pathlib
    return pathlib.Path(tempfile.mkdtemp())