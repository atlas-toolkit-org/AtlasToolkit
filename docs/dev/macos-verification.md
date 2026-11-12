# Verifying the Mac build by hand

CI builds and tests the Mac app on GitHub's machines, which have no GPU, no display and no person in front of them. This checklist covers what that leaves unproven. It needs a Mac with Apple Silicon and macOS 15 or later: a rented cloud Mac for a few hours covers everything except the trackpad.

When you finish, record the result in `docs/PLATFORMS.md`: the date, the Mac model and macOS version, and what passed or failed. A failure is useful: it tells us what to fix before calling macOS fully supported.

## Before you start

Get the DMG from a `full-ci` run (the `installers-macos` artifact, kept for three days) or build it:

```bash
python3 scripts/setup_env.py
.venv/bin/python scripts/package.py
```

Download it through a web browser if you can, not `curl` or `scp`. Only a browser marks the file as downloaded from the internet (the quarantine flag), and Gatekeeper's behaviour is part of what you are testing.

## 1. Installing and the first start

Follow `docs/install/macos.md` exactly, as a new user would.

- [ ] The DMG opens with the app and an Applications link side by side.
- [ ] Dragging the app to Applications works.
- [ ] macOS refuses the first start, and **Open Anyway** appears in **System Settings > Privacy & Security**.
- [ ] After **Open Anyway**, the app starts, and later starts need no extra steps.
- [ ] `codesign --verify --deep --strict "/Applications/Atlas Toolkit.app"` reports nothing.

## 2. The 3D views on a real GPU

This is the biggest unknown. The check needs nothing but Python:

```bash
python3 scripts/check_frozen_gui.py --exe "/Applications/Atlas Toolkit.app/Contents/MacOS/Atlas Toolkit"
```

- [ ] It reports `ok frozen 3D view` and a colour count above 64.

From a source checkout, this also reports the GPU and every shader:

```bash
.venv/bin/python scripts/dev/gl_probe.py
```

- [ ] The renderer names the Apple GPU (not "Apple Software Renderer"), no shaders fail, and the result is `rendered`.
- [ ] If the default context fails, run it again with `--profile core41` and record both results. That decides the fix.

In the app, open a mesh in **Twist** or **Plane Cut**:

- [ ] The model is shaded and sits on its grid.
- [ ] Orbit, pan and zoom are smooth.

## 3. Retina

On a built-in Retina display, and on an external non-Retina one if available:

- [ ] Text and icons are sharp.
- [ ] The 3D view fills its pane: not a quarter of it, not offset.
- [ ] In **Plane Cut** and **Raster DEM to Mesh > Placement**, handles grab where you click. Scaling bugs show up as handles that respond some distance away from the pointer.
- [ ] Moving the window between displays keeps all of this right.

## 4. Trackpad

Using the trackpad, without a mouse, in **Twist** and **Plane Cut**:

- [ ] A pinch zooms.
- [ ] A two-finger swipe orbits.
- [ ] Shift with a two-finger swipe pans.
- [ ] The direction feels natural. Record it if it feels reversed.

## 5. Finder and the Dock

- [ ] Double-clicking an `.atlasflow` file opens it as a workflow, both with the app closed and with it running.
- [ ] **Open With** on an SVG or STL lists Atlas Toolkit, and it opens in a tool that takes it.
- [ ] Dropping a file on the Dock icon opens it.
- [ ] While a heavy job runs (Remesh, or a Workflow), no second Dock icon appears.
- [ ] Quitting during a job leaves nothing running: check **Activity Monitor** for `atlas-` and `Atlas Toolkit`.

## 6. The Mac's own conventions

- [ ] The menu bar has **About**, **Settings**, **Close Window** and **Quit** in the usual places.
- [ ] Tooltips show ⌘ and not Ctrl. ⌘K, ⌘, and ⌘Q work.
- [ ] Light and dark themes both look right.

## 7. Other programs

If Blender is installed in Applications:

- [ ] **Setup** finds it, and **Melt** runs.
- [ ] **Locate** accepts choosing `Blender.app` itself.

For GPU depth estimation, install the runtime from **Setup**, then start the app with the GPU enabled:

```bash
ATLAS_DEPTH_DEVICE=mps "/Applications/Atlas Toolkit.app/Contents/MacOS/Atlas Toolkit"
```

- [ ] **Image to Mesh** with depth estimation finishes, using the GPU.
- [ ] The result looks like the same image processed on the CPU. If it does, CPU no longer needs to stay the macOS default.

## 8. Command line, upgrade and removal

- [ ] The command-line link from `docs/install/macos.md` works: `atlas-toolkit list`.
- [ ] Dragging the app in again and choosing **Replace** keeps your settings.
- [ ] Moving the app to the Trash leaves `~/Library/Application Support/AtlasToolkit` in place.
