"""Add models to the shipped Cookbook catalogue, reading every figure from the Hub.

The catalogue is four models because adding one costs three reads and a person
has to do them:

    https://huggingface.co/api/models/<repo>                  parameter count,
                                                             licence, languages,
                                                             whether it is gated
    https://huggingface.co/<repo>/raw/main/config.json        context, layers,
                                                             key/value heads
    https://huggingface.co/api/models/<artifact_repo>/tree/main  the real size of
                                                             every downloadable build

That is why it is four. It is not a limit in the code: nothing caps the
catalogue, the page paginates, and the Refresh control already re-reads a
catalogue at runtime. It is only that nobody had a way to do the three reads
without hand-editing JSON, and inventing figures was never an option -- a
confidently wrong memory number is worse than a catalogue that stops at four.

This does the reads, through the same validated path the app uses at runtime,
so a model added here has been through exactly the checks a refreshed one has.
A record that fails them is reported and skipped, never written.

What it cannot read from the Hub is what the model is *for*. "coding" and
"writing" are judgements, not published facts, so they are asked for in the
input file rather than guessed.

Usage:

    python scripts/grow_catalogue.py candidates.json [--dry-run]

``candidates.json`` is a list of objects:

    [
      {"repo": "Qwen/Qwen2.5-7B-Instruct",
       "artifact_repo": "bartowski/Qwen2.5-7B-Instruct-GGUF",
       "tasks": ["coding", "reasoning", "writing"],
       "name": "Qwen2.5 7B"}
    ]

``name`` is optional and defaults to the repository's own. ``tasks`` is
required and must be names this build knows; an unknown task is refused rather
than stored, because a filter nobody can select is not data.

Run it with --dry-run first: it prints what it would add without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desktop_app.cookbook import (CATALOG_PATH, CATALOG_VERSION, TASKS,  # noqa: E402
                                  load_catalog, model_record, parse_model)

DEFAULT_BASE = "https://huggingface.co"


def read_candidates(path):
    """The models to add, checked for the fields only a person can supply.

    Everything else -- sizes, geometry, parameter counts -- comes from the Hub,
    because that is the whole point of the script.
    """
    # utf-8-sig, because this is a file a person is expected to edit. Notepad
    # and PowerShell both write a byte-order mark on Windows, and a strict
    # utf-8 read refuses the whole file over three invisible bytes -- which
    # would make the tool fail on the machine it is meant to run on rather
    # than on anything about the models.
    entries = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list):
        raise ValueError("the candidate file must hold a list of models")

    checked = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"candidate {index} is not an object")
        for field in ("repo", "artifact_repo", "tasks"):
            if not entry.get(field):
                raise ValueError(f"candidate {index} has no {field}")
        unknown = sorted(set(entry["tasks"]) - set(TASKS))
        if unknown:
            raise ValueError(
                f"candidate {index} ({entry['repo']}) names tasks this build "
                f"does not know: {unknown}. Known: {sorted(TASKS)}")
        checked.append({
            "repo": entry["repo"].strip(),
            "artifact_repo": entry["artifact_repo"].strip(),
            "tasks": list(entry["tasks"]),
            "name": (entry.get("name") or "").strip(),
        })
    return checked


async def fetch_candidates(entries, base=DEFAULT_BASE):
    """One entry per model that could be read, and one line per failure.

    Failures are collected rather than raised so one unreachable repository
    does not abandon the rest of the run -- which matters when the input is a
    list of twenty and the point is to widen the catalogue.
    """
    import httpx

    from desktop_app.cookbook_hub import fetch_entry

    records, failures = [], []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for entry in entries:
            try:
                record = await fetch_entry(client, entry["repo"],
                                           entry["artifact_repo"], base)
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                # Anything at all: a 404, a 403, a gated repository, a network
                # failure. The message is what the person needs to decide
                # whether to retry, so it is kept whole.
                failures.append((entry["repo"],
                                 f"{type(error).__name__}: {error}"))
                continue
            record["tasks"] = list(entry["tasks"])
            if entry["name"]:
                record["name"] = entry["name"]
            records.append(record)
    return records, failures


def build_catalogue(existing, incoming, updated):
    """The new catalogue: models already there kept, additions merged in.

    Re-read entries replace what was there for the same repository, because a
    refreshed figure is better than the one it supersedes. Order is stable --
    what was already shipped keeps its place, and additions go on the end --
    so a diff shows one model's figures changing rather than the whole file
    reordering.
    """
    by_repo = {model.repo: model for model in existing}
    added, replaced = [], []

    for record in incoming:
        model = parse_model(record, len(by_repo))
        previous = by_repo.get(model.repo)
        if previous is not None:
            replaced.append(model.repo)
        else:
            added.append(model.repo)
        by_repo[model.repo] = model

    return ([by_repo[model.repo] for model in existing] +
            [by_repo[repo] for repo in added]), added, replaced


def serialise(models, updated, previous_note=""):
    """The file as it will be written, in the shape the app reads.

    Round-tripped through ``parse_catalog`` before it is returned, for the same
    reason ``save_catalog`` does it: the only way to be sure a file that
    validates is a file that can be loaded is to build the text and read it
    back. A catalogue that fails to parse is one the app silently discards on
    the next launch, taking every figure in it with it.
    """
    payload = {
        "version": CATALOG_VERSION,
        "updated": updated,
        "note": (previous_note or "").strip() or
                "Every number here was read from the Hugging Face Hub on the "
                "date shown. Nothing is estimated and nothing is maintained by "
                "hand.",
        "models": [model_record(model) for model in models],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    from desktop_app.cookbook import parse_catalog
    parse_catalog(json.loads(text))
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("candidates", help="JSON file listing the models to add")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help="catalogue host to read from")
    parser.add_argument("--today", default="",
                        help="the date to record as read (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    try:
        entries = read_candidates(args.candidates)
    except (OSError, ValueError) as error:
        print(f"Cannot read the candidate file: {error}", file=sys.stderr)
        return 2
    if not entries:
        print("No candidates listed.")
        return 0

    existing = load_catalog()
    known = {model.repo for model in existing}
    wanted = [entry for entry in entries if entry["repo"] not in known]
    print(f"{len(existing)} models shipped, {len(wanted)} to read.")
    if not wanted:
        print("Every candidate is already in the catalogue.")
        return 0

    records, failures = asyncio.run(fetch_candidates(wanted, args.base))
    for repo, reason in failures:
        print(f"  refused {repo}: {reason}", file=sys.stderr)
    if not records:
        print("Nothing could be read, so nothing was written.")
        return 1

    models, added, replaced = build_catalogue(existing, records,
                                              args.today or "")
    current = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    text = serialise(models, current.get("updated", ""), current.get("note", ""))

    print(f"read     : {len(records)} of {len(wanted)}")
    print(f"added    : {', '.join(added) or 'none'}")
    print(f"replaced : {', '.join(replaced) or 'none'}")
    print(f"catalogue: {len(models)} models")
    for model in models:
        if model.repo in added:
            builds = ", ".join(
                f"{variant.quantization} {variant.size / 1073741826:.2f}GB"
                for variant in model.variants[:3])
            print(f"  + {model.repo} — {len(model.variants)} builds "
                  f"({builds}, ...)")

    if args.dry_run:
        print("\nDry run. Nothing was written.")
        return 0
    if failures:
        print("\nSome candidates were refused. Nothing was written, so a "
              "half-finished catalogue cannot ship. Re-run once they are "
              "fixed, or drop them from the file.")
        return 1

    from desktop_app.storage import write_json_durably
    write_json_durably(CATALOG_PATH, text)
    print(f"\nWrote {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())