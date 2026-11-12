# Security

Please report security problems privately. Two ways:
- GitHub's **Report a vulnerability** button, on the repository's Security tab;
- or e-mail **security@atlas-toolkit.org**.

Please don't open a public issue for them.

Useful things to include:
- the version (About › Copy diagnostics);
- your operating system;
- the steps or file that trigger the problem.

## In scope

- **Files the app opens:** SVG, meshes, images, rasters, `.atlasflow` workflows, `.atlaspreset` presets,
  `.atlaspack` preset packs and `.zip` files holding them. A crafted file should never write outside the app's own
  folders or run code.
- **Preset pack signatures** (`core/ed25519.py`, `core/presets.py`): a pack must never show **Official** unless a
  trusted key signed it and it hasn't changed since.
- **Child processes:** the native helpers, Blender and the depth runtime.

Only the latest release gets security fixes.
