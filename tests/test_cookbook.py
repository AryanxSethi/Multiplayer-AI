"""The Cookbook's arithmetic, checked against published model geometry.

No Qt, like ``test_resources``: everything in ``desktop_app/cookbook`` is
arithmetic over metadata, and testing it needs nothing but the numbers.
"""

import json

import pytest

from desktop_app.cookbook import (CATALOG_VERSION, LIKELY, NOT_RECOMMENDED,
                                  POSSIBLE, UNKNOWN, VERIFIED, CatalogError,
                                  Model, Variant, classify, detect_hardware,
                                  estimate, human_bytes, kv_cache_bytes,
                                  load_catalog, ollama_commands, parse_catalog,
                                  parse_model, recommend)


def device(total_gb=32, available_gb=24, vram=None, disk_gb=100, cores=16):
    """A device described by hand rather than read, so a test states its case."""
    return detect_hardware.__globals__["Hardware"](
        platform="Linux 6.1", architecture="x86_64", cpu_cores=cores,
        memory_total=int(total_gb * 1024 ** 3),
        memory_available=int(available_gb * 1024 ** 3),
        disk_free=int(disk_gb * 1024 ** 3), vram=vram, unified_memory=False,
        sources={"memory_available": "manual"})


# Qwen/Qwen2.5-Coder-7B-Instruct, read from the Hub on 2026-10-10: the
# safetensors index reports 7,615,616,512 parameters and config.json reports
# 28 layers, 4 key/value heads and a 3584-wide hidden size over 28 heads.
CODER = Model(repo="Qwen/Qwen2.5-Coder-7B-Instruct",
              artifact_repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
              name="Qwen2.5 Coder 7B", publisher="Qwen", parameters=7615616512,
              context=32768, layers=28, kv_heads=4, head_dim=128,
              license="apache-2.0", tasks=("coding", "reasoning"),
              last_verified="2026-10-10",
              variants=(
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q2_K.gguf",
                          quantization="Q2_K", size=3015940896),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q3_K_S.gguf",
                          quantization="Q3_K_S", size=3492369184),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q4_0.gguf",
                          quantization="Q4_0", size=4444121888),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
                          quantization="Q4_K_M", size=4683074336),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf",
                          quantization="Q5_K_M", size=5444832032),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q6_K.gguf",
                          quantization="Q6_K", size=6254199584),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-Q8_0.gguf",
                          quantization="Q8_0", size=8098525984),
                  Variant(name="Qwen2.5-Coder-7B-Instruct-f16.gguf",
                          quantization="f16", size=15237853696)))


def test_the_kv_cache_matches_the_published_geometry_of_a_real_model():
    """Two keys and two values per layer per token, from the model's own shape.

    Pinned against Qwen2.5-Coder-7B-Instruct's published config so the formula
    cannot drift into agreeing with itself. 2 x28 layers x4 kv heads x128
    dimensions x2 bytes is 57,344 per token, and 32,768 tokens of that is
    1.75 GiB -- which is the reason a model with room for its weights can still
    fail on a long conversation.
    """
    cache = kv_cache_bytes(CODER, 32768)
    assert cache == 2 * 28 * 4 * 128 * 2 * 32768
    assert human_bytes(cache) == "1.8 GB"

    # Halving the element width halves it, which is what a runtime loading the
    # cache in q8 does. It is a parameter because this module cannot know.
    assert kv_cache_bytes(CODER, 32768, bytes_per_element=1) == cache // 2

    # And it grows exactly with the conversation.
    assert kv_cache_bytes(CODER, 8192) == cache // 4


def test_a_model_with_no_published_shape_has_no_measurable_cache():
    """Missing geometry is unknown, not zero.

    A cache of zero would make a 32k-context model look as cheap as a 1k one
    and quietly recommend something that will not load its own conversation.
    """
    assert kv_cache_bytes(Model(repo="x/y"), 4096) is None
    assert kv_cache_bytes(CODER, 0) is None
    assert kv_cache_bytes(CODER, -1) is None


