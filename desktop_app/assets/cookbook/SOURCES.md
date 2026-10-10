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

## Keeping it current

The catalogue is a versioned data file under `desktop_app/assets/`, which
`MultiplayerAI.spec` already bundles whole, so a catalogue change needs no
build. It can be refreshed from the Hub at runtime — see the Refresh control
in the app, which is the only thing that causes a request to leave the
machine. If a refresh cannot be validated, the shipped catalogue is kept.