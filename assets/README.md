# Agent F artwork

Franklin the Firefox: a red panda in a fedora.

- `red-panda.ico`: Windows icon with 16, 24, 32, 48, 64, 96, 128 and 256 pixel versions: the Windows installer's icon, and Agent F's in Settings > Apps. The macOS uninstaller's icon is made from `red-panda-master.png` when the package is built.
- `red-panda-256.png`, `red-panda-512.png`: larger exports. The 256 version is the README image and the addons.mozilla.org icon.
- `red-panda-master.png`: the original 1254 × 1254 artwork, with a transparent background.
- `fedora.svg`, `fedora.png`: Franklin's fedora, front on, drawn in the artwork's colours by `tools/make_fedora_icons.py`, which also writes the toolbar icons (`extension/icons/fedora-*.png`, and `fedora-light-*.png` with a cream edge for dark toolbars) and the landing page's favicon (`docs/favicon.svg` and `docs/favicon.png`, the hat centred).
- The panel's shifty eyes come from `tools/make_franklin_eyes.py`, which writes `extension/ui/franklin-blank-eyes.png` and `extension/ui/franklin-eyes.svg` from the master artwork.
- `social-preview.png`: 1280 × 640 image for link previews, set as the GitHub repo's social preview and used by the landing page.
- `amo-description.md`: the add-on's description on addons.mozilla.org.

The add-on's own icons are in `extension/icons/`, and `docs/` has copies of the images the landing page uses.
