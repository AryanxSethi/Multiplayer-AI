"""What can run on this machine, and how much of it there is room for.

Kept apart from the widgets, for the same reason ``resources`` is: the whole
of this module is arithmetic over published metadata, and none of it should
need a display to be checked. Everything here is a pure function of its
arguments except :func:`detect_hardware`, which reads the system once.

Two rules shape the code, and both come from the feature's own brief.

**Nothing here is a measurement of this app running a model.** It is arithmetic
over a model's published weights and a device's published capacity. That is
enough to say a model *probably* fits and never enough to say it was tested,
so :func:`classify` cannot return ``VERIFIED``: no local catalogue entry or
remote metadata feed carries evidence that a model ran on this hardware, and
inventing one is the specific failure the brief warns about. ``VERIFIED``
exists as a constant because the catalogue format accepts it, so a future
hardware test matrix has somewhere to record it, and so that an entry claiming
it can be recognised rather than silently trusted.

**Every number is attributed.** A parameter count read from a publisher's
metadata is exact and says so. A KV cache worked out from a layer count and a
context length is an estimate and says so too, and carries the terms it used.
The distinction is the product: a user deciding what to download can act on an
estimate, and cannot act on a number pretending to be something it is not.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

import psutil

CATALOG_VERSION = 1

CATALOG_PATH = Path(__file__).resolve().parent / "assets" / "cookbook" / "catalog.json"

# How a result is meant to be read, worst to best. The order is load-bearing:
# ranking sorts on it, so a worse result never outranks a better one.
UNKNOWN = "unknown"
NOT_RECOMMENDED = "not recommended"
POSSIBLE = "possible with compromises"
LIKELY = "likely to run"
VERIFIED = "verified compatible"

ORDER = (VERIFIED, LIKELY, POSSIBLE, NOT_RECOMMENDED, UNKNOWN)

EXPLANATIONS = {
    VERIFIED: "This exact model and precision have been run on matching hardware.",
    LIKELY: "The estimate fits with room to spare.",
    POSSIBLE: "It fits only if something is given up.",
    NOT_RECOMMENDED: "It does not fit, or the runtime cannot run it.",
    UNKNOWN: "There is not enough published information to say.",
}

# What a running model costs on top of its weights. The engine is llama.cpp's
# shape, which is what the bundled provider speaks, and the figure covers the
# runtime's own allocations: the tokenizer, the graph, the scratch buffers the
# allocator keeps, and the slack left by fragmentation. It is a constant
# rather than a measurement because nothing here can measure one.
#
# Two boundaries it must never cross: it must not be folded into the weights,
# because that would make a device look tighter than it is and turn "runs" into
# "does not"; and it must not be omitted, because that would make every
# estimate optimistic by this much, which is the direction that strands someone
# mid-download.
RUNTIME_OVERHEAD_BYTES = 512 * 1024 * 1024

# Held back from what the device reports. System RAM is never all available to
# one process: the kernel keeps some, the compositor and drivers take more, and
# the page cache is not free to reclaim under pressure. Ten per cent is a
# conventional allowance and, like the overhead above, errs towards not
# promising.
MEMORY_MARGIN = 0.10

# Context is expensive and almost never needed at full length, so a model is
# judged at a length a person would actually hold, and the cost of a longer
# conversation is reported separately rather than baked into the verdict.
DEFAULT_CONTEXT = 4096

TASKS = {
    "coding": "Coding",
    "debugging": "Debugging",
    "reasoning": "Reasoning",
    "research": "Research",
    "writing": "Writing",
    "vision": "Image understanding",
    "general": "General chat",
}

# Verdicts a model can still be suggested on. "not recommended" and "unknown"
# are excluded, because a model this device cannot run is not a lesser
# recommendation, it is not one.
FITTING = (VERIFIED, LIKELY, POSSIBLE)

# Which build to suggest first, best expected answer first. Q4_K_M leads
# because it is the community's default and Ollama's own: it picks Q4_K_M when
# a repository publishes one. Q8_0 and f16 are above it in quality and far
# above it in size, so they sit below anything a reader is likely to have room
# for, and the tight two- and three-bit builds come last because they are a
# fallback for a device with little memory rather than a first suggestion.
#
# This is a preference order, not a quality ranking, and nothing here has
# measured the difference between two of these on any benchmark.
PREFERRED_VARIANTS = ("Q4_K_M", "Q5_K_M", "Q4_0", "Q6_K", "Q3_K_S",
                      "IQ4_XS", "Q8_0", "IQ3_M", "Q3_K_M", "Q2_K")


class CatalogError(ValueError):
    """A catalogue that cannot be trusted, with the reason it cannot."""


@dataclass(frozen=True)
class Hardware:
    """What this device can offer, and how much of that was measured.

    Every measurement carries its origin, because a value typed by a user and a
    value read from the system are not equally trustworthy and the rest of the
    module needs to be able to say which it is holding. ``None`` means
    genuinely unknown rather than zero: a device that reports no graphics memory
    is a device whose graphics memory was not detected, and treating the two
    the same would either hide a capable machine or invent a limit.

    Attributes:
        platform: Operating system and version, as reported.
        architecture: Machine architecture, e.g. ``AMD64``.
        cpu_cores: Logical cores, used for headless CPU inference estimates.
        memory_total: Total system memory in bytes.
        memory_available: Memory not already in use, in bytes.
        disk_free: Free space where models are stored, in bytes.
        vram: Dedicated graphics memory in bytes, or ``None`` if not detected.
        unified_memory: True on Apple Silicon and similar, where system and
            graphics memory are one pool and a separate VRAM figure is wrong.
    """

    platform: str = ""
    architecture: str = ""
    cpu_cores: int = 0
    memory_total: int = 0
    memory_available: int = 0
    disk_free: int = 0
    vram: int | None = None
    unified_memory: bool = False
    sources: dict = field(default_factory=dict)

    def budget(self):
        """Memory a model may claim, in bytes.

        Available memory less the margin, or a fifth of total memory when
        availability was not measured. The fallback is deliberately stingy:
        a device reporting no free memory is a device whose free memory is
        not knowable, and offering the whole of its RAM would claim it fits
        models that the operating system will not actually let it have.
        """
        if self.memory_available > 0:
            return int(self.memory_available * (1 - MEMORY_MARGIN))
        return int(self.memory_total * 0.20)

    def describe(self):
        """One line a person can read, saying what is known and what is not."""
        parts = []
        if self.memory_total:
            parts.append(f"{human_bytes(self.memory_total)} memory")
        if self.vram:
            parts.append(f"{human_bytes(self.vram)} graphics memory")
        elif self.unified_memory:
            parts.append("unified memory")
        else:
            parts.append("graphics memory not detected")
        if self.cpu_cores:
            parts.append(f"{self.cpu_cores} cores")
        return " · ".join(parts)


@dataclass(frozen=True)
class Variant:
    """One downloadable artifact of a model at one precision.

    Sizes are the publisher's own, taken from what the repository actually
    holds rather than computed from a bit depth, because the two differ: a
    4-bit file is not the parameter count divided by four. Quantised weights
    carry per-block scales and zero points, tensors the format leaves in wider
    types, and metadata no bit count accounts for.

    Attributes:
        name: Publisher's file stem, used verbatim in commands so nothing has
            to be constructed from a guess.
        quantization: Format label taken from the filename, e.g. ``Q4_K_M``.
        bits_per_weight: Effective bits per weight, derived from the label.
        size: Download and on-disk size in bytes.
        quant_source: Where ``size`` came from, e.g. ``publisher tree``.
    """

    name: str
    quantization: str = ""
    bits_per_weight: float | None = None
    size: int = 0
    quant_source: str = "publisher"

    def weight_bytes(self, parameters):
        """Weight memory for this variant, in bytes.

        ``None`` when neither a size nor a usable bit depth is known. An
        estimate derived from a bit depth is a different number from the file,
        so the file is preferred wherever there is one and the arithmetic is
        only a fallback, labelled as such by the caller. The bit depth falls
        back to the quantisation label, since a record built by hand rarely
        carries both and a caller who named a format has named a depth.
        """
        if self.size:
            return self.size
        bits = self.bits_per_weight or bits_from_label(self.quantization or self.name)
        if bits and parameters:
            return int(parameters * bits / 8)
        return None


@dataclass(frozen=True)
class Model:
    """A model as its publisher describes it.

    Carries the provenance every field needs, because ``cookBook.md`` is
    explicit that metadata must be attributed and dated and that a catalogue
    update must not mark anything as tested. ``verified`` is therefore part of
    the record rather than inferred from arithmetic, and nothing in this module
    sets it.

    Attributes:
        repo: Publisher's repository id, used verbatim for every request and
            command so no identifier is ever assembled from parts.
        artifact_repo: Repository the downloadable quantised builds come
            from, which is usually *not* the publisher's own. Quantisation is
            done by third parties, and attributing their files to the model
            publisher would be a false provenance claim, so the two are kept
            apart and both are shown.
        name: Display name.
        publisher: Who publishes it.
        parameters: Exact parameter count from the publisher's index.
        context: Maximum context length in tokens.
        layers: Transformer layer count, for the KV cache.
        kv_heads: Key/value head count, for the KV cache.
        head_dim: Dimension per attention head.
        license: SPDX identifier where the publisher states one.
        languages: Language tags the publisher declares.
        vision: Whether it accepts images.
        variants: Downloadable artifacts, largest first.
        tasks: Task tags, which must be keys of :data:`TASKS`.
        source: Where this record came from.
        verified: Whether real hardware testing backs it. Always False here.
        last_verified: When it was last checked against its publisher.
    """

    repo: str
    artifact_repo: str = ""
    name: str = ""
    publisher: str = ""
    parameters: int = 0
    context: int = 0
    layers: int = 0
    kv_heads: int = 0
    head_dim: int = 0
    license: str = ""
    languages: tuple = ()
    vision: bool = False
    variants: tuple = ()
    tasks: tuple = ()
    source: str = "seed catalogue"
    verified: bool = False
    last_verified: str = ""

    @property
    def display(self):
        """How the model is named on screen."""
        return self.name or self.repo.split("/")[-1]

    @property
    def size_class(self):
        """Rough scale, for a heading: tiny, small, medium, large, huge."""
        if not self.parameters:
            return "unknown size"
        billions = self.parameters / 1e9
        if billions < 1.5:
            return "under 1.5B"
        if billions < 4:
            return f"{billions:.1f}B"
        if billions < 11:
            return f"{billions:.0f}B"
        if billions < 40:
            return f"{billions:.0f}B"
        return f"{billions:.0f}B"

    def variants_for(self, bits=None):
        """Artifacts matching a bit depth, or all of them when none is given.

        Sorted smallest first, because a person opening a catalog at this
        point is usually trying to fit something and the smallest artifact that
        does the job is the answer.
        """
        chosen = [variant for variant in self.variants
                  if bits is None or (variant.bits_per_weight or 0) == bits]
        return sorted(chosen, key=lambda variant: variant.size or 0)


def human_bytes(value):
    """A byte count as something a person can size a download against."""
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024 or unit == "PB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def bits_from_label(label):
    """Effective bits per weight from a quantisation label, or ``None``.

    The dominant number is the bit depth, which is what the labels name, and
    is close enough to the truth for a memory budget. It is not a quality
    ranking: ``Q4_K_M`` and ``Q4_0`` are both four bits and are not equally
    good, and nothing here claims to know which is which.
    """
    if not label:
        return None
    digits = ""
    for char in str(label).upper():
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return None
    return float(digits)


def detect_hardware(disk_path=None):
    """Read this device once and describe what it can offer.

    No new dependency and no subprocess. CPU model name, dedicated graphics
    memory and Apple Silicon unified-memory sizing are all unreadable without
    something this app deliberately does not have, so they are reported as
    unknown rather than guessed: a wrong VRAM figure produces confident,
    wrong recommendations, which is worse than an honest blank.

    Each measurement is recorded in ``sources`` so the interface can show
    which figures were read and which were not, rather than presenting a row
    of numbers that look equally solid.
    """
    sources = {}
    hardware = {
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "architecture": platform.machine(),
    }
    sources["platform"] = "detected"
    sources["architecture"] = "detected"

    try:
        cores = psutil.cpu_count(logical=True) or 0
        hardware["cpu_cores"] = int(cores)
        sources["cpu_cores"] = "detected"
    except Exception:
        hardware["cpu_cores"] = 0
        sources["cpu_cores"] = "unknown"

    try:
        memory = psutil.virtual_memory()
        hardware["memory_total"] = int(memory.total)
        hardware["memory_available"] = int(memory.available)
        sources["memory_total"] = "detected"
        sources["memory_available"] = "detected"
    except Exception:
        hardware["memory_total"] = 0
        hardware["memory_available"] = 0
        sources["memory_total"] = "unknown"
        sources["memory_available"] = "unknown"

    # Coerced to str, for the same reason the Resources page does it:
    # psutil.disk_usage rejects os.PathLike on Windows and the storage
    # directory is a Path, which raised a TypeError that the surrounding
    # guard turned into a permanently unavailable reading.
    try:
        target = str(disk_path) if disk_path else os.path.abspath(os.sep)
        if not os.path.isdir(target):
            target = os.path.abspath(os.sep)
        hardware["disk_free"] = int(psutil.disk_usage(target).free)
        sources["disk_free"] = "detected"
    except Exception:
        hardware["disk_free"] = 0
        sources["disk_free"] = "unknown"

    unified = platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")
    hardware["unified_memory"] = unified
    hardware["vram"] = None
    sources["vram"] = "not detected without a graphics library"
    return Hardware(sources=sources, **hardware)


def kv_cache_bytes(model, context, bytes_per_element=2):
    """Key/value cache for a conversation of ``context`` tokens.

    Every generated token keeps one key and one value per layer, and those are
    the only tensors that grow with the conversation. The figure is exact
    arithmetic on the model's own layer count, head count and head dimension,
    so it is an estimate of the cache and not of the model: it says how much
    room the conversation takes, and nothing about how good the answer will be.

    ``2`` is the element width, f16 or bf16. It is the default because it is
    what both are, and it is a parameter because a runtime loading the cache
    in q8 halves it and one leaving it in f32 doubles it, and this module does
    not know which the reader's runtime will do.
    """
    if not (model.layers and model.kv_heads and model.head_dim and context > 0):
        return None
    per_token = 2 * model.layers * model.kv_heads * model.head_dim * bytes_per_element
    return per_token * int(context)


def estimate(model, variant, hardware, context=DEFAULT_CONTEXT):
    """What one artifact would cost on this device, as separate components.

    Returns ``None`` when the model is too poorly described to estimate at all,
    which is a different answer from "too big". The components are kept apart
    deliberately: the brief asks for weights, cache, runtime overhead and
    margin to be separable, and a single total would hide which of them is
    what makes a model not fit.

    The margin is reported but never applied here. It is applied once, in
    :func:`classify`, against the device's budget, so that a reader can see
    the uncushioned figure and decide for themselves how much slack they want.
    """
    weights = variant.weight_bytes(model.parameters) if variant else None
    if weights is None:
        return None
    cache = kv_cache_bytes(model, context)
    overhead = RUNTIME_OVERHEAD_BYTES if variant else 0
    total = weights + (cache or 0) + overhead
    return {
        "weights": weights,
        "kv_cache": cache,
        "overhead": overhead,
        "total": total,
        "context": context,
        "full_context_bytes": kv_cache_bytes(model, model.context) if model.context else None,
        "estimates": {"weights": variant.size == 0, "kv_cache": True},
    }


def classify(model, variant, hardware, context=DEFAULT_CONTEXT):
    """How well one artifact suits one device, and the reason why.

    The categories are ordered and the first that applies wins, so the order
    of the checks is the definition of the verdict. Nothing here returns
    :data:`VERIFIED`: fitting the arithmetic is the most this module can
    establish, and a device that has not run the model has not verified it.

    Every path returns a reason naming the term that decided it, because a
    verdict without one is a verdict the reader has to take on trust, and a
    reader who cannot check a number will not act on it.
    """
    figures = estimate(model, variant, hardware, context)
    if figures is None:
        return {"result": UNKNOWN,
                "reason": "This model has no published size, so its memory use cannot be estimated."}
    if not hardware.memory_total:
        return {"result": UNKNOWN,
                "reason": "This device's memory could not be read, so nothing can be compared against it."}

    total = figures["total"]
    budget = hardware.budget()

    if hardware.vram:
        accelerator = hardware.vram
        place = "graphics memory"
    else:
        accelerator = budget
        place = "system memory"

    if total > budget:
        return {"result": NOT_RECOMMENDED,
                "reason": (f"It needs about {human_bytes(total)} and this device has "
                           f"{human_bytes(budget)} of {place} to give.")}

    if variant and variant.size and hardware.disk_free and variant.size > hardware.disk_free:
        return {"result": NOT_RECOMMENDED,
                "reason": (f"The download is {human_bytes(variant.size)} and there is "
                           f"{human_bytes(hardware.disk_free)} free.")}

    if variant and total > accelerator:
        return {"result": POSSIBLE,
                "reason": (f"It needs about {human_bytes(total)}, over the "
                           f"{human_bytes(accelerator)} of {place}, so it would spill onto "
                           f"the CPU and run slowly.")}

    headroom = budget - total
    if headroom < budget * 0.15:
        return {"result": POSSIBLE,
                "reason": (f"It fits, but only {human_bytes(headroom)} to spare, which is "
                           f"little for anything else running.")}

    if accelerator > budget:
        return {"result": LIKELY,
                "reason": (f"It needs about {human_bytes(total)} and fits in this device's "
                           f"{human_bytes(budget)} of {place}, with {human_bytes(headroom)} "
                           f"to spare.")}

    return {"result": LIKELY,
            "reason": (f"It needs about {human_bytes(total)} against {human_bytes(budget)} of "
                       f"{place}, with {human_bytes(headroom)} to spare.")}


def best_variant(model, hardware, context=DEFAULT_CONTEXT):
    """The variant to suggest, which is a balance and not merely the smallest.

    Taken down a preference list rather than up from the smallest file, because
    "fits" is not the only thing a recommendation has to get right. Offering
    the two-bit build of a model when the four-bit one also fits gives a
    noticeably worse answer in exchange for memory the reader already has, and
    the whole point of asking is to be told which one to use.

    The order starts at the community's usual default, which is also Ollama's
    own: it picks ``Q4_K_M`` when a repository has one. Below that sit the
    higher precisions for a device with room, and the tighter ones for a device
    without, so the suggestion degrades towards what actually fits rather than
    running out of options and returning nothing.

    Quantisation is not ranked by quality here and no such ranking is claimed.
    This is an order of preference for *what to try first*, chosen because it
    is a convention rather than because it was measured.
    """
    ranked = sorted(model.variants, key=lambda item: item.size or 0)
    for wanted in PREFERRED_VARIANTS:
        for variant in ranked:
            if (variant.quantization or "").upper() == wanted:
                if classify(model, variant, hardware, context)["result"] in FITTING:
                    return variant
    for variant in ranked:
        if classify(model, variant, hardware, context)["result"] in FITTING:
            return variant
    return None


def recommend(catalog, hardware, task=None, context=DEFAULT_CONTEXT, limit=None):
    """Models worth this device's attention, best first.

    Ranked on fit and then on capability, never on parameter count alone: a
    bigger model that does not run is not a better recommendation, and the
    brief is explicit about not ranking by size, popularity or benchmark
    score. A model that fits with room to spare outranks one that barely fits,
    and a model that cannot run is left out entirely rather than shown as
    something to consider.
    """
    scored = []
    for model in catalog:
        if task and task not in model.tasks:
            continue
        variant = best_variant(model, hardware, context)
        if variant is None:
            continue
        verdict = classify(model, variant, hardware, context)
        scored.append((ORDER.index(verdict["result"]), -model.parameters, model, variant, verdict))
    scored.sort(key=lambda row: (row[0], row[1]))
    rows = [{"model": model, "variant": variant, "verdict": verdict}
            for _, _, model, variant, verdict in scored]
    return rows[:limit] if limit else rows


def ollama_commands(model, variant):
    """Commands that fetch and run one artifact, built from real identifiers.

    Ollama can pull a GGUF straight off the Hub and name the precision as a
    tag, so no Modelfile and no invented flag are needed:

        ollama run hf.co/<repository>:<quantization>

    Both halves are the publisher's own strings. The repository is the one
    holding the file, which for a quantised build is the quantiser's and not
    the model's publisher, and the tag is the label on that file rather than a
    bit depth worked out here. If either is missing, no command is produced:
    a command that would not run is worse than none, because a user cannot
    tell which of the two they are looking at.
    """
    if not (model.artifact_repo and variant.quantization):
        return []
    tag = f"hf.co/{model.artifact_repo}:{variant.quantization}"
    return [
        {"purpose": f"Download and run the {variant.quantization} build",
         "command": f"ollama run {tag}"},
        {"purpose": "Give it a longer memory, at more cost",
         "command": f"OLLAMA_CONTEXT_LENGTH=8192 ollama run {tag}"},
        {"purpose": "Download it without starting it",
         "command": f"ollama pull {tag}"},
    ]


def _require(mapping, key, kind, where):
    if key not in mapping:
        raise CatalogError(f"{where} is missing {key!r}")
    value = mapping[key]
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise CatalogError(f"{where} has {key!r} as {type(value).__name__}, not {kind.__name__}")
    return value


def parse_model(record, index):
    """One catalogue record, validated.

    Validation is strict and the error says which record and which field,
    because a catalogue that loads with half its entries missing is worse than
    one that refuses: the missing entries would simply not be recommended,
    and nothing would say they had gone.
    """
    where = f"model {index}"
    if not isinstance(record, dict):
        raise CatalogError(f"{where} is not an object")
    repo = _require(record, "repo", str, where)
    where = repo
    if repo.count("/") != 1:
        raise CatalogError(f"{where!r} is not a publisher/name repository id")

    numbers = ("parameters", "context", "layers", "kv_heads", "head_dim")
    values = {}
    for key in numbers:
        raw = record.get(key, 0) or 0
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CatalogError(f"{where} has {key} as {type(raw).__name__}, not a number")
        if raw < 0:
            raise CatalogError(f"{where} has a negative {key}")
        values[key] = int(raw)

    tasks = record.get("tasks") or ()
    if not isinstance(tasks, (list, tuple)):
        raise CatalogError(f"{where} has tasks as {type(tasks).__name__}, not a list")
    unknown = [task for task in tasks if task not in TASKS]
    if unknown:
        raise CatalogError(f"{where} claims tasks this build does not know: {unknown!r}")

    variants = []
    for position, item in enumerate(record.get("variants") or ()):
        if not isinstance(item, dict):
            raise CatalogError(f"{where} variant {position} is not an object")
        name = _require(item, "name", str, f"{where} variant {position}")
        label = item.get("quantization") or bits_from_label(name)
        size = int(item.get("size", 0) or 0)
        if size < 0:
            raise CatalogError(f"{where} variant {name} has a negative size")
        variants.append(Variant(
            name=name,
            quantization=str(label or ""),
            bits_per_weight=item.get("bits_per_weight") or bits_from_label(label or name),
            size=size,
            quant_source=str(item.get("source") or "publisher")))

    verified = record.get("verified", False)
    if not isinstance(verified, bool):
        raise CatalogError(f"{where} has verified as {type(verified).__name__}, not true or false")

    return Model(
        repo=repo, artifact_repo=str(record.get("artifact_repo") or repo),
        name=str(record.get("name") or ""), publisher=str(record.get("publisher") or ""),
        parameters=values["parameters"], context=values["context"], layers=values["layers"],
        kv_heads=values["kv_heads"], head_dim=values["head_dim"],
        license=str(record.get("license") or ""),
        languages=tuple(record.get("languages") or ()), vision=bool(record.get("vision", False)),
        variants=tuple(variants), tasks=tuple(tasks),
        source=str(record.get("source") or "seed catalogue"), verified=verified,
        last_verified=str(record.get("last_verified") or ""))


def parse_catalog(payload):
    """A whole catalogue, validated and version-checked.

    An unrecognised version is refused rather than guessed at. A future format
    may renumber fields or change what a size means, and reading it as if it
    were this one would produce confident nonsense, which is the exact failure
    the brief asks to avoid. Refusing leaves the last good catalogue in place.
    """
    if not isinstance(payload, dict):
        raise CatalogError("The catalogue is not an object")
    version = payload.get("version")
    if version != CATALOG_VERSION:
        raise CatalogError(f"This build reads catalogue version {CATALOG_VERSION}, not {version!r}")
    models = payload.get("models")
    if not isinstance(models, list):
        raise CatalogError("The catalogue has no model list")
    seen = set()
    parsed = []
    for index, record in enumerate(models):
        model = parse_model(record, index)
        if model.repo in seen:
            raise CatalogError(f"{model.repo} is listed twice")
        seen.add(model.repo)
        parsed.append(model)
    return parsed


def load_catalog(path=None):
    """Read and validate the catalogue shipped with the app.

    Falls back to nothing rather than to a half-parsed file. A catalogue that
    will not load is a page with nothing on it and a clear reason, which is
    recoverable; a catalogue that loads with entries quietly dropped is a page
    that is quietly wrong.
    """
    target = Path(path) if path else CATALOG_PATH
    try:
        return parse_catalog(json.loads(target.read_text(encoding="utf-8")))
    except OSError as error:
        raise CatalogError(f"The catalogue could not be read: {error}") from error
    except json.JSONDecodeError as error:
        raise CatalogError(f"The catalogue is not valid JSON: {error}") from error


def catalog_from_payload(payload, fetched_at):
    """Turn metadata fetched from a publisher into a catalogue record.

    Only fields the response actually carries are used, and a field it does
    not carry is left at zero rather than filled with a plausible number. That
    matters more here than anywhere else in the app: an invented context
    length becomes a KV cache estimate, and an invented one is confidently
    wrong by a factor of several.
    """
    record = dict(payload)
    record.setdefault("source", "Hugging Face")
    record["last_verified"] = fetched_at
    record.setdefault("verified", False)
    return record


def rank_key(row):
    """Sort key for a recommendation row, exposed for tests and callers."""
    return ORDER.index(row["verdict"]["result"])