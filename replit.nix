{pkgs}: {
  deps = [
    pkgs.nss
    pkgs.nspr
    pkgs.dbus
    pkgs.atk
    pkgs.cups
    pkgs.libdrm
    pkgs.gtk3
    pkgs.pango
    pkgs.cairo
    pkgs.alsa-lib
    pkgs.xorg.libX11
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXrandr
    pkgs.xorg.libxcb
    pkgs.mesa
    pkgs.expat
    pkgs.libxkbcommon
  ];
}
