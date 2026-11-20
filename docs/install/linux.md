# Installing Atlas Toolkit on Linux

Needs a 64-bit x86 system with glibc 2.34 or later: Ubuntu 22.04 and later, Debian 12 and later, Fedora, or RHEL, Rocky and AlmaLinux 9 and later. It works on both X11 and Wayland.

Download the package for your system from [atlas-toolkit.org/download](https://atlas-toolkit.org/download/) or from the
[latest release](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/latest) on GitHub. The current version is
**2.4.2**:

| File |
|---|
| [`atlas-toolkit_2.4.2_amd64.deb`](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/download/v2.4.2/atlas-toolkit_2.4.2_amd64.deb) |
| [`atlas-toolkit-2.4.2-1.x86_64.rpm`](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/download/v2.4.2/atlas-toolkit-2.4.2-1.x86_64.rpm) |
| [`AtlasToolkit-2.4.2-linux-x86_64.AppImage`](https://github.com/atlas-toolkit-org/AtlasToolkit/releases/download/v2.4.2/AtlasToolkit-2.4.2-linux-x86_64.AppImage) |

Later versions keep these names with a new number. Each release also lists `SHA256SUMS.txt`, the checksum of
every file.

## Check the download

Optional, but it proves the file arrived whole and unchanged. In the folder of the download, with `SHA256SUMS.txt` beside it:

```bash
sha256sum -c --ignore-missing SHA256SUMS.txt
```

It prints `OK` for each file you downloaded.

## Install

Debian and Ubuntu:

```bash
sudo apt install ./atlas-toolkit_2.4.2_amd64.deb
```

Fedora, RHEL, Rocky and AlmaLinux:

```bash
sudo dnf install ./atlas-toolkit-2.4.2-1.x86_64.rpm
```

Keep the `./`: it tells the package manager this is a file and not a package to search for. The package manager also installs the system libraries the app needs.

Atlas Toolkit then appears in your applications menu, and `atlas-toolkit` works in a terminal. Double-clicking an `.atlasflow` workflow opens it in the app.

## Or: the AppImage, with nothing to install

An AppImage is the whole app in one file, so it needs no administrator rights. Make it executable, then run it:

```bash
chmod +x AtlasToolkit-2.4.2-linux-x86_64.AppImage
```

```bash
./AtlasToolkit-2.4.2-linux-x86_64.AppImage
```

It takes the same command-line arguments as the installed app. It does not add a menu entry or file type, and the package manager can't bring missing libraries for it. Any desktop system already has them. On a minimal system, install them yourself, or use the deb or rpm.

If it says it cannot mount and mentions FUSE, either install FUSE (`sudo apt install fuse3` or `sudo dnf install fuse3`) or run it without. Starting this way is slower, because it unpacks itself each time:

```bash
./AtlasToolkit-2.4.2-linux-x86_64.AppImage --appimage-extract-and-run
```

A folder named `AtlasToolkit-data` next to the file keeps all its settings there, which suits a USB drive.

## Upgrade

Run the same install command with the newer package, or replace the AppImage with the newer file. Your settings stay.

## Uninstall

```bash
sudo apt remove atlas-toolkit
```

```bash
sudo dnf remove atlas-toolkit
```

For an AppImage, delete the file.

Your own files stay, so a later install finds them again. To remove those too, delete:

| Folder | What it holds |
|---|---|
| `~/.config/atlas-toolkit` | Settings, presets, plug-ins and workflows |
| `~/.local/share/atlas-toolkit` | Run history, and the depth environment (`ml-runtime`, with its installer's tools in `ml-runtime-base`) |
| `~/.cache/atlas-toolkit` | Caches and previews |
| `~/.cache/huggingface/hub` | Photo Hatching's depth models, downloaded at their first use. Other apps that use Hugging Face may share this folder: delete only the `models--` folders of the depth models |

The app's **Setup › Components** page can remove the depth environment for you, and with it the downloaded models if you tick **Also delete the downloaded models**.

If you set `XDG_CONFIG_HOME`, `XDG_DATA_HOME` or `XDG_CACHE_HOME`, the app uses those locations instead.

## What gets installed

| Path | What it is |
|---|---|
| `/opt/atlas-toolkit/` | The app, with `LICENSE` and `THIRD_PARTY_NOTICES.md`, and the helpers' licences and the GPL helper's source in `_internal/atlas_toolkit/licenses/` |
| `/usr/bin/atlas-toolkit` | The command, a link into `/opt` |
| `/usr/share/applications/atlas-toolkit.desktop` | The menu entry |
| `/usr/share/mime/packages/atlas-toolkit.xml` | The `.atlasflow` file type |
| `/usr/share/icons/hicolor/*/apps/atlas-toolkit.png` | The icon |

## The command line

The command line needs no display, so it also works on a server or over SSH:

```bash
atlas-toolkit list
atlas-toolkit describe svg-overlap-cleaner
```

## Other programs

Most tools need nothing else. A few use programs Atlas Toolkit doesn't include: Melt needs Blender, and Photo Hatching's depth models need the depth environment. The app's **Setup › Components** page shows what it found and how to add the rest; the depth environment installs from there with one button (**Install…**), with no Python of your own, and shows the download size, disk space and time before it starts.

## Troubleshooting

**The 3D views are blank, or the app stops when one opens.** The app needs working OpenGL from your system. Install the Mesa drivers:

```bash
sudo apt install libgl1-mesa-dri
```

```bash
sudo dnf install mesa-dri-drivers
```

**Something looks wrong on Wayland.** Try X11 for one run. If that fixes it, the problem is specific to your compositor or driver:

```bash
QT_QPA_PLATFORM=xcb atlas-toolkit
```

**The package manager says `libc6 (>= 2.34)` or `GLIBC_2.34` is missing.** The system is older than the app supports.

**The icon or menu entry does not appear at once.** Log out and back in. Some desktops only re-read these at login.

**NVIDIA's proprietary driver.** It has not been tested on real hardware yet, because the automated tests use software OpenGL.
