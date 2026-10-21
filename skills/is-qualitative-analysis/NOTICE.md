# Attribution and adaptation

This skill adapts the MIT-licensed `alterlab-qualitative-analysis` module from
[AlterLab-IEU/AlterLab-Academic-Skills](https://github.com/AlterLab-IEU/AlterLab-Academic-Skills),
commit `e4836c08a20da195a11f30f203a8cf23ec30aa95`.
Copyright (c) 2026 AlterLab Creative Technologies Laboratory.
The exact upstream MIT license is preserved in `LICENSE`. Source paths and
SHA-256 hashes are recorded in `UPSTREAM.json`.

OpenAI4S adaptations add Information Systems interview/case evidence handling,
local Artifact APIs and a validated, importable nominal-agreement helper.
The helper derives the nominal coincidence calculation and pairwise agreement
from upstream `scripts/icr.py`, validates matrix width and coder identities,
and reports constant-category kappa as undefined. The upstream command-line
runner, default unit bootstrap, dependencies and global threshold verdict are
not included. Guidance corrects the suggestion that agreement CIs demonstrate
human/model equivalence and the shorthand making consensus a requirement of
reflexive thematic analysis. No upstream noncommercial pipeline modules are
included. `origin: openai4s` denotes bundled distribution, not original authorship.

AlterLab's suite acknowledges its origins in K-Dense's scientific-agent-skills.
Its inherited MIT notice is retained below from upstream
`THIRD_PARTY_NOTICES.md`; this does not claim K-Dense authored this particular
AlterLab module.

```text
MIT License

Copyright (c) 2025 K-Dense Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
