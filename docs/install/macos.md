# Installing Atlas Toolkit on macOS

Needs macOS 15 or later on Apple Silicon (M1 or newer). Intel Macs are not supported.

**How far this has been tested:** every release is built and tested automatically on GitHub's Mac machines, and 2.3.1 was used by hand on a remote Mac mini M4 (macOS 15): it installs, the drawing tools render, the 3D preview draws, and the depth environment installs and runs Photo Hatching on the CPU. That Mac was used remotely, without its full GPU, so 3D drawing on a Mac's own GPU, Retina scaling and trackpad gestures have not been seen working yet. [docs/PLATFORMS.md](../PLATFORMS.md) lists what is proven.

Download the disk image from [atlas-toolkit.org/download](https://atlas-toolkit.org/download/) or from the
[latest release](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/latest) on GitHub. The current version is
**2.4.1**:

| File |
|---|
| [`AtlasToolkit-2.4.1-macos-arm64.dmg`](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/download/v2.4.1/AtlasToolkit-2.4.1-macos-arm64.dmg) |

Later versions keep these names with a new number. Each release also lists `SHA256SUMS.txt`, the checksum of
every file.

## Check the download

Optional, but it proves the file arrived whole and unchanged. In Terminal, in the folder of the download, with `SHA256SUMS.txt` beside it:

```bash
grep macos-arm64.dmg SHA256SUMS.txt | shasum -a 256 -c -
```

It prints `OK` when the file matches.

## Install

1. Open `AtlasToolkit-2.4.1-macos-arm64.dmg`.
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
| `~/Library/Application Support/AtlasToolkit` | Settings, presets, history, plug-ins and workflows, and the depth environment (`ml-runtime`, with its installer's tools in `ml-runtime-base`) |
| `~/Library/Caches/AtlasToolkit` | Caches and previews |
| `~/.cache/huggingface/hub` | Photo Hatching's depth models, downloaded at their first use. Other apps that use Hugging Face may share this folder: delete only the `models--` folders of the depth models |

The app's **Setup › Components** page can remove the depth environment for you, and with it the downloaded models if you tick **Also delete the downloaded models**.

## Other programs

Most tools need nothing else. A few use programs Atlas Toolkit doesn't include: Melt needs Blender, and Photo Hatching's depth models need the depth environment. The app's **Setup › Components** page shows what it found and how to add the rest; the depth environment installs from there with one button (**Install…**), with no Python of your own, and shows the download size, disk space and time before it starts.

Depth estimation runs on the CPU by default on a Mac, with all three depth models; their weights download at the first render. To try the Mac's GPU, start the app from a terminal with `ATLAS_DEPTH_DEVICE=mps`. That has not been tried on real hardware yet.

## Troubleshooting

**"Atlas Toolkit is damaged and can't be opened."** The download may have been changed or cut short. Download it again. If the message stays, tell macOS to stop treating this copy as a download:

```bash
xattr -dr com.apple.quarantine "/Applications/Atlas Toolkit.app"
```

**The 3D views are blank.** Please report your Mac model and macOS version: this is one of the things not yet seen on real hardware.

**Opening from the Dock shows two icons.** Please report this too, for the same reason.
