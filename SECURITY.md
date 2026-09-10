# Security

Loadout is a local, pre-release plugin with the authority of the Hermes backend
and desktop account that loads it. It is not a sandbox for untrusted skills or
for another process running as the same operating-system user.

Normal operations protect foreign links and real directories, validate reviewed
paths, and retain originals for recovery. These checks do not make arbitrary
skill code safe to run. Configuration backups can contain credentials even when
the visible receipt and logs are redacted. Keep the Hermes home and backup
locations private, and review files before sharing them.

Do not post credentials, raw backups, private paths, or an exploitable report in
a public issue. When GitHub private vulnerability reporting is enabled, use the
repository's Security reporting flow. Otherwise, ask the maintainer for a private
channel without including sensitive details. No dedicated security email,
response SLA, or production support guarantee is advertised for this alpha.
