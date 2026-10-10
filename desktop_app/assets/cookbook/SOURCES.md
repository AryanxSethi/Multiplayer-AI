# Cookbook model catalogue

`catalog.json` holds the models this build ships with, and the numbers in it
were read from the Hugging Face Hub rather than estimated.

## Where each field comes from

| Field | Source |
|---|---|
| `parameters` | `https://huggingface.co/api/models/<repo>` → `safetensors.parameters.total`. A count of the tensors the publisher actually shipped, not a name-derived guess. |
| `context` | `<repo>/raw/main/config.json` → `max_position_embeddings`. |
| `layers`, `kv_heads`, `head_dim` | `<repo>/raw/main/config.json` → `num_hidden_layers`, `num_key_value_heads`, and `hidden_size / num_attention_heads`. |
| `license`, `languages` | the same API call → `cardData`. |
| `variants[].size` | `https://huggingface.co/api/models/<artifact_repo>/tree/main` → each file's `size`. |
| `variants[].quantization` | the label in that same file name. |

## `repo` and `artifact_repo` are different repositories

`repo` is the model's publisher. `artifact_repo` is the community repository
holding the quantised builds, which is almost never the publisher's own:
quantisation is third-party work, and attributing those files to the publisher
would be a false provenance claim. Both are shown in the app, and every
generated command names the repository that actually holds the file.

The quantised builds currently listed are by **bartowski**, who publishes
under the same licence as the model they were made from. The licence recorded
on each record is the *model's* licence, taken from the publisher's own model
card; it is not a licence on the quantised file, which carries its own terms
in its repository.

## What is deliberately absent

**No entry is marked `verified`.** That field is in the format so a hardware
test matrix has somewhere to record real results, and so an entry that claims
to be verified can be recognised rather than quietly trusted. Nothing in this
catalogue has been run on any reader's hardware, so nothing claims to have
been. `classify()` cannot return `verified compatible` for the same reason.

**No download sizes are computed from a bit depth.** Where a publisher's own
file size is known it is used, because a 4-bit file is not the parameter count
divided by four: quantised weights carry per-block scales, tensors the format
leaves wider, and metadata no bit count accounts for.

**No benchmark scores, download links or capabilities are guessed.** Where the
Hub did not carry a field it is absent from the record rather than filled in.

**No gated model is listed.** A gated repository's `config.json` returns 401
without an accepted licence agreement, so its layer geometry cannot be read and
its KV cache cannot be worked out. `meta-llama/Llama-3.2-1B-Instruct` was
dropped for exactly this reason, despite being an obvious candidate.

**Phi-3.5 Mini records 4096 as its context, not 131072.** Its
`config.json` declares `max_position_embeddings: 131072` but also
`original_max_position_embeddings: 4096` and a LongRoPE scaling block. The
longer figure depends on a runtime applying that scaling correctly, and this
catalogue cannot tell whether one will. The native figure is recorded, so the
cache is costed for a conversation the model definitely supports.

## Widening the catalogue

Each model costs three reads and cannot be added for less:

1. `https://huggingface.co/api/models/<repo>` — parameter count, licence,
   languages, and whether it is gated at all.
2. `https://huggingface.co/<repo>/raw/main/config.json` — context, layers,
   key/value heads, and `hidden_size / num_attention_heads`.
3. `https://huggingface.co/api/models/<artifact_repo>/tree/main` — the real
   size of every downloadable build.

Step 3 is what makes a model recommendable. `best_variant()` walks variants, so
a record with no verified file size never appears in a recommendation, which is
correct — there would be nothing to download.

Every figure currently in `catalog.json` is asserted in
`tests/test_cookbook.py::test_every_shipped_number_is_traceable_to_a_publisher`,
so a hand edit or a repointed repository fails the suite rather than shipping.

## Keeping it current

The catalogue is a versioned data file under `desktop_app/assets/`, which
`MultiplayerAI.spec` already bundles whole, so a catalogue change needs no
build. It can be refreshed from the Hub at runtime — see the Refresh control
in the app, which is the only thing that causes a request to leave the
machine. If a refresh cannot be validated, the shipped catalogue is kept.