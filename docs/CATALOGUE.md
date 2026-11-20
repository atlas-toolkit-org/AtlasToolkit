# The model catalogue

The app's **Model catalogue** window offers ready-made models to try the mesh tools with (the user's side is in
[TOOLS.md](TOOLS.md#the-model-catalogue)). This page is for developers: what it is made of, how to add a model, and
how its files reach users.

## How it fits together

| Piece | What it does |
|---|---|
| `catalogue/recipes.toml` | The source of truth: one `[[model]]` block per model, and the categories |
| `catalogue/sources.lock.json` | Each source file's URL, page and SHA-256, pinned by `fetch` |
| `scripts/catalogue.py` | Builds everything from the recipes (below) |
| `src/atlas_toolkit/catalogue/index.json` | What the app reads: categories, models, credits, sizes, hashes (generated, committed) |
| `src/atlas_toolkit/catalogue/thumbs/<id>.png` | The cards' pictures, 384 px clay renders (generated, committed) |
| `src/atlas_toolkit/catalogue/models/<id>.ply.gz` | The starter set inside the app (gitignored; the build puts them there) |
| `src/atlas_toolkit/licenses/catalogue-models.txt` | Every model's credit, source, licence and changes (generated, committed) |
| `core/catalogue.py` | Loads the index; `ensure()` builds, unpacks or downloads a model into the catalogue folder |
| `geom/shapes.py` | The shapes made in code; `catalogue.py prepare` turns them into files like the scans |
| `geom/thumbnail.py` | The clay renderer: embree on the CPU, no OpenGL, deterministic |
| `ui/catalogue.py` | The window, and `pick_models()` for every entry point |

A model is one of two kinds:
- **bundled:** a `.ply.gz` in the app (the starter set, under 40 MB in all): every simple shape of Shapes & forms
  (`simple_shapes` in the recipes, which `check` enforces) and a few solid favourites;
- **remote:** a `.ply.gz` on the catalogue's GitHub release, downloaded on first use and checked against its SHA-256:
  everything else, the more complex shapes included.

Shapes made in code (`geom/shapes.py`, CC0) become files at build time like the scans, so nothing is computed on the
user's computer.

Every kind ends up as `<catalogue folder>/<Category>/<Name>.ply` (binary PLY, Z up, front toward -Y, credit and
`atlas-frame` in its header) with a `<Name>.txt` credit beside it. The folder is the `catalogue_dir` option, by default
`Documents/Atlas Toolkit/Catalogue` (`core/paths.catalogue_dir`).

## Adding a model

1. **Check that it suits Atlas.** Solid, readable forms that draw and sculpt well. Not: scans with scale bars,
   labels or display stands; models mostly made of thin parts (skeletons, wires, fine branches, hollow vessels);
   clutters of hundreds of loose pieces; fractal noise. `check` measures thinness: at most 40% of the surface may be
   thinner than 3% of the model's size.
2. **Check the licence.** Allowed: `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, and `LicenseRef-public-domain`
   (marked public domain at the source). Never NC (non-commercial), ND (no derivatives), CC BY-SA 3.0 or
   "research only" terms (the Stanford scans, for instance): the app is GPL-3.0 and anyone may sell it.
3. **Add a block** to `catalogue/recipes.toml`:

   ```toml
   [[model]]
   id = "venus-de-milo-bust"            # unique, lower-case, stable: it names the files and the release asset
   name = "Venus de Milo, bust"
   category = "busts"
   commons = "Venus (Afrodite) fra Milo - KAS434 1.stl"   # a Wikimedia Commons file...
   license = "CC0-1.0"                                     # ...whose licence Commons must agree with
   author = "Attributed to Alexandros of Antioch"
   made = "c. 150–125 BC"
   credit = "SMK – National Gallery of Denmark, The Royal Cast Collection (KAS434/1)"
   description = "Head and shoulders of the Aphrodite of Milos, from a plaster cast."
   changes = "cropped to the head and shoulders, the cut closed"
   tags = ["greek", "hellenistic", "face", "woman"]
   crop = { z = [0.70, 1.0] }          # keep this share of the height (after turning: z up, -y front)
   triangles = 260000                  # the most it keeps (quadric decimation)
   bundled = true                      # ship it in the app (keep that set small)
   ```

   Not on Commons: give `url` (a file or a `.zip` with `member`), `source_url` (the page that states the terms) and
   `license_evidence` (a quote of them). Made in code: `generator` and `params` (add it to `geom/shapes.py`).
   Turning: `up`/`front` name the source's axes (default: guessed as the tools do), `rotate = [x, y, z]` degrees
   turns it further. Light models: `subdivide = "smooth"` or `"flat"` refines them up to `triangles`.
   The thumbnail's camera: `view = { azimuth = -28, elevation = 14 }`.
   A lighter copy of a model already listed: `from = "<id>"` with its own `id`, `name`, `category` and `triangles`; it
   takes the source, licence, credits, turns and crop of that model (Light & quick holds them, 5,000 to 50,000
   triangles).
4. **Build it:** `.venv\Scripts\python scripts\catalogue.py all --only <id>` (fetch, prepare, thumbs, index, bundle,
   check). Then `sheet` writes contact sheets to `build/qa/catalogue/` to review the new card beside the others.
5. **Run** `tests/test_catalogue.py` and `tests/test_catalogue_ui.py`.
6. **Publish** a remote model's file (below) before the release that lists it.

Limits (`check` enforces them): 8,000 to 1.6 million triangles, at most 20 MB compressed per model, at most 40% of
the surface thinner than 3% of the model's size, and only the simple shapes bundled in Shapes & forms.

Every model ships as a **clean closed solid**, so every tool takes it as it is. `prepare` repairs each model after
decimating it (`geom/repair.py`, the Repair Mesh tool's repair without resampling): back-to-back faces and crumbs that
decimation leaves, holes left by cropping or scanning, non-manifold edges and flipped faces. `check` fails a model that is
still not a solid. A model that cannot be one says why in its recipe: `open = "non-orientable: …"` (the Klein bottle,
Boy's surface). Revision 2 rebuilt the 46 models revision 1 had shipped with such damage.

## Hosting

Remote models live on a GitHub release of the public repository, tag `catalogue-<revision>`, one `<id>.ply.gz` per
model; `index.json`'s `base_url` points there. `scripts/catalogue.py publish` uploads `build/catalogue/out/*.ply.gz`
(only when asked: it publishes). It is made a pre-release, so it never shows as the latest app version and GitHub's `releases/latest` and the app's update check skip it; never delete one an app still lists. Changing a model's file changes its hash: raise `revision` in `recipes.toml` (a new
tag) so older apps keep the files they know. The build (`scripts/build.py`) runs `catalogue.py bundle`, which takes the
starter set from `build/catalogue/out/` or else downloads it from the release, each file checked against the index.

Wikimedia asks scripts to identify themselves: `scripts/catalogue.py` sends a User-Agent with the project's address
and fetches one file at a time.
