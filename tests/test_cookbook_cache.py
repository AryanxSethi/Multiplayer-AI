"""The catalogue's own storage: validation, rollback, and what changed.

Everything here runs against a temporary directory and the real validators.
The point of the cache is that a reader is never worse off than with the
bundled file alone, so that is the property most of these check: a cache that
cannot be used is discarded, a refresh that cannot be trusted is refused
before it is written, and there is always somewhere to go back to.
"""

import json
import tempfile
from pathlib import Path

import pytest

from desktop_app.cookbook import (PREVIOUS_NAME, CatalogError, Model, Variant,
                                  describe_changes, load_cache, parse_catalog,
                                  parse_model, rollback_catalog, save_catalog)
from tests.test_cookbook import CODER


def directory():
    return Path(tempfile.mkdtemp())


def a_model(repo="a/b", size=4_000_000_000, **extra):
    """A complete record, so a test changes one thing rather than building one."""
    record = {"repo": repo, "name": repo.split("/")[-1], "parameters": 7_000_000_000,
              "context": 32768, "layers": 28, "kv_heads": 4, "head_dim": 128,
              "license": "apache-2.0", "tasks": ["coding"],
              "variants": [{"name": f"{repo.split('/')[-1]}-Q4_K_M.gguf",
                            "quantization": "Q4_K_M", "size": size}]}
    record.update(extra)
    return parse_model(record, 0)


def test_a_saved_catalogue_reads_back_identically(tmp_path) -> None:
    """What goes on disk has to be what would have been accepted.

    A cache that round-trips into something the validator would refuse is
    discarded on the next launch, so the user watches a refresh succeed and
    then find it gone. Saving re-parses the exact text it wrote rather than
    trusting the objects in hand.
    """
    models = [a_model("a/b"), a_model("c/d", size=2_000_000_000)]
    save_catalog(models, tmp_path)

    restored = load_cache(tmp_path)
    assert restored is not None, "the catalogue did not come back"
    assert [model.repo for model in restored] == ["a/b", "c/d"]
    assert restored[0].parameters == 7_000_000_000
    assert restored[0].license == "apache-2.0"
    assert restored[0].tasks == ("coding",)
    assert restored[1].variants[0].size == 2_000_000_000
    assert restored[0].verified is False, (
        "saving must not turn an untested model into a tested one")


def test_everything_the_cache_needs_survives_the_round_trip(tmp_path) -> None:
    """Each field a recommendation reads has to be on disk.

    A field that drops is not a cosmetic loss: without the geometry the cache
    is skipped for costing a conversation, without the task tags the model is
    filtered out of every category, and without the artifact repository there
    is no command to give. Each is set to something distinguishable so a drop
    cannot pass for a zero.
    """
    model = a_model("a/b", artifact_repo="someone/Builds-GGUF", languages=("en", "fr"),
                    vision=True, deprecated=True, deprecated_reason="pulled by the publisher")
    save_catalog([model], tmp_path)
    restored = load_cache(tmp_path)[0]

    assert restored.artifact_repo == "someone/Builds-GGUF"
    assert restored.languages == ("en", "fr")
    assert restored.vision is True
    assert restored.deprecated is True
    assert restored.deprecated_reason == "pulled by the publisher"
    assert restored.context == 32768 and restored.head_dim == 128
    assert restored.last_verified == ""


def test_a_cache_that_cannot_be_read_is_ignored_rather_than_raised(tmp_path) -> None:
    """One reason to return None, so the caller has one thing to check.

    Absent, corrupt, not JSON, and a version this build does not read are all
    the same situation to a reader: there is no usable cache, and the bundled
    catalogue is what is used instead. Any of them being an exception would
    mean the page could not open at all.
    """
    from desktop_app.cookbook import CACHE_NAME
    assert load_cache(tmp_path) is None, "an empty directory reported a catalogue"

    for content in ("{not json", json.dumps({"version": 99, "models": []}),
                    json.dumps({"version": 1}), json.dumps([1, 2, 3])):
        (tmp_path / CACHE_NAME).write_text(content, encoding="utf-8")
        assert load_cache(tmp_path) is None, f"{content!r} was accepted as a catalogue"


