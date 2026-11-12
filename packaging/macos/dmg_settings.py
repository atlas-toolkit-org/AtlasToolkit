# The macOS disk image, made by scripts/package.py with dmgbuild:
#   dmgbuild -s packaging/macos/dmg_settings.py -D app="dist/Atlas Toolkit.app" "Atlas Toolkit" out.dmg
# dmgbuild runs this file with `defines` set. The window shows the app and a link to Applications, side by side.
import os.path

application = defines["app"]        # noqa: F821
appname = os.path.basename(application)

format = "ULMO"                     # LZMA: the smallest, and every macOS the app supports can open it
files = [application]
symlinks = {"Applications": "/Applications"}

default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((200, 160), (640, 400))
icon_size = 112
text_size = 13
icon_locations = {appname: (170, 180), "Applications": (470, 180)}