def test_the_estimate_separates_weights_cache_and_overhead():
    """The components are reported apart because they are decided apart.

    A single total cannot answer the only question a reader has when a model
    does not fit, which is which of the three to give up. The cache is the one
    that shrinks with a shorter conversation, and it has to be visible as its
    own figure for that to be actionable.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    figures = estimate(CODER, variant, device(), context=4096)
    assert figures["weights"] == 4683074336, "the publisher's file size must be used as-is"
    assert figures["kv_cache"] == kv_cache_bytes(CODER, 4096)
    assert figures["overhead"] > 0, "runtime overhead must not be folded into the weights"
    assert figures["total"] == figures["weights"] + figures["kv_cache"] + figures["overhead"]

    # And a longer conversation is reported separately rather than assumed.
    long_context = estimate(CODER, variant, device(), context=32768)
    assert long_context["kv_cache"] > figures["kv_cache"]
    assert long_context["full_context_bytes"] == kv_cache_bytes(CODER, 32768)


def test_a_quantised_file_is_not_its_parameter_count_divided_by_eight():
    """Where a size is published it wins, and the arithmetic is a labelled fallback.

    Quantised weights carry per-block scales, tensors the format leaves in
    wider types, and metadata no bit count accounts for. The 4-bit build of
    this model is 4.36 GiB; a naive division would say 3.55 GiB, so the
    shortcut under-reports by nearly a gigabyte and would recommend builds that
    do not fit.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    naive = int(CODER.parameters * 4 / 8)
    assert variant.weight_bytes(CODER.parameters) == 4683074336
    assert naive < 4683074336, "this test needs the naive figure to be wrong"

    # With no published size, and a bit depth, the fallback is used instead of
    # refusing, and says so.
    unmeasured = Variant(name="b.gguf", quantization="Q4_K_M", size=0)
    assert unmeasured.weight_bytes(CODER.parameters) == naive
    figures = estimate(CODER, unmeasured, device())
    assert figures["estimates"]["weights"] is True, (
        "a size worked out from a bit depth must be reported as an estimate")


def test_a_model_that_cannot_be_estimated_is_unknown_rather_than_too_big():
    """No size and no bit depth is not a verdict.

    It is a different answer from "does not fit", and conflating them would
    hide a model whose metadata simply has not been read yet.
    """
    variant = Variant(name="a.gguf", quantization="", bits_per_weight=None, size=0)
    verdict = classify(Model(repo="x/y"), variant, device())
    assert verdict["result"] == UNKNOWN
    assert verdict["reason"], "every verdict must carry a reason"


def test_a_device_that_reports_no_memory_gets_no_verdict():
    """Nothing can be compared against nothing.

    Comparing against the machine's whole RAM would claim it fits models the
    operating system will not let it have.
    """
    blind = device(total_gb=0, available_gb=0)
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    assert classify(CODER, variant, blind)["result"] == UNKNOWN


def test_a_model_that_does_not_fit_says_so_and_by_how_much():
    """The shortfall is the number a reader can act on.

    The total is quoted rather than the weight file alone, because the total is
    what has to fit: a 4.4GB model asking for 5.1GB is the difference between
    fitting and not, and quoting only the file would hide it.
    """
    small = device(total_gb=4, available_gb=2, disk_gb=50)
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    verdict = classify(CODER, variant, small)
    assert verdict["result"] == NOT_RECOMMENDED
    assert "5.1 GB" in verdict["reason"], f"the reason omits the total: {verdict['reason']}"
    assert "1.8 GB" in verdict["reason"], f"the reason omits what there is: {verdict['reason']}"


def test_a_model_that_fits_with_room_is_likely_and_a_tight_one_is_not():
    """Headroom is what separates the two, and 15% is where it starts mattering.

    A model using almost everything available would run today and fail
    tomorrow, when something else is open. That is a different answer from one
    with room, and the verdict has to be able to say so.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    roomy = classify(CODER, variant, device(total_gb=32, available_gb=28))
    assert roomy["result"] == LIKELY

    # 6GB available cushions to 5.4GB, and the model at 4096 tokens needs
    # 5.1GB, so there is 0.3GB spare: five per cent of the budget.
    tight = classify(CODER, variant, device(total_gb=8, available_gb=6))
    assert tight["result"] == POSSIBLE, (
        f"a model using almost all available memory was called {tight['result']}")
    assert "spare" in tight["reason"]


def test_a_model_larger_than_the_graphics_memory_spills_and_is_flagged():
    """With a GPU, that memory is the real limit, not the system's.

    A 5.1GB model does fit on an 8GB card, so it is offered without complaint.
    The same model on a 4GB card does not fit there at all: it spills onto the
    CPU and runs slowly, which is a different trade and worth naming rather
    than hiding behind a verdict that only knows about system memory.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    roomy_card = classify(CODER, variant,
                          device(total_gb=64, available_gb=60, vram=8 * 1024 ** 3))
    assert roomy_card["result"] == LIKELY

    small_card = classify(CODER, variant,
                          device(total_gb=64, available_gb=60, vram=4 * 1024 ** 3))
    assert small_card["result"] == POSSIBLE
    assert "graphics" in small_card["reason"], small_card["reason"]

    # And with no card detected, the whole of system memory is on offer,
    # because a model too big for the card still runs, just slowly.
    without_card = classify(CODER, variant, device(total_gb=64, available_gb=60))
    assert without_card["result"] == LIKELY


