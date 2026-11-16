# Releasing panir

One version for every language, SemVer (`0.1.0`, `0.1.0-alpha.1`); Python
spells it as PEP 440 (`0.1.0a1`). `scripts/version.py` prints it, fails if
the packages disagree, and sets it everywhere.

CI's `package` job builds every package on every change, as it would be
published, without publishing: `twine check` on the Python sdist and wheel,
the npm tarball installed and imported, `cargo publish --dry-run`.

## One-time setup (needs the accounts)

Each registry trusts `.github/workflows/release.yml` in `ickc/panir`
(trusted publishing, OpenID Connect), so no token is stored anywhere. In
the repository's settings, create the environments `pypi`, `npm` and
`crates` (optionally with required reviewers, to approve each release).

- **PyPI:** at pypi.org, "Publishing", add a pending trusted publisher: project
  `panir`, owner `ickc`, repository `panir`, workflow `release.yml`,
  environment `pypi`.
- **npm:** the name `panir` is free (2026-09-28). npm's trusted publishing
  needs the package to exist: publish once by hand (`cd ts && npm publish
  --access public`, which builds it first), then in the package's
  settings add a trusted publisher: GitHub Actions, `ickc/panir`,
  `release.yml`, environment `npm`.
- **crates.io:** likewise the first publish by hand (`cd rust && cargo
  publish`, with a token from crates.io), then in the crate's settings,
  "Trusted Publishing": `ickc/panir`, `release.yml`, environment `crates`.
- **Julia (General registry):** install the JuliaRegistrator GitHub app on
  `ickc/panir` (and TagBot, if wanted). `Panir` is free (2026-09-28).
- **conda (prefix.dev):** `python/recipe/` builds and passes its tests
  locally; publishing it is not automated yet.

## Each release

1. `python3 scripts/version.py 0.1.0-alpha.1`, its entry in
   `CHANGELOG.md` (what dependants get, what breaks), commit, push; wait
   for CI.
2. `git tag v0.1.0-alpha.1 && git push origin v0.1.0-alpha.1`. The release
   workflow checks that the tag is the packages' version, publishes to
   PyPI, npm (a prerelease under the dist-tag `next`) and crates.io, then
   makes a GitHub release.
3. Julia: comment `@JuliaRegistrator register subdir=julia` on the tagged
   commit. (Check first whether General accepts a prerelease version; if
   not, register Julia from the first full release.)
