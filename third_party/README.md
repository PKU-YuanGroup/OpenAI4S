# third_party

[中文说明](README_zh.md)

Source trees OpenAI4S pins as git submodules. They are not part of the `openai4s` wheel and the core never needs them.

| Entry | What it is |
| --- | --- |
| [`OpenMHS`](OpenMHS) | [OpenMHS](https://mhs.openai4s.org), the open-source Model Hardware Standard: the SDK (`MHS/sdk/src`, import `mhs`), the OpenAI4S reference host (`integration/openai4s`, import `openai4s_mhs`) and the device market (`MHS/drivers`, 746 packages). Pinned to the release this OpenAI4S version is tested with. `openai4s/mhs_lab/openmhs.py` finds it automatically, so no `PYTHONPATH` is needed. |

Fetch it with `git clone --recursive` or, in an existing checkout, `git submodule update --init third_party/OpenMHS`.
Installations without the submodule run `openai4s lab setup`, which clones the same pinned release into
`~/.openai4s/openmhs`. See [docs/openmhs.md](../docs/openmhs.md).
