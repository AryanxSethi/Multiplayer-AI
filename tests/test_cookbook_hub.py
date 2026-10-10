"""Reading the Hub, without the Hub.

Every test here runs against a mock transport, so the suite never depends on
Hugging Face being up, on a rate limit, or on a model continuing to exist.
That is deliberate rather than a convenience: the point of the feature is that
it works offline from a bundled catalogue, so a test that needed the network to
pass would be testing the opposite of what ships.

The shapes used below are copied from real responses. None of the figures are
invented for a test to look tidy -- the parameter count and the file sizes are
the ones the Hub reported, so a parser change that breaks on real payloads
breaks here too.
"""

import httpx
import pytest

from desktop_app.cookbook import CatalogError, parse_model
from desktop_app.cookbook_hub import (HubError, check_repo, fetch_entry,
                                      fetch_model, fetch_variants)

CODER_MODEL = {
    "id": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "gated": False,
    "cardData": {"license": "apache-2.0", "language": ["en"]},
    "safetensors": {"parameters": {"BF16": 7615616512}, "total": 7615616512},
}

CODER_CONFIG = {
    "architectures": ["Qwen2ForCausalLM"],
    "hidden_size": 3584, "max_position_embeddings": 32768,
    "num_attention_heads": 28, "num_hidden_layers": 28, "num_key_value_heads": 4,
}

