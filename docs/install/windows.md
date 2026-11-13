# Installing Atlas Toolkit on Windows

Needs Windows 10 or 11, 64-bit. No administrator rights unless you install for all users.

Download the Windows `.exe` installer from the [Releases](https://github.com/atlas-toolkit-org/AtlasToolkit/releases) page. Each release lists `SHA256SUMS.txt` to check
the download against.

## Install

1. Run `AtlasToolkit-<version>-windows-x64-setup.exe`.

   The installer is not signed yet, so Windows may say "Windows protected your PC". Choose **More info**, then **Run anyway**.
2. Choose who it is for:

   | Choice | Folder | Needs an administrator |
   |---|---|---|
   | Just you (the default) | `%LOCALAPPDATA%\Programs\Atlas Toolkit` | No |
   | All users | `C:\Program Files\Atlas Toolkit` | Yes |
3. Choose the options you want:
   - **Desktop shortcut:** off by default.
   - **Open `.atlasflow` workflows with Atlas Toolkit:** on by default.
   - **Add `atlas-toolkit` to PATH,** for the command line: off by default.

Atlas Toolkit then appears in the Start Menu.

## The command line

`atlas-toolkit.exe`, in the install folder, is the console version: it waits for the job, prints as it goes and returns an exit code.

```bat
atlas-toolkit list
atlas-toolkit describe svg-overlap-cleaner
```

If you did not add it to PATH, use the full path: `"%LOCALAPPDATA%\Programs\Atlas Toolkit\atlas-toolkit.exe" list`. A terminal that was already open needs restarting to see a new PATH.

## Upgrade

Run the newer installer. It replaces the old version in place, in the same folder, and keeps your settings. Close the app first, or the installer offers to close it.

## Uninstall

Go to **Settings > Apps > Installed apps > Atlas Toolkit > Uninstall**. This removes the program, its Start Menu entry, the file association and the PATH entry.

Your own files stay, so a later install finds them again. To remove those too, delete:

| Folder | What it holds |
|---|---|
| `%APPDATA%\AtlasToolkit` | Settings, presets, history, plug-ins and workflows |
| `%LOCALAPPDATA%\AtlasToolkit` | Caches and previews |

## Unattended install

```bat
AtlasToolkit-<version>-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /TASKS=association,path
```

Use `/ALLUSERS` instead of `/CURRENTUSER` from an elevated prompt. To remove it, run `unins000.exe /VERYSILENT` in the install folder.

## Portable use

If a folder named `AtlasToolkit-data` sits next to the program, the app keeps everything there and leaves the profile folders alone. This suits the single `Atlas Toolkit.exe` from `scripts/build.py` on a USB drive. Don't use it inside `C:\Program Files`, which ordinary users can't write to.

## Other programs

Most tools need nothing else. A few use programs Atlas Toolkit doesn't include: Melt needs Blender, and Photo Hatching's depth models need the depth environment. The app's **Setup › Components** page shows what it found and how to add the rest; the depth environment installs from there with one button (**Install…**), with no Python of your own, and shows the download size, disk space and time before it starts.

## Troubleshooting

**The 3D views are blank.** Update your graphics driver. Over Remote Desktop or in a virtual machine there may be no OpenGL at all, and the 3D previews need it.

**`atlas-toolkit` is not recognized.** Restart the terminal, or reinstall with the PATH option on.

**The installer says the app is running.** Close Atlas Toolkit, including any job still running, and try again.
