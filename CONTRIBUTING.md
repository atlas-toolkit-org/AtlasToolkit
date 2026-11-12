# Contributing to Atlas Toolkit

Thank you for helping. Bug reports, fixes, new tools and documentation are all welcome.

## Licence of contributions

Atlas Toolkit is GPL-3.0-or-later. You keep the copyright of what you contribute, and you license it under the
project's licence (inbound = outbound).

Sign off every commit to certify the [Developer Certificate of Origin](https://developercertificate.org/), which says
you have the right to submit the work under that licence:

```bash
git commit -s -m "Fix the stipple seed on reload"
```

That adds `Signed-off-by: Your Name <you@example.com>` to the commit message. A pull request whose commits are not
signed off cannot be merged.

Do not submit code, models or images you may not share under the GPL. Third-party code needs a licence compatible
with GPL-3.0, and its notice goes in `THIRD_PARTY_NOTICES.md`.

## Before you open a pull request

- Read `docs/ARCHITECTURE.md`, and `docs/ADDING_A_TOOL.md` for a new tool. A tool is a folder in
  `src/atlas_toolkit/tools/`. `core/` and `ui/` never special-case a tool.
- Set up the environment with `python scripts/setup_env.py`, then run the tests:
  ```bash
  .venv/bin/python -m unittest discover -s tests
  ```
  (`.venv\Scripts\python` on Windows.)
- Lint with `ruff check .`. Do **not** run `ruff format`: the project keeps its own compact style.
- Bump the tool's `version` when its behaviour, outputs or defaults change, and add a line to `CHANGELOG.md`.
- Outputs, tests and scripts write only to temporary folders or `build/qa/`, never to a user's folders.

## Presets

The app ships no presets, and presets are never code. Never put a tool's preset values in the source, tests or docs:
use the made-up packs in `tests/fixtures/presets/`. Your own presets are welcome as `.atlaspreset` files shared
outside this repository. See `docs/PRESETS.md`.

## Conduct and security

- Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Report security problems privately, as [SECURITY.md](SECURITY.md) explains, not in a public issue.