CODER_TREE = [
    {"path": "README.md", "size": 10722},
    {"path": "Qwen2.5-Coder-7B-Instruct.imatrix", "size": 4536678},
    {"path": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", "size": 4683074336},
    {"path": "Qwen2.5-Coder-7B-Instruct-Q8_0.gguf", "size": 8098525984},
    {"path": "Qwen2.5-Coder-7B-Instruct-f16.gguf", "size": 15237853696},
]


def transport(pages, status=200):
    """A client that answers from a mapping of URL fragment to payload.

    The longest matching fragment wins, because the file tree's URL also
    begins ``/api/models/`` and a first-match lookup would answer a tree
    request with a model's metadata.
    """
    def handle(request):
        url = str(request.url)
        for fragment in sorted(pages, key=len, reverse=True):
            if fragment in url:
                payload = pages[fragment]
                if isinstance(payload, tuple):
                    return httpx.Response(payload[1], text=payload[0])
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"error": "not found"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_a_repository_id_is_checked_before_it_reaches_a_url():
    """A catalogue entry must not be able to redirect a request elsewhere.

    The identifier goes straight into a URL, so anything beyond
    ``publisher/name`` -- a host, a path, a query -- would be a way to send
    this app's request to a server of the catalogue's choosing.
    """
    assert check_repo("Qwen/Qwen2.5-0.5B-Instruct") == "Qwen/Qwen2.5-0.5B-Instruct"
    for bad in ("no-slash", "a/b/c", "", "  ", "../../etc/passwd",
                "https://evil.example/x", "a/b?x=1", "a/b#f", "a/b\nc",
                "a/b/../../c", None, 42):
        with pytest.raises(ValueError):
            check_repo(bad)


async def test_a_fetched_model_reads_only_the_fields_it_understands():
    """The response is not merged in wholesale.

    A Hub payload can grow new keys, and merging it wholesale would put
    anything unrecognised straight into a recommendation without anyone having
    looked at it. So only named fields are taken, and the numbers are the ones
    the Hub reported rather than tidy substitutes.
    """
    async with transport({"api/models/Qwen/": {**CODER_MODEL, "surprise": {"nested": 1}},
                          "raw/main/config.json": CODER_CONFIG}) as client:
        result = await fetch_model(client, "Qwen/Qwen2.5-Coder-7B-Instruct")

    assert result["parameters"] == 7615616512
    assert result["license"] == "apache-2.0"
    assert result["languages"] == ("en",)
    assert result["context"] == 32768
    assert result["layers"] == 28
    assert result["kv_heads"] == 4
    assert result["head_dim"] == 128, "hidden_size over attention heads, not a guess"
    assert "surprise" not in result, "an unrecognised key was carried into a record"
    assert result["gated"] is False
    assert result["fetched_at"].endswith("UTC"), "the reading was not dated"


async def test_the_head_dimension_is_not_guessed_when_the_division_is_not_exact():
    """A remainder means the assumption behind the figure does not hold.

    Deriving head_dim from hidden_size / num_attention_heads is right for the
    models it was checked against. Where it does not divide evenly the two
    figures describe something other than a plain dense transformer, and
    rounding would produce a cache estimate that is wrong by an unknown amount.
    """
    async with transport({"api/models/Qwen/": CODER_MODEL,
                          "raw/main/config.json": {**CODER_CONFIG, "hidden_size": 3583}}) as client:
        result = await fetch_model(client, "Qwen/Qwen2.5-Coder-7B-Instruct")
    assert result["head_dim"] == 0, (
        "a head dimension was invented from a division that does not divide")


async def test_a_missing_config_leaves_the_geometry_out_rather_than_zeroed():
    """Absent is not the same as zero.

    A zero context would be read as a model with no conversation at all, and a
    zero layer count as one that needs no cache. Leaving the keys out is what
    lets the catalogue say the figure is unknown.
    """
    async with transport({"api/models/Qwen/": CODER_MODEL}) as client:
        result = await fetch_model(client, "Qwen/Qwen2.5-Coder-7B-Instruct")
    assert "context" not in result
    assert "layers" not in result


async def test_a_long_context_needs_scaling_and_is_recorded_at_its_native_length():
    """Phi-3.5 advertises 131072 and natively supports 4096.

    Its config carries both numbers and a scaling block. The native one is
    taken, because the longer figure only applies if a runtime applies that
    scaling and this cannot tell whether one will.
    """
    async with transport({
        "api/models/microsoft/": {"id": "microsoft/Phi-3.5-mini-instruct", "gated": False,
                        "cardData": {"license": "mit", "language": ["multilingual"]},
                        "safetensors": {"total": 3821079552}},
        "raw/main/config.json": {"hidden_size": 3072, "original_max_position_embeddings": 4096,
                                 "max_position_embeddings": 131072, "num_attention_heads": 32,
                                 "num_hidden_layers": 32, "num_key_value_heads": 32,
                                 "rope_scaling": {"type": "longrope"}}}) as client:
        result = await fetch_model(client, "microsoft/Phi-3.5-mini-instruct")
    assert result["context"] == 4096, (
        "the extended context was recorded as though no scaling were needed")


async def test_builds_are_read_with_their_real_sizes_and_nothing_else():
    """Only GGUF weights become variants.

    An importance matrix is a calibration artefact, not a model, and the
    README is neither. Listing one as a downloadable build would offer a user
    a file that cannot be run.
    """
    async with transport({"Qwen2.5-Coder-7B-Instruct-GGUF/tree": CODER_TREE}) as client:
        variants = await fetch_variants(client, "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF")

    assert [item["name"] for item in variants] == [
        "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "Qwen2.5-Coder-7B-Instruct-Q8_0.gguf",
        "Qwen2.5-Coder-7B-Instruct-f16.gguf"]
    assert variants[0]["size"] == 4683074336
    assert variants[0]["quantization"] == "Q4_K_M"
    assert variants[0]["bits_per_weight"] == 4.0
    assert variants[-1]["quantization"] == "f16", (
        "a format with no digits in its label should keep its own name")


async def test_a_gated_model_is_refused_rather_than_half_read():
    """Its details cannot be read, so it cannot be costed.

    A record built from half a response would look like any other and be
    recommended on its weights alone, with no cache figure and no way for a
    reader to tell that is why.
    """
    async with transport({"api/models/Qwen/": {**CODER_MODEL, "gated": "manual"}}) as client:
        with pytest.raises(HubError, match="gated"):
            await fetch_entry(client, "Qwen/Qwen2.5-Coder-7B-Instruct",
                              "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF")


async def test_a_model_with_no_downloadable_build_is_reported_rather_than_listed():
    """There would be nothing to run, so it is not a recommendation.

    Returning an empty build list quietly would put a model on screen that
    cannot be downloaded, which is the failure this catalogue is meant to
    prevent.
    """
    async with transport({"api/models/Qwen/": CODER_MODEL,
                          "raw/main/config.json": CODER_CONFIG,
                          "Qwen2.5-Coder-7B-Instruct-GGUF/tree": [{"path": "README.md", "size": 100}]}) as client:
        with pytest.raises(HubError, match="nothing that could be run"):
            await fetch_entry(client, "Qwen/Qwen2.5-Coder-7B-Instruct",
                              "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF")


async def test_a_refreshed_record_passes_the_catalogue_s_own_checks():
    """The network is not a way around validation.

    A record read from a response goes through the same parser as one read
    from disk, so a Hub that started reporting a task this build has never
    heard of would be refused rather than shown.
    """
    async with transport({"api/models/Qwen/": CODER_MODEL,
                          "raw/main/config.json": CODER_CONFIG,
                          "Qwen2.5-Coder-7B-Instruct-GGUF/tree": CODER_TREE}) as client:
        record = await fetch_entry(client, "Qwen/Qwen2.5-Coder-7B-Instruct",
                                   "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF")
    assert parse_model(record, 0).parameters == 7615616512
    assert record["verified"] is False, (
        "reading metadata marked a model as tested")
    assert record["last_verified"], "the reading was not dated"


async def test_a_request_that_fails_says_so_rather_than_returning_nothing():
    """A refused request must not look like an empty catalogue."""
    async def handler(request):
        return httpx.Response(429, json={"error": "rate limited"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPError):
            await fetch_model(client, "Qwen/Qwen2.5-Coder-7B-Instruct")