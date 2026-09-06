# Homebrew distribution preparation

The canonical distribution is the Python wheel/sdist, installed primarily through
pipx. Homebrew will use a third-party tap after the maintainer establishes the
canonical public repository and publishes the release. No usable formula or
install command is advertised yet. There is no invented owner, URL, or checksum.

Once those facts exist, stage `Formula/cortexshift.rb` in the real tap. Follow
[Homebrew's Python guidance](https://docs.brew.sh/Python-for-Formula-Authors):
use `Language::Python::Virtualenv`, a supported `python@3.14` dependency, and
`virtualenv_install_with_resources`. Source must be the real immutable release
sdist with its actual SHA-256, and metadata must name the verified project URL.

Run `brew update-python-resources` against that formula after the package is
available on PyPI. Review every transitive resource URL and checksum; installation
must use declared resources rather than uncontrolled dependency resolution.
Resource generation is pending the public source and has not been claimed complete.

Include a `test do` block checking `cortexshift --version` and `--help`, with no
model calls. Before publishing the tap run `brew style`, `brew audit --strict`,
`brew install --build-from-source`, and `brew test` against the finalized formula.
Document exact tap/formula arguments after naming it. Do not fake a successful audit
while URLs are unavailable. Taps execute formula code with user privileges: keep
it simple, with no postinstall network hooks, curl installer, or custom updater.

Finalization requires canonical public repository/release URLs and immutable
release hashes, followed by explicit authorization to create/push the tap.