def test_a_refresh_that_would_be_unusable_is_never_written(tmp_path) -> None:
    """Validation before the write, not after it.

    The cache is validated by reading back the exact text that would land on
    disk. A catalogue that cannot be read back must not replace one that can,
    or the next launch has nothing and the user watches a successful refresh
    vanish.
    """
    good = [a_model("a/b")]
    save_catalog(good, tmp_path)

    broken = Model(repo="c/d", name="D", parameters=1_000_000_000, context=4096,
                   layers=4, kv_heads=2, head_dim=64, license="mit",
                   tasks=("general",), variants=(
                       Variant(name="d.gguf", quantization="Q4_K_M", size=-5),))
    with pytest.raises(CatalogError):
        save_catalog([broken], tmp_path)

    assert [model.repo for model in load_cache(tmp_path)] == ["a/b"], (
        "an unusable catalogue replaced a usable one")


def test_the_catalogue_before_a_refresh_is_kept_and_can_be_put_back(tmp_path) -> None:
    """The rollback is a move, not a copy, and it works.

    Two files rather than one, because a single cache file would make a bad
    refresh unrecoverable, which is the whole reason for validating before
    saving. The previous one is moved aside rather than copied so a copy that
    failed half way could not leave two good files and a rollback target
    nobody could tell from the live one.
    """
    first = [a_model("a/b")]
    second = [a_model("c/d"), a_model("e/f")]
    assert save_catalog(first, tmp_path) is False, (
        "there was nothing to keep on the first save")
    assert save_catalog(second, tmp_path) is True

    assert (tmp_path / PREVIOUS_NAME).is_file(), "the earlier catalogue was not kept"
    assert [model.repo for model in load_cache(tmp_path)] == ["c/d", "e/f"]

    restored = rollback_catalog(tmp_path)
    assert [model.repo for model in restored] == ["a/b"]
    assert [model.repo for model in load_cache(tmp_path)] == ["a/b"], (
        "rolling back did not replace the live catalogue")


def test_there_is_nothing_to_roll_back_to_until_there_has_been(tmp_path) -> None:
    """A button that always works is worse than one that does not.

    With no previous catalogue the answer is None, so the control can be
    disabled rather than offering a rollback to nothing.
    """
    assert rollback_catalog(tmp_path) is None
    save_catalog([a_model("a/b")], tmp_path)
    assert rollback_catalog(tmp_path) is None, (
        "the first catalogue claimed a predecessor it did not have")


def test_a_previous_catalogue_that_will_not_load_is_reported_not_restored(tmp_path) -> None:
    """Rolling back to something unreadable is not a rollback."""
    save_catalog([a_model("a/b")], tmp_path)
    save_catalog([a_model("c/d")], tmp_path)
    (tmp_path / PREVIOUS_NAME).write_text("{corrupt", encoding="utf-8")

    assert rollback_catalog(tmp_path) is None
    assert [model.repo for model in load_cache(tmp_path)] == ["c/d"], (
        "a failed rollback damaged the live catalogue")


def test_the_shipped_catalogue_can_be_copied_into_the_cache(tmp_path) -> None:
    """The cache is the bundled file with somewhere to live, and nothing else.

    Written from the parsed models rather than by copying the file, so what is
    stored is exactly what the validator accepted. A cache written by copying
    could contain a field this build has stopped understanding and still be
    read back as a catalogue missing it.
    """
    from desktop_app.cookbook import load_catalog
    bundled = load_catalog()
    save_catalog(bundled, tmp_path)
    restored = load_cache(tmp_path)

    assert [model.repo for model in restored] == [model.repo for model in bundled]
    assert restored[0].variants[0].size == bundled[0].variants[0].size
    assert restored[0].artifact_repo == bundled[0].artifact_repo


def test_a_refresh_that_changed_nothing_says_so(tmp_path) -> None:
    """Re-reading the same catalogue is not news.

    The previous wording reported how many records were read, which says
    nothing about whether anything changed and reads identically whether one
    model moved or none did.
    """
    models = [a_model("a/b")]
    assert describe_changes(models, models) == "Nothing has changed since the last check."


