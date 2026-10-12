"""Reading model details from the Hugging Face Hub, when a person asks for it.

Kept apart from ``cookbook`` and from the dialog so that the one thing in this
feature that reaches the network can be read on its own and tested with a
mock transport, and so the arithmetic never has to know a request happened.

Nothing here runs unless the Cookbook's Refresh control calls it. That is a
design constraint rather than an accident: the Resources page tells the user
that this app's readings are sent nowhere, and a catalogue that refreshed on
open would make that untrue while the page still said it.

The responses are read for specific fields and nothing else. A payload is not
merged into the catalogue wholesale, because a Hub response can grow new keys
and anything unrecognised would then reach a recommendation without having
been looked at. A field the response does not carry is left out rather than
defaulted: an absent context length would otherwise become a key/value cache
figure, and an invented one is wrong by a factor of several while still looking
precise.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

MODEL_URL = "{base}/api/models/{repo}"
CONFIG_URL = "{base}/{repo}/raw/main/config.json"
TREE_URL = "{base}/api/models/{repo}/tree/main"

# Where model metadata is read from. A mirror serves the same three paths, so
# pointing this somewhere else is a matter of the host alone and nothing about
# how a record is fetched or checked changes.
DEFAULT_BASE = "https://huggingface.co"

# A repository identifier is ``publisher/name`` and nothing else. Checked
# before it goes anywhere near a URL, so a catalogue entry cannot send the
# request to a host of its choosing.
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}")

# A base is an origin and nothing more: scheme, host, optional port. No path,
# because the three paths above are appended to it and a base carrying its own
# would produce a URL nothing serves. Plain HTTP is refused for a remote
# mirror, on the same grounds as every other remote address in this app.
BASE = re.compile(r"https://[A-Za-z0-9][A-Za-z0-9.-]{0,253}(?::\d{1,5})?")

TIMEOUT = 15


def check_repo(repo):
    """Return the identifier if it is safe to put in a URL, or raise."""
    if not isinstance(repo, str) or not REPO.fullmatch(repo.strip()):
        raise ValueError("That is not a publisher/name repository id.")
    return repo.strip()


def check_base(base):
    """Return a catalogue host, or raise.

    ``https`` and an origin, nothing else. No path, because the request
    paths are appended to this and a base carrying one would build a URL
    nothing serves; no query or fragment, which would be dropped silently and
    leave a reader unsure whether they were honoured. Plain HTTP is refused
    for the same reason every other remote address in this app refuses it: a
    mirror is still somewhere the model metadata is read from, and it is read
    to decide what a person downloads.
    """
    if base is None or (isinstance(base, str) and not base.strip()):
        return DEFAULT_BASE
    if not isinstance(base, str) or not BASE.fullmatch(base.strip()):
        raise ValueError("That is not an https:// host for a model catalogue.")
    return base.strip()


def _urls(repo, base):
    """The three addresses for one repository, at the chosen host."""
    host = check_base(base)
    return (MODEL_URL.format(base=host, repo=repo),
            CONFIG_URL.format(base=host, repo=repo),
            TREE_URL.format(base=host, repo=repo))


def _number(value):
    """A count, or zero. Never a guess and never a string."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _languages(value):
    """A card's languages as one per entry, however the host spelled it.

    ``language`` is a list on most cards and a bare string on some, and
    ``tuple("en")`` is ``('e', 'n')`` -- a language count that matches neither
    the record nor anything a reader would recognise. A string is therefore one
    language, and an iterable keeps only its strings, so a card carrying
    ``["en", 7, None]`` does not put two non-languages in the catalogue.
    """
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


async def fetch_model(client, repo, base=None):
    """One model's published details, as the Cookbook can check them.

    Args:
        client: An ``httpx.AsyncClient`` carrying this request, so it follows
            whatever routing and TLS policy the app already applies.
        repo: A ``publisher/name`` identifier.
        base: Host to read from. Defaults to the public catalogue; a mirror
            serving the same paths works the same way.

    Returns:
        A dict holding only the fields that were actually present, plus
        ``fetched_at`` recording when they were read. That is a reading date
        and not a testing date: nothing here has been run anywhere.
    """
    repo = check_repo(repo)
    model_url, config_url, _ = _urls(repo, base)
    payload = await _get(client, model_url)
    card = payload.get("cardData") or {}
    weights = payload.get("safetensors") or {}
    result = {
        "repo": repo,
        "name": repo.split("/")[-1],
        "publisher": repo.split("/")[0],
        "license": card.get("license") or "",
        "languages": _languages(card.get("language")),
        "parameters": _number(weights.get("total")),
        "gated": payload.get("gated") or False,
    }
    geometry = await _geometry(client, config_url)
    result.update(geometry)
    result["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return result


async def _geometry(client, config_url):
    """Layer shape and context length, where the publisher publishes them.

    A gated repository answers 403 here without an accepted agreement. That is
    reported rather than worked around: without these four numbers the cache
    cannot be costed, and a record missing them would recommend a model on
    weights alone while looking as complete as one that had them.
    """
    try:
        config = await _get(client, config_url)
    except httpx.HTTPStatusError:
        # A 403 is a gated repository and a 404 is a repository that does not
        # hold a config. Neither is readable, and both are reported as the
        # geometry being absent rather than as a failure: what is unknown is
        # unknown, and the caller decides whether that is fatal.
        return {}
    if not isinstance(config, dict):
        return {}
    heads = _number(config.get("num_attention_heads"))
    hidden = _number(config.get("hidden_size"))
    context = _number(config.get("original_max_position_embeddings")) or \
        _number(config.get("max_position_embeddings"))
    result = {
        "context": context,
        "layers": _number(config.get("num_hidden_layers")),
        "kv_heads": _number(config.get("num_key_value_heads")),
    }
    # A published head_dim is taken as given: the model says what its attention
    # head is, and deriving it instead can divide evenly by accident and be
    # wrong anyway. Only when the publisher does not carry one is it worked out
    # from the two fields that are.
    #
    # Both have to be present for the derivation, and the division has to come
    # out whole: guessing the remainder is how a cache estimate ends up
    # confidently wrong.
    published = _number(config.get("head_dim"))
    if published > 0:
        result["head_dim"] = published
    else:
        result["head_dim"] = (hidden // heads
                              if heads and hidden and hidden % heads == 0
                              else 0)
    return result


async def _get(client, url):
    response = await client.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


async def fetch_variants(client, repo, base=None):
    """Every downloadable build in a repository, with its real size."""
    repo = check_repo(repo)
    _, _, tree_url = _urls(repo, base)
    files = await _get(client, tree_url)
    variants = []
    if not isinstance(files, list):
        return variants
    from .cookbook import bits_from_label
    for entry in files:
        path = entry.get("path") if isinstance(entry, dict) else None
        size = entry.get("size") if isinstance(entry, dict) else None
        if not path or not path.endswith(".gguf") or not isinstance(size, int):
            continue
        if path.endswith(".imatrix"):
            continue
        label = path[:-len(".gguf")].rsplit("-", 1)[-1]
        variants.append({"name": path, "quantization": label,
                         "bits_per_weight": bits_from_label(label), "size": size})
    return variants


class HubError(RuntimeError):
    """A request that did not produce usable metadata."""


async def fetch_entry(client, repo, artifact_repo=None, base=None):
    """A catalogue record for one model, ready to be checked by the engine.

    Returns the record and the builds together, because a model whose build
    list came back empty is not a model anybody can act on: there would be
    nothing to download. It is raised as an error rather than returned empty,
    so the caller can say which model had none instead of silently listing
    fewer.
    """
    model = await fetch_model(client, repo, base)
    if model.get("gated"):
        raise HubError(f"{repo} is gated, so its details cannot be read without "
                       f"accepting that publisher's agreement.")
    record = {key: value for key, value in model.items() if key != "fetched_at"}
    if artifact_repo:
        record["artifact_repo"] = check_repo(artifact_repo)
        variants = await fetch_variants(client, artifact_repo, base)
        if not variants:
            raise HubError(f"{artifact_repo} holds no downloadable builds, so "
                           f"{repo} has nothing that could be run.")
        record["variants"] = variants
    record["source"] = "Hugging Face"
    record["last_verified"] = model["fetched_at"]
    # Stated rather than left to a default. Reading metadata is not testing
    # hardware, and the field is the whole difference between a record that
    # says so and one that merely has not been asked.
    record["verified"] = False
    return record