"""Windows installer tooling for the 言行 demonstration app.

The shipped product is a portable CPython runtime with the frozen application
sources, wrapped by a generated NSIS installer.  This package intentionally has
no dependency on the developer venv being copied: the build script installs
pinned wheels into the portable runtime and only packages the frozen source
tree passed by the caller.

No compiler is invoked here during development; real compilation is a separate
acceptance step by Codex on the build host.
"""