def test_a_download_that_does_not_fit_on_the_disk_is_not_recommended():
    """Room to run it is not room to fetch it.

    The model itself fits comfortably, so without this check it would be
    recommended and the download would fail part way through, which is the
    worst place to discover it.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    verdict = classify(CODER, variant, device(total_gb=32, available_gb=28, disk_gb=1))
    assert verdict["result"] == NOT_RECOMMENDED
    assert "free" in verdict["reason"], verdict["reason"]


def test_a_long_conversation_can_overshoot_a_model_that_fits_short():
    """The case the whole component split exists for.

    The weights fit with 4GB to spare, and a 32k conversation needs 1.75GiB of
    cache on top. Reported at a short context this model is comfortable; the
    same model in a long conversation is not, and the tool has to be able to
    say both rather than only the flattering one.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    narrow = device(total_gb=8, available_gb=7, disk_gb=100)
    assert classify(CODER, variant, narrow, context=2048)["result"] == LIKELY
    assert classify(CODER, variant, narrow, context=32768)["result"] == NOT_RECOMMENDED


def test_fitting_the_arithmetic_is_never_reported_as_verified():
    """Nothing here can verify anything, so nothing here may claim to.

    There is no hardware test matrix and no empirical record in this build, so
    a model that fits by every figure this module holds is still only
    probably fine. The constant exists so a future matrix has somewhere to
    record real results and so an entry claiming verification is recognisable.
    """
    variant = Variant(name="a.gguf", quantization="Q4_K_M", size=4683074336)
    generous = device(total_gb=256, available_gb=250, vram=80 * 1024 ** 3, disk_gb=2000)
    assert classify(CODER, variant, generous)["result"] != VERIFIED
    assert VERIFIED, "the category must exist for a test matrix to record into"


def test_a_recommendation_prefers_the_usual_build_over_the_smallest_one():
    """Fitting is not the only thing a suggestion has to get right.

    Both the two-bit and the four-bit build of this model fit on this device,
    and the four-bit is the better answer by a long way. Recommending the
    two-bit because it is smaller would trade a real loss in output for memory
    the reader already has spare, which is the wrong default for the only
    question the tool exists to answer.
    """
    catalog = [CODER]
    rows = recommend(catalog, device(total_gb=32, available_gb=28, disk_gb=500),
                     context=4096)
    assert rows, "nothing was recommended"
    assert rows[0]["variant"].quantization == "Q4_K_M"
    assert rows[0]["variant"].size == 4683074336


def test_a_recommendation_degrades_towards_what_a_small_device_can_hold():
    """The same model, a smaller answer, rather than no answer at all.

    5GB available cushions to 4.5GB. The usual four-bit build is 4.36GB on
    disk and needs more than that once the runtime and a conversation are
    counted, so it is out; the four-bit legacy and the three-bit small fit.
    Recommending nothing here would be wrong: the model does run on this
    machine, just at a coarser precision.
    """
    small = device(total_gb=8, available_gb=5, disk_gb=20)
    rows = recommend([CODER], small, context=4096)
    assert rows, "a 5GB device should still be offered this model"
    assert rows[0]["variant"].quantization != "Q4_K_M", (
        f"it recommended the build that does not fit: {rows[0]['variant'].quantization}")
    assert rows[0]["variant"].size < 4683074336
    assert rows[0]["verdict"]["result"] in (POSSIBLE, LIKELY)


def test_a_device_with_too_little_memory_is_offered_nothing_rather_than_a_wrong_answer():
    """The smallest build of this model is 2.8GB, and 1.4GB will not hold it.

    Offering it anyway would send somebody to a download that cannot load.
    """
    tiny = device(total_gb=2, available_gb=1536 / 1024, disk_gb=8)
    assert recommend([CODER], tiny, context=4096) == []


