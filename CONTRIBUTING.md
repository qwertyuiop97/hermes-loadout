# Contributing

Start with a small issue describing the user-visible problem and a reproducible
example in disposable directories. Include the plugin revision, operating system,
Hermes version, client, and Global/Project scope. Do not attach raw configurations,
receipts, backups, session transcripts, tokens, or personal filesystem paths.

For changes, reproduce the failure with a focused filesystem or interaction test
before fixing it. Use the gates in [development](docs/development.md). Prefer
small patches over new frameworks. Keep discovery separate from activation,
preserve unrelated files, and make partial results explicit.

Client additions need primary path documentation and tests. UI changes should
retain narrow layouts, keyboard names, and one review flow for mutations. Label
screenshots with their actual environment. Do not claim native compatibility
from a fixture or a passing unit test.

Report security-sensitive details privately as described in [SECURITY.md](SECURITY.md).