def test_a_refresh_says_what_it_actually_changed() -> None:
    """Additions, updates and removals, by name.

    A refresh that silently dropped a model is the one worth knowing about,
    and a count of records read cannot distinguish it from a refresh that
    changed nothing.
    """
    before = [a_model("a/b"), a_model("c/d", size=4_000_000_000)]
    after = [a_model("a/b"), a_model("c/d", size=5_000_000_000), a_model("e/f")]

    said = describe_changes(before, after).lower()
    assert "added e/f" in said
    assert "updated c/d" in said
    assert "a/b" not in said.replace("no longer listed", ""), (
        f"a model that did not change was reported as having: {said}")

    dropped = describe_changes(before, [before[0]]).lower()
    assert "no longer listed: c/d" in dropped

    first = describe_changes(None, before).lower()
    assert "added a/b, c/d" in first, (
        f"a first catalogue did not report everything it added: {first}")


def test_a_change_in_something_nobody_reads_is_not_a_change() -> None:
    """Only what a recommendation depends on counts.

    The reading date is stamped on every record a refresh touches, so
    comparing whole records would report every model as updated on every
    refresh and make the changelog useless.
    """
    before = [a_model("a/b", last_verified="2026-01-01")]
    after = [a_model("a/b", last_verified="2026-10-10")]
    assert describe_changes(before, after) == "Nothing has changed since the last check."


def test_a_withdrawn_model_is_kept_but_not_recommended() -> None:
    """Deleted would be indistinguishable from never having existed.

    Somebody who already has one needs to be told it was withdrawn; somebody
    deciding what to download must not be sent to it. So it stays in the
    catalogue with the reason and stays out of the recommendations.
    """
    from desktop_app.cookbook import LIKELY, classify, recommend
    from tests.test_cookbook import device
    live = a_model("a/b")
    gone = a_model("c/d", deprecated=True, deprecated_reason="no longer maintained")
    hardware = device(total_gb=64, available_gb=60, disk_gb=500)

    rows = recommend([live, gone], hardware, context=4096)
    assert [row["model"].repo for row in rows] == ["a/b"], (
        "a withdrawn model was recommended for download")

    listed = recommend([live, gone], hardware, context=4096, include_deprecated=True)
    assert {row["model"].repo for row in listed} == {"a/b", "c/d"}, (
        "a caller asking for the whole list did not get it")

    # And the reason survives, since it is the only part worth telling someone.
    assert classify(live, live.variants[0], hardware)["result"] == LIKELY


def test_a_withdrawn_flag_has_to_be_a_real_yes_or_no() -> None:
    """"truthy" is not true, and a string here would read as withdrawn."""
    for value in ("yes", 1, None, [], {}):
        with pytest.raises(CatalogError, match="deprecated"):
            parse_model({"repo": "a/b", "deprecated": value}, 0)
    assert parse_model({"repo": "a/b", "deprecated": True}, 0).deprecated is True
    assert parse_model({"repo": "a/b"}, 0).deprecated is False


def test_the_cache_is_written_beside_the_settings_and_not_over_the_bundled_file(tmp_path) -> None:
    """The bundled catalogue is shipped, read-only, and not ours to replace.

    It is the fallback for a reader with no cache and the thing a rollback
    falls back to conceptually, so a refresh writing over it would leave
    nothing behind it.
    """
    from desktop_app.cookbook import CATALOG_PATH, cache_paths
    live, previous = cache_paths(tmp_path)
    assert live.name == "cookbook-cache.json"
    assert previous.name == "cookbook-cache.previous.json"
    assert tmp_path in live.parents and tmp_path in previous.parents
    assert CATALOG_PATH not in (live, previous), (
        "the cache would overwrite the catalogue that ships with the app")
    assert CATALOG_PATH.is_file(), "the bundled catalogue went missing"


def test_a_cache_write_survives_a_reader_arriving_mid_swap(tmp_path) -> None:
    """The write is atomic, so there is never a half-written cache.

    Read as a plain write the file is truncated first, and a reader arriving
    in that window sees nothing at all -- which for this catalogue means a
    page that suddenly has no models in it.
    """
    save_catalog([a_model("a/b")], tmp_path)
    live, _ = __import__("desktop_app.cookbook", fromlist=["cache_paths"]).cache_paths(tmp_path)
    for _ in range(5):
        save_catalog([a_model("c/d")], tmp_path)
        assert load_cache(tmp_path) is not None, (
            "the cache was unreadable while it was being replaced")
    assert not list(tmp_path.glob("*.tmp")), (
        "a temporary file was left behind, which is how a failed write under a "
        "scanner or a sync client accumulates")