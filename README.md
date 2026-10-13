# Namefinder v7.1 — Minecraft, Discord, Ubisoft + English words

Python desktop app for potentially available 3-, 4- and 5-character usernames, with optional Discord webhook alerts.

## Start

1. Close the old app and extract the entire `Namefinder-v7.1-source.zip` into a writable folder.
2. Run **start.bat** (requires Python 3.10+ and an installed browser).
3. On **Search**, choose the **Minecraft**, **Discord**, or **Ubisoft** card.
4. Choose **Random characters** or **English words**.
5. Choose length, characters, pattern and speed. Add an optional webhook in **Settings**, then **Start search**.

No pip packages are needed. Manual launch: `py -3 app.py`. On macOS/Linux: `python3 app.py`. If launching fails, run **debug.bat** to see the startup error.

## New interface

- **Search:** platform cards with icons, animated controls, live candidate previews, length and character filters, dictionary mode, optional pasted lists and live progress.
- **Results:** a separate table, potential-only filter, copy/open actions, pagination and CSV download. Downloads use your browser's normal download location or save prompt.
- **Settings:** five complete colour schemes (**Iris, Lagoon, Ember, Rose, Glacier**), an animation switch, webhook and Ubisoft session fields.
- Rounded cards, gradient accents and hover outlines throughout. Keyboard focus is visible; arrow keys switch tabs. Reduced-motion system preferences are respected.
- Theme and motion preferences are saved in `data/preferences.json`. Webhook URLs and Ubisoft session fields are never saved by the app. Reloading clears typed credentials and search controls; current results remain in the running Python process.

The checker still runs in Python. Its interface is served only on `127.0.0.1` and opens in an Edge/Chrome app-style window on Windows, or your default browser as a fallback. No website is hosted online, and no fonts, scripts or UI assets are downloaded. Internet access is still needed for username checks and webhook delivery.

Close it with **Settings > Quit Namefinder** to stop immediately. Closing or refreshing the interface requests that the scan stop; a current network request may take up to 15 seconds to finish. The local service stops automatically after five minutes without an interface connection, including if the browser closes unexpectedly. Export results before quitting. Do not share the local app URL, which contains a temporary access key. Launch only one copy per app folder.

## English words

**English words** uses a bundled English spelling dictionary instead of random combinations. It works on all three platforms with the same length, pattern, batch size, result filtering, CSV and webhook settings.

- 774 three-letter, 2,208 four-letter and 3,244 five-letter entries: 6,226 total.
- British and American spellings, including familiar and less common entries. This is not a complete list of every English word or inflected form.
- Names are lowercase dictionary entries with no digits or symbols appended. Enabling other character types permits them but does not require them. Letters must be enabled.
- Patterns work normally: `?o??` finds four-letter words with `o` second; `s????` finds five-letter words starting with `s`.
- Matching words are shuffled. If fewer match than your batch size, the app checks that smaller number without duplicates.
- Pasted names override both random and word modes. Clear the pasted-name box to search dictionary entries.
- Word selection is offline. Availability checks still use the chosen service. A dictionary word is not necessarily an available username.

