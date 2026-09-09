# Bundled TOML reader

`tomli/` contains the unmodified pure-Python source files from
[Tomli 2.2.1](https://github.com/hukkin/tomli/tree/2.2.1/src/tomli), by Taneli
Hukkinen. Its MIT license is included beside the source.

Loadout uses this pinned TOML 1.0 reader on every supported Python version,
including Python 3.9, which has no standard-library `tomllib`. No dependency
installation is needed. It replaces a partial parser that could lose quoted
values and client-specific settings. The source is loaded lazily by file path,
so the Hermes plugin loader does not need to add this directory to `sys.path`.

The editor uses the pinned reader's key/value tokenizer to locate complete
statements, including multiline strings. Updating Tomli requires rerunning the
MCP preservation and cross-platform tests; do not change the version alone.

Upstream Git blob identifiers, verified when vendored:

| File | SHA-1 Git blob |
|---|---|
| `__init__.py` | `2b08d6e741929871cdece4489a3a9019921ab37a` |
| `_parser.py` | `b548e8b8045326555222a03900f5e20f99537810` |
| `_re.py` | `513486618cd2a25a49581a3571548b2651d86412` |
| `_types.py` | `d949412e03b29d70592c7721fe747e5085c2e280` |
| `LICENSE` | `e859590f886cd78344206af1a8ccb3080d4385e0` |