def test_a_model_that_cannot_run_is_left_out_rather_than_ranked_last():
    """Not a lesser recommendation: not one.

    Showing it would invite a download that cannot be run on this device.
    """
    rows = recommend([CODER], device(total_gb=1, available_gb=512 / 1024, disk_gb=2))
    assert rows == []


def test_recommendations_can_be_narrowed_to_a_task():
    """Coding and writing are different searches."""
    catalog = [CODER, Model(repo="x/Writer", name="Writer", parameters=10 ** 9,
                           tasks=("writing",), variants=(
                               Variant(name="w.gguf", quantization="Q4_K_M", size=1_500_000_000),))]
    hardware = device(total_gb=32, available_gb=28, disk_gb=500)
    assert [row["model"].name for row in recommend(catalog, hardware, task="coding")] == ["Qwen2.5 Coder 7B"]
    assert [row["model"].name for row in recommend(catalog, hardware, task="writing")] == ["Writer"]


def test_commands_are_built_from_real_identifiers_and_are_all_or_nothing():
    """A command that would not run is worse than none.

    Both halves come from the catalogue: the repository that actually holds the
    file, which for a quantised build is the quantiser's and not the model's
    publisher, and the label on that file rather than a bit depth worked out
    here.
    """
    variant = Variant(name="Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
                      quantization="Q4_K_M", size=4683074336)
    commands = ollama_commands(CODER, variant)
    assert commands
    assert commands[0]["command"] == (
        "ollama run hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M")
    assert all(row["command"].startswith(("ollama run", "OLLAMA_", "ollama pull"))
               for row in commands), "an unverified flag was invented"

    # Nothing to build from, so nothing is offered rather than something guessed.
    nameless = Model(repo="x/y", artifact_repo="")
    assert ollama_commands(nameless, variant) == []
    unlabelled = Variant(name="a.gguf", quantization="")
    assert ollama_commands(CODER, unlabelled) == []


def test_a_catalogue_is_refused_when_it_cannot_be_trusted():
    """A catalogue that loads half-empty is worse than one that will not load.

    Entries that fail to parse would simply not be recommended and nothing
    would say they had gone, so the file is refused and the last good one kept.
    """
    good = {"version": CATALOG_VERSION, "models": [{"repo": "a/b", "parameters": 1}]}
    assert len(parse_catalog(good)) == 1

    for payload, expected in (
        ({"version": 99, "models": []}, "version"),
        ({"version": CATALOG_VERSION}, "model list"),
        ({"version": CATALOG_VERSION, "models": {}}, "model list"),
        ({"version": CATALOG_VERSION, "models": [{"parameters": 1}]}, "repo"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "nope"}]}, "repository id"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "parameters": "many"}]}, "parameters"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "context": -1}]}, "negative"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "tasks": ["teleport"]}]}, "does not know"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "variants": [{"size": 1}]}]}, "name"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "variants": [
            {"name": "v.gguf", "size": -1}]}]}, "negative size"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b", "verified": "yes"}]}, "verified"),
        ({"version": CATALOG_VERSION, "models": [{"repo": "a/b"}, {"repo": "a/b"}]}, "twice"),
        ("not a mapping", "not an object"),
    ):
        with pytest.raises(CatalogError) as caught:
            parse_catalog(payload)
        assert expected in str(caught.value), f"{payload!r} gave {caught.value}"