The list comes from the [SCOWL 2026.02.25 release](https://github.com/en-wl/wordlist/releases/tag/rel-2026.02.25). Source details and required licenses are in `dictionary/`.

## Ubisoft / Rainbow Six Siege (experimental; requires your session)

This checks **Ubisoft Connect PC usernames**, not PlayStation/Xbox gamertags or Siege privacy aliases. A missing PC profile is only **potential availability**: reserved, blocked or legacy names may still be unclaimable. Confirm in your Ubisoft account.

Ubisoft's profile endpoint rejects requests without Authorization. No reliable, documented, unauthenticated availability API was found. This mode needs a temporary session from your own account; it does not automate login, change your username or claim names.

### Session setup

1. Open **Settings > Ubisoft session > Where do I find this?**, then **Open Ubisoft**.
2. Sign in normally. Complete any verification on Ubisoft's website.
3. Open browser Developer Tools (**F12**) and the **Network** tab, then refresh the signed-in account page.
4. Select a request to `public-ubiservices.ubi.com`. Under **Request Headers**, copy **Authorization**, beginning `Ubi_v1 t=`.
5. Paste it into the app's masked **Authorization value** field. A raw ticket also works.
6. If that request includes **Ubi-SessionId**, copy it into the optional session ID field. Otherwise leave it blank.

Use only your own session from the official account page. The ticket grants account access: keep it private and paste it only into this local app, never into chat, a webhook or another person's app. Both fields stay in memory and go only to Ubisoft's HTTPS profile endpoint; redirects are refused. They are never written to settings, CSV, the database or webhook payloads. The Ubisoft **Clear** button clears the fields; closing the app discards its in-memory session. There is no password field or automatic token renewal.

If no matching Authorization header appears, or the session is rejected, use the official account page directly. The app cannot promise this flow will work on every account, browser or future website version.

### Reducing requests

- One worker; Gentle waits 10 seconds between lookup starts, Fast 5 seconds, Turbo 3 seconds.
- Taken names are cached for 24 hours and labeled as cached. Cache hits do not extend the original expiry. Potential results are always rechecked.
- Rate limits stop the batch immediately. The server's `Retry-After` cooldown is saved across app restarts, with a minimum of 60 seconds and a fallback of 5 minutes.
- Authentication failures, forbidden responses, verification pages and unexpected data never count as available. They stop the search.
- Network/server errors have bounded retries. Stop interrupts retry waits.

These are conservative app settings, not published Ubisoft quotas. No request rate guarantees freedom from limits or verification. No proxy/account rotation or verification bypass is included. Avoid running multiple copies simultaneously.

**Validation limitation:** an unauthenticated request was checked live and required Authorization. Authenticated responses, expiry, blocking, caching and cooldowns were tested with fixtures. An end-to-end authenticated Ubisoft lookup has not been tested because no user session was supplied. This experimental mode may need adjustment if Ubisoft changes or restricts its API.

## Discord mode

Uses an experimental unauthenticated username-availability route. No Discord account token, login, cookies or browser automation are needed for the lookup. This route is not a documented public bot API contract and can change or be restricted.

Only HTTP 200 with the exact boolean `taken: false` counts as potential availability. CAPTCHA responses, missing/null/string values and error pages never do. Verification or access restrictions stop the search. Rate limits honor JSON `retry_after` and HTTP `Retry-After`; no verification bypass is included.

Confirm in **Discord User Settings > My Account > Username**. Earlier live tests returned both taken and not-taken responses without a browser; future verification-free checks are not guaranteed.

## Shared settings

- **Length:** 3c, 4c or 5c.
- **Characters:** letters, numbers and underscore. Discord also allows periods; Ubisoft also allows periods and dashes. Unsupported toggles are disabled.
- Selected types are allowed, not required. Ubisoft must start with a letter and excludes names containing `ubi` under its published restrictions. Discord cannot contain consecutive periods.
- **Pattern:** `?` means any enabled character. Blank means any candidate at the selected length. Literal characters must be enabled.
- **Pasted names:** spaces, commas, semicolons or newlines separate names. They override generation, pattern and batch size; length and character rules still apply. Case-insensitive duplicates are removed.
- Maximum batch: 5,000. No duplicates within a batch. Later batches can recheck, except fresh Ubisoft taken-cache hits.
- **Results > Potential only:** filters the table and CSV export.
- Use a result's copy button or external-link button to copy it or open the correct account page.

Switching platforms does not relabel old results or change their links. Starting another batch clears the table; export first to keep a separate CSV.

## Speed

| Setting | Minecraft | Discord | Ubisoft |
| --- | --- | --- | --- |
| Gentle | 1 worker; 1s between starts | 1 worker; 5s | 1 worker; 10s |
| Fast | Up to 4 workers; 0.2s | 1 worker; 2s | 1 worker; 5s |
| Turbo | Up to 8 workers; 0.1s | 1 worker; 1s | 1 worker; 3s |

Workers share a schedule and cooldown. Minecraft/Discord rate limits slow later requests with at most three attempts; delays over 120 seconds stop for a later retry. Ubisoft stops immediately on rate limits and saves its cooldown.

Stop cancels waits; an active HTTP request can take up to 15 seconds to finish. Completion shows elapsed time and names per second, including cached results. Latency, service limits and webhook delivery affect speed.

## Results and alerts

- **Taken:** an existing matching profile or explicit taken response. Ubisoft cache hits are labeled.
- **Potentially available:** an explicit Minecraft missing-profile response, Discord `taken: false`, or successful Ubisoft response with an empty profile list.
- **Unknown:** blocked/invalid response, verification, expired session or exhausted retries. Never alerts; stops the batch.

Results are not reservations or guarantees. Confirm in the relevant account settings.

A Discord text-channel webhook receives finds from any platform. Each alert identifies the source and links to that service. It never pings everyone or roles. Ubisoft credentials never go to webhook delivery.

The masked webhook stays in memory, not settings, CSV or the database. Optionally set `DISCORD_WEBHOOK_URL` before launch. Successful alerts are remembered separately per platform/name/destination (the destination is hashed). Failed sends are not marked delivered; recheck to retry. A lost response can cause a duplicate on retry.

## Data and sharing

To keep history, close both versions and copy the old `data/history.sqlite3` into the new app's `data` folder. Existing Minecraft/Discord tables remain compatible; Ubisoft uses separate tables. Run only one app per folder.

The supplied ZIP contains source, tests, dictionary and documentation only: no personal webhook, ticket, account data or history. Share the original ZIP rather than re-zipping a used app folder.

For a public repository, follow [PUBLISHING.md](PUBLISHING.md). The app's code is
MIT licensed; keep [LICENSE](LICENSE) and the dictionary's original notices.
This is an unofficial project, not endorsed by Minecraft, Discord or Ubisoft.
See [SECURITY.md](SECURITY.md) for data handling, account risks and reporting.
Build a clean source release with `python build_release.py`.

## Verification and references

Run `py -3 -m unittest discover -s tests -v`. The 80 tests cover the local API, appearance persistence, export authorization, credential isolation, generation, dictionary filters, all three response parsers, blocking, retries, cooldowns, cancellation, cache expiry, platform-specific alerts and separate histories. Security regressions also cover unauthorized mutations, stalled connections, connection limits, response bounds and release exclusions. Live webhook delivery requires your webhook and was not performed during packaging.

- [Ubisoft username changes](https://www.ubisoft.com/en-gb/help/article/000077823)
- [Ubisoft terms and username restrictions](https://www.ubisoft.com/legal/documents/termsofuse/en-INTL)
- [Official Ubisoft account page](https://account.ubisoft.com/)
- [Discord username rules](https://support.discord.com/hc/en-us/articles/12620128861463-New-Usernames-Display-Names)
- [Discord webhook documentation](https://docs.discord.com/developers/resources/webhook)
- [Minecraft profile](https://www.minecraft.net/profile)
