# Third-party software

This repository contains no vendored Python packages, fonts, icons, models,
media, or other third-party assets.

The demo calls the locally installed `ffmpeg` and `ffprobe` executables. They
are system dependencies and are not redistributed by this repository. FFmpeg
licensing depends on how a particular binary was configured; review the
license and build configuration of the binary you install.

The optional development extra installs Ruff and jsonschema under their MIT
licenses for style and schema-contract checks. Their transitive development
dependencies are installed by the package manager, are not runtime dependencies,
and are not redistributed.

GitHub Actions used by CI are pinned to full commit SHAs. Dependabot checks
those pins for updates.