def test_a_malformed_catalogue_on_disk_says_what_was_wrong():
    """The failure a user can meet is a bad file, not a bad object."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        broken = Path(directory) / "catalog.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(CatalogError, match="valid JSON"):
            load_catalog(broken)
        with pytest.raises(CatalogError, match="could not be read"):
            load_catalog(Path(directory) / "absent.json")


def test_the_shipped_catalogue_is_valid_and_proclaims_nothing_it_cannot_know():
    """Checked at import time by every test, which is the point of shipping it.

    Every record must be attributed, dated, and carry no verification claim:
    the whole premise is that a fit computed on this machine is not evidence
    that anything ever ran here.
    """
    catalog = load_catalog()
    assert catalog, "the shipped catalogue is empty"
    for model in catalog:
        assert model.source, f"{model.repo} does not say where it came from"
        assert model.last_verified, f"{model.repo} is not dated"
        assert model.verified is False, (
            f"{model.repo} claims verification, and nothing in this build can support one")
        assert model.artifact_repo, f"{model.repo} does not say where its builds come from"
        assert model.license, f"{model.repo} carries no licence"


def test_every_shipped_number_is_traceable_to_a_publisher():
    """The figures are checked against what the Hub reported, not against each other.

    Each expectation here is a value read from the repository named beside it.
    If a catalogue entry is edited by hand, or a model is repointed at a
    different repository, one of these stops holding.
    """
    catalog = {model.repo: model for model in load_catalog()}
    coder = catalog["Qwen/Qwen2.5-Coder-7B-Instruct"]
    assert coder.parameters == 7615616512
    assert (coder.layers, coder.kv_heads, coder.head_dim, coder.context) == (28, 4, 128, 32768)
    assert coder.license == "apache-2.0"
    sizes = {variant.quantization: variant.size for variant in coder.variants}
    assert sizes["Q4_K_M"] == 4683074336
    assert sizes["f16"] == 15237853696
    assert sizes["Q2_K"] < sizes["Q4_K_M"] < sizes["f16"], (
        "a smaller quantisation cannot be a larger file")

    small = catalog["Qwen/Qwen2.5-0.5B-Instruct"]
    assert small.parameters == 494032768
    assert (small.layers, small.kv_heads, small.head_dim) == (24, 2, 64)
    assert small.artifact_repo.endswith("-GGUF")
    assert small.variants[0].size < 1024 ** 3


def test_detection_reports_what_it_read_and_what_it_did_not():
    """A number with no provenance is a guess wearing a number's clothes.

    Graphics memory cannot be read without a graphics library this app
    deliberately does not have, so it stays None and says so. Treating that as
    zero would hide a capable machine; filling it would invent a limit.
    """
    hardware = detect_hardware()
    assert hardware.vram is None, (
        "a VRAM figure was invented; it must stay unknown without a graphics library")
    assert hardware.memory_total > 0, "total memory should be readable"
    assert "not detected" in hardware.sources["vram"]
    assert hardware.sources["memory_total"] == "detected"
    assert "graphics memory not detected" in hardware.describe()


def test_detection_takes_a_path_object_like_the_resources_page_does():
    """A regression test for a bug that already happened once.

    psutil rejects os.PathLike on Windows, and the storage directory is a
    Path. The TypeError was swallowed by the guard and turned into a
    permanently blank disk reading.
    """
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        assert detect_hardware(Path(directory)).disk_free > 0


def test_a_device_with_unmeasurable_memory_is_offered_a_stingy_share():
    """Not the whole of its RAM.

    A device reporting no free memory is one whose free memory is not
    knowable, and offering all of it would claim it fits models the operating
    system will not let it have.
    """
    blind = device(total_gb=32, available_gb=0)
    assert blind.budget() == int(32 * 1024 ** 3 * 0.20)
    assert blind.budget() < 32 * 1024 ** 3


def test_available_memory_is_cushioned_and_total_is_not_assumed_free():
    """The margin is applied once, to what is actually available."""
    loaded = device(total_gb=32, available_gb=24)
    assert loaded.budget() == int(24 * 1024 ** 3 * 0.90)


def test_byte_counts_are_readable_at_every_scale():
    for value, expected in ((512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"),
                            (1024 ** 3, "1.0 GB"), (int(4.4 * 1024 ** 3), "4.4 GB")):
        assert human_bytes(value) == expected


def test_a_published_record_is_never_treated_as_a_verified_one():
    """Fetching metadata is not testing hardware.

    The brief is explicit that updating a model's details must not mark it as
    tested, so a record built from a live response carries no verification and
    the date it was read rather than the date it was run.
    """
    from desktop_app.cookbook import catalog_from_payload
    record = catalog_from_payload({"repo": "a/b", "name": "B"}, "2026-10-10")
    assert record["verified"] is False
    assert record["last_verified"] == "2026-10-10"
    model = parse_model(record, 0)
    assert model.verified is False
    assert classify(model, Variant(name="v.gguf", quantization="Q4_K_M", size=1024),
                    device(total_gb=64, available_gb=60))["result"] != VERIFIED


def test_the_catalogue_file_is_bundled_where_the_build_already_looks():
    """PyInstaller copies assets/ whole, so the catalogue ships unchanged.

    If this ever moves out of assets/ the packaged app would find no catalogue
    at all and show an empty page with no explanation.
    """
    from pathlib import Path
    import desktop_app.cookbook as cookbook
    assert "assets" in cookbook.CATALOG_PATH.parts, (
        f"the catalogue is at {cookbook.CATALOG_PATH}, outside the bundled assets tree")
    assert cookbook.CATALOG_PATH.is_file()
    json.loads(cookbook.CATALOG_PATH.read_text(encoding="utf-8"))