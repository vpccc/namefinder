# Security and privacy

## Scope

Namefinder is a local desktop tool, not a hosted service. Keep its HTTP server
bound to 127.0.0.1. Do not expose it through port forwarding, tunnels or a public
web server. It is not designed to protect secrets from malware, browser extensions
with access to the page, or another person controlling the same OS account.

## Data handling

- Webhook URLs and Ubisoft session values are held in memory, not deliberately
  saved by the app. Python/browser memory is not securely erased, and the OS,
  browser or extensions may retain data. Do not save these fields in a password
  manager on a shared computer.
- Ubisoft credentials are sent only to the fixed Ubisoft HTTPS profile endpoint.
  HTTP redirects are refused. The public Ubi-AppId in the code is an application
  identifier, not a personal login credential.
- Minecraft and Discord lookups send candidate usernames to their services.
  Ubisoft lookups also send the session credentials. These services see your
  network IP. Optional webhook delivery sends found names, platform and time
  to the configured Discord channel, visible to people with access to it.
- data/history.sqlite3 contains checked names, statuses, times, rate-limit
  cooldowns and hashes of webhook destinations. It does not store raw webhook
  URLs or Ubisoft tickets. This history can still be private.
- data/preferences.json holds the theme and motion preference. CSV exports
  contain usernames and results. Do not publish your data directory or exports.
- The local app URL contains a temporary access key. Do not share it, screenshots
  of it, browser history or command-line diagnostics. Quit ends that server's
  access. The key is separate from any platform account credential.

## Built-in protections

The local API requires a random per-launch key, validates Host and Origin,
does not enable CORS, and limits request bodies and concurrent request handlers.
Slow local connections time out. Only four explicit UI assets are served; the
server does not expose the project directory. Results use textContent, not HTML
insertion. CSP, no-store and no-referrer headers are set. CSV download links
expire after 60 seconds and can be used once. Outbound requests use HTTPS with
normal certificate validation, fixed lookup destinations and a strict Discord
webhook allowlist. Error messages avoid echoing credentials or response bodies.

These measures do not guarantee freedom from vulnerabilities. Use supported,
updated Python and browser versions. Only run code from sources you trust.
Review changes from contributors before running or merging them, especially
changes to network destinations, secret handling, launchers and dependencies.

## Account restrictions

Discord and Ubisoft checks use experimental interfaces. Authentication or
verification failures stop the scan; there is no bypass. Rate limits and
backoff do not guarantee permission from a platform or freedom from account
restrictions. Ubisoft's authenticated mode has not been tested end to end with
a live account. A local security review is not an assurance against bans.

## Reporting a vulnerability

Use the repository's Security > Report a vulnerability option if the maintainer
has enabled private vulnerability reporting. Otherwise open an issue requesting
a private contact route without including exploit details or credentials.
Never post tickets, webhooks, private logs or account information in a public
issue. Use dummy credentials in reproduction steps.

If a real secret is published, revoke or rotate it at the issuing service first.
Deleting a file or commit alone does not make an exposed credential safe again.

## Release hygiene

Build releases with `python build_release.py`. The explicit file list excludes
runtime data, environments, bytecode, Git history, logs and screenshots.
`.gitignore` is an extra guard for Git; it does not remove files already tracked
and is not an upload filter in GitHub's website. Inspect every upload.
