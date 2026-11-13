# Installing Atlas Toolkit on macOS

Needs macOS 15 or later on Apple Silicon (M1 or newer). Intel Macs are not supported.

**How far this has been tested:** the Mac build is built and tested automatically on GitHub's Mac machines, but nobody has tried it by hand on a real Mac yet. Those machines have no graphics card, so 3D previews on a real GPU, Retina scaling and trackpad gestures have not been seen working. `docs/PLATFORMS.md` lists what is proven.

Download the `.dmg` disk image from the [Releases](https://github.com/atlas-toolkit-org/AtlasToolkit/releases) page. Each release lists `SHA256SUMS.txt` to check
the download against.

## Install

1. Open `AtlasToolkit-<version>-macos-arm64.dmg`.
2. Drag **Atlas Toolkit** onto **Applications**.
3. Eject the disk image.

## The first start

The app is not notarized by Apple yet, so macOS refuses to open it the first time. It says it could not verify the app. This only happens once:

1. Open **Atlas Toolkit** from Applications. When the warning appears, click **Done**.
2. Open **System Settings > Privacy & Security** and scroll down to **Security**.
3. Next to the message about Atlas Toolkit, click **Open Anyway**, then confirm with your password or Touch ID.

Control-clicking the app and choosing Open no longer gets past this on macOS 15, so use System Settings.

## The command line

The program inside the app is also the command-line version. To type `atlas-toolkit` in a terminal, make a link to it:

```bash
sudo mkdir -p /usr/local/bin
sudo ln -sf "/Applications/Atlas Toolkit.app/Contents/MacOS/Atlas Toolkit" /usr/local/bin/atlas-toolkit
```

Then:

```bash
atlas-toolkit list
atlas-toolkit describe svg-overlap-cleaner
```

## Upgrade

Open the newer disk image and drag the app onto Applications again, choosing **Replace**. Your settings stay.

## Uninstall

Drag **Atlas Toolkit** from Applications to the Trash. If you made the command-line link, remove it too:

```bash
sudo rm /usr/local/bin/atlas-toolkit
```

Your own files stay, so a later install finds them again. To remove those too, delete:

| Folder | What it holds |
|---|---|
| `~/Library/Application Support/AtlasToolkit` | Settings, presets, history, plug-ins and workflows |
| `~/Library/Caches/AtlasToolkit` | Caches and previews |

## Other programs

Most tools need nothing else. A few use programs Atlas Toolkit doesn't include: Melt needs Blender, and Photo Hatching's depth models need the depth environment. The app's **Setup › Components** page shows what it found and how to add the rest; the depth environment installs from there with one button (**Install…**), with no Python of your own, and shows the download size, disk space and time before it starts.

Depth estimation runs on the CPU by default on a Mac. To try the Mac's GPU, start the app from a terminal with `ATLAS_DEPTH_DEVICE=mps`. This has not been tried on real hardware.

## Troubleshooting

**"Atlas Toolkit is damaged and can't be opened."** The download may have been changed or cut short. Download it again. If the message stays, tell macOS to stop treating this copy as a download:

```bash
xattr -dr com.apple.quarantine "/Applications/Atlas Toolkit.app"
```

**The 3D views are blank.** Please report your Mac model and macOS version: this is one of the things not yet seen on real hardware.

**Opening from the Dock shows two icons.** Please report this too, for the same reason.
