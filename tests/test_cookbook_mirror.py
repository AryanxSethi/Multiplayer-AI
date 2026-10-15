"""Pointing the catalogue at a host other than the public one.

Two reasons this is worth having. A mirror the user controls means the public
host never sees the request at all, which is the only way to avoid that
rather than to obfuscate it -- there is no way to hide an address from a
server you are connecting to, since it needs it to send the reply. And an
internal or offline mirror is the only thing that works on a network where
the public one is unreachable.

Everything else about a record is unchanged by where it came from: same three
paths, same validation, same refusal rules. Only the host differs.
"""

import httpx
import pytest

from desktop_app.cookbook_hub import (DEFAULT_BASE, HubError, check_base,
                                      fetch_entry, fetch_model,
                                      fetch_variants)


def transport(pages):
    def handle(request):
        url = str(request.url)
        for fragment in sorted(pages, key=len, reverse=True):
            if fragment in url:
                return httpx.Response(200, json=pages[fragment])
        return httpx.Response(404, json={"error": "not found"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


MODEL = {"id": "Qwen/Qwen2.5-0.5B-Instruct", "gated": False,
         "cardData": {"license": "apache-2.0", "language": ["en"]},
         "safetensors": {"total": 494032768}}
CONFIG = {"hidden_size": 896, "max_position_embeddings": 32768,
          "num_attention_heads": 14, "num_hidden_layers": 24, "num_key_value_heads": 2}
TREE = [{"path": "m-Q4_K_M.gguf", "size": 397808192}]


def test_the_default_is_the_public_catalogue_and_an_absent_choice_stays_there() -> None:
    """A user who has never chosen a host gets the default, not an error.

    Absent and empty both mean unset. Treating an empty string as a bad host
    would make a settings file with the key present but blank unusable, and
    that is the shape an unset preference usually takes.
    """
    assert check_base(None) == DEFAULT_BASE
    assert check_base("") == DEFAULT_BASE
    assert check_base("   ") == DEFAULT_BASE
    assert check_base(DEFAULT_BASE) == DEFAULT_BASE
    assert DEFAULT_BASE.startswith("https://")


def test_a_mirror_is_only_accepted_as_an_https_origin() -> None:
    """No path, no query, no fragment, no plain HTTP.

    The three request paths are appended to the base, so a base carrying one
    of its own builds a URL nothing serves, and a query or fragment would be
    dropped silently and leave a reader unable to tell whether it was honoured.
    Plain HTTP is refused for the same reason every other remote address in
    this app refuses it: a mirror is still somewhere metadata is read from, and
    it is read to decide what to download.
    """
    for good in ("https://models.internal", "https://localhost:8443",
                 "https://mirror.example:9443"):
        assert check_base(good) == good

    for bad in ("http://models.internal", "ftp://x", "models.internal",
                "https://models.internal/catalogue", "https://models.internal?a=b",
                "https://models.internal#frag", "https://models.internal/../etc",
                "wss://models.internal", 42, [], "https://user:pw@models.internal"):
        with pytest.raises(ValueError, match="https:// host"):
            check_base(bad)


async def test_a_record_is_read_from_the_chosen_host_and_nowhere_else() -> None:
    """The three paths are built on the mirror, not appended to the public one.

    A mirror that served a different catalogue would otherwise be ignored and
    the public one read instead, which is the opposite of the point and
    invisible to the reader.
    """
    seen = []

    def handle(request):
        seen.append(str(request.url))
        url = str(request.url)
        for fragment, payload in (("raw/main/config.json", CONFIG),
                                  ("/tree/main", TREE),
                                  ("api/models/", MODEL)):
            if fragment in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        record = await fetch_entry(client, "Qwen/Qwen2.5-0.5B-Instruct",
                                   "someone/Builds-GGUF", "https://models.internal")

    assert seen, "nothing was requested at all"
    assert all(url.startswith("https://models.internal/") for url in seen), (
        f"a request went somewhere other than the mirror: {seen}")
    assert record["parameters"] == 494032768
    assert record["artifact_repo"] == "someone/Builds-GGUF"
    assert record["variants"][0]["size"] == 397808192



async def test_no_host_means_the_public_catalogue() -> None:
    """And the default is what a caller that says nothing gets."""
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=MODEL)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await fetch_model(client, "Qwen/Qwen2.5-0.5B-Instruct")

    assert all(url.startswith(DEFAULT_BASE) for url in seen), seen


async def test_a_mirror_cannot_smuggle_an_unreadable_repository_in() -> None:
    """The repository check is the same whichever host is being asked.

    Pointing at a mirror must not relax what an identifier may contain, since
    a mirror is still a host the app is building URLs for.
    """
    with pytest.raises(ValueError):
        await fetch_model(transport({"api/models/": MODEL}), "../etc/passwd",
                          "https://models.internal")


async def test_a_mirror_that_serves_nothing_says_so_rather_than_being_ignored() -> None:
    """A wrong host is an error, not a silent fallback to the public one.

    Falling back would be worse: the reader would believe they were reading
    their mirror while every record came from somewhere else.
    """
    async with transport({}) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_model(client, "Qwen/Qwen2.5-0.5B-Instruct", "https://models.internal")


async def test_a_gated_model_is_refused_wherever_it_is_read_from() -> None:
    """The refusal is about the model, not about where the metadata came from."""
    async with transport({"api/models/": {**MODEL, "gated": "manual"}}) as client:
        with pytest.raises(HubError, match="gated"):
            await fetch_entry(client, "Qwen/Qwen2.5-0.5B-Instruct",
                              "someone/Builds-GGUF", "https://models.internal")


async def test_a_host_that_cannot_be_reached_is_reported_where_the_request_was_made() -> None:
    """So a reader can tell a mirror problem from a catalogue one."""
    async def handle(request):
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ConnectError):
            await fetch_variants(client, "someone/Builds-GGUF", "https://models.internal")