# Security policy

OpenAI4S executes model-authored Python, R, and shell code. Its permission
system, kernel protocol, sandbox adapters, credential handling, egress fence,
artifact store, remote-compute transport, and WebSocket/HTTP boundary are all
security-sensitive. Please report suspected weaknesses privately, even when
you are unsure whether they are exploitable.

## Supported versions

Before the first public release, security fixes target the current `main`
branch. After releases begin, the newest published minor line and `main` are
supported; older minor lines and development snapshots on `next` may be asked
to upgrade before a fix is backported. A release-specific exception will be
documented in its GitHub security advisory.

## Reporting a vulnerability

Use GitHub's private vulnerability report form:

https://github.com/PKU-YuanGroup/OpenAI4S/security/advisories/new

Do not open a public issue, discussion, pull request, or test case containing
an undisclosed vulnerability, credential, private dataset, or working exploit.
Include the affected commit/version, platform, prerequisites, impact, minimal
reproduction, and any mitigation you already tested. Redact real secrets and
personal or unpublished research data.

Maintainers aim to acknowledge a report within three business days and provide
an initial triage within seven. These are response targets rather than a paid
support SLA. We will coordinate validation, a fix, regression coverage,
release timing, CVE/advisory publication when appropriate, and reporter credit.
Please allow up to 90 days for coordinated disclosure unless active
exploitation or another material risk requires a different schedule.

## High-priority examples

- escape from the configured Seatbelt/bubblewrap workspace or network policy;
- leakage of model, connector, SSH, cloud, or laboratory credentials into a
  kernel, subprocess, log, artifact, replay, delegation child, or exported
  notebook;
- bypass of permission decisions, one-shot shell capabilities, egress rules,
  query guards, or delegated-child restrictions;
- host-RPC frame confusion, cross-session access, artifact/provenance
  corruption, or unsafe recovery replay;
- remote-compute command injection or credential exposure;
- Web UI cross-site scripting, request forgery, origin/authentication bypass,
  path traversal, or arbitrary file access.

Security reports about unsupported third-party models, public APIs, optional
science packages, operating systems, or infrastructure should also be sent to
the relevant upstream. We still want a private OpenAI4S report when our
integration worsens the impact or violates a documented boundary.

## Good-faith research

We will not pursue legal action against good-faith research that stays within
your own accounts and data, avoids privacy violations and service disruption,
does not retain or exfiltrate secrets, follows provider terms, and gives us a
reasonable opportunity to remediate before disclosure. This safe-harbor
statement does not authorize testing third-party systems or data.
