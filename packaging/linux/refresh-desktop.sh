#!/bin/sh
# After installing or removing: tell the desktop about the menu entry, file type and icons. A missing tool is not an error:
# a server or a container has none of these, and the app works without them.
command -v update-desktop-database > /dev/null && update-desktop-database -q /usr/share/applications
command -v update-mime-database > /dev/null && update-mime-database /usr/share/mime
command -v gtk-update-icon-cache > /dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor
exit 0
