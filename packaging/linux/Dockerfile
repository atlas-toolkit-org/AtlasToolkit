# The Linux build environment. AlmaLinux 9 has glibc 2.34, the same floor PySide6's wheels set, so everything built here
# runs on Ubuntu 22.04+, Debian 12+, Fedora and RHEL 9+. It stays independent of whichever runner images GitHub retires.
#
#   docker build -t atlas-linux-build packaging/linux
#   docker run --rm -v "$PWD:/src" -w /src atlas-linux-build sh -c "uv sync --locked --all-extras && .venv/bin/python native/build.py"
#
# Use a clean clone, never a mount of a Windows folder: .venv, the helpers and the line endings all differ per OS.
FROM almalinux:9.8

# Keep packages' documentation: for some, that is where their licence text is, and the build copies it into the app's notices.
RUN sed -i '/^tsflags=nodocs/d' /etc/dnf/dnf.conf \
    && dnf -y -q install epel-release dnf-plugins-core \
    && dnf config-manager --set-enabled crb \
    && dnf -y -q install git tar gzip findutils which file binutils gcc-c++ make cmake ninja-build libstdc++-static \
        mesa-libGL mesa-libEGL libglvnd-opengl libxkbcommon libxkbcommon-x11 dbus-libs fontconfig freetype glib2 \
        libX11-xcb xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
        libwayland-client libwayland-cursor libwayland-egl xorg-x11-server-Xvfb mesa-dri-drivers \
    && dnf clean all

# uv brings its own Python; PyInstaller works with it here (docs/dev/multi-os-port.md).
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/0.13.7/install.sh | sh

# nfpm makes the deb and rpm packages (scripts/package.py).
RUN curl -LsSf -o /tmp/nfpm.tar.gz https://github.com/goreleaser/nfpm/releases/download/v2.48.0/nfpm_2.48.0_Linux_x86_64.tar.gz \
    && echo "6d7f9a28b2f397934403950fcbba83f598b08d02edbe79a0815b3b4897341750  /tmp/nfpm.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/nfpm.tar.gz -C /usr/local/bin nfpm \
    && rm /tmp/nfpm.tar.gz

# appimagetool makes the AppImage. It is an AppImage itself, and Docker has no FUSE, so it stays unpacked.
ENV APPIMAGE_RUNTIME=/opt/appimagetool/runtime-x86_64
RUN curl -LsSf -o /tmp/appimagetool https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage \
    && curl -LsSf -o /tmp/runtime https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
    && echo "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0  /tmp/appimagetool" | sha256sum -c - \
    && echo "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d  /tmp/runtime" | sha256sum -c - \
    && chmod +x /tmp/appimagetool && cd /tmp && ./appimagetool --appimage-extract > /dev/null \
    && mv /tmp/squashfs-root /opt/appimagetool && mv /tmp/runtime "$APPIMAGE_RUNTIME" && rm /tmp/appimagetool \
    && printf '#!/bin/sh\nexec /opt/appimagetool/AppRun "$@"\n' > /usr/local/bin/appimagetool \
    && chmod +x /usr/local/bin/appimagetool

ENV PYTHONUTF8=1
RUN git config --system --add safe.directory '*'
