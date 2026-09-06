# Security policy

Tandem is a self-hosted server that holds your library, your reading positions and — if you
connect them — credentials for third-party services. Please report anything that undermines
that privately, using the channel below, rather than in a public issue.

## Supported versions

| Version | Supported |
|---|---|
| Latest release (and `main`) | Yes |
| Anything older | No |

This is a single-maintainer project with no backport branches. Fixes land on `main` and go out
in the next release; if you are running an older commit, the upgrade *is* the patch.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting:**
<https://github.com/jlafuenti/Book-Sync/security/advisories/new>

That form opens a private advisory visible only to you and the maintainer. There is no
security email address for this project — please do not open a public issue, post in
discussions, or describe the bug in a pull request, because a live instance of this software is
someone's home server and a public report is a working exploit against it.

What helps:

- The component (`server/`, `web/`, `android/`, `jetson/`) and the commit or release you tested.
- What an attacker gains, and what access they need to start.
- A minimal reproduction — a request, a sequence of UI steps, or a short script.

What to expect:

- **Acknowledgement within 7 days.**
- An assessment (accepted / not-in-scope / need more information) **within 14 days**.
- For an accepted report, a fix or a written plan with a date **within 30 days**, and credit in
  the advisory unless you would rather stay anonymous.

This is a hobby project maintained by one person in their spare time. Those are the timelines
that can actually be met; if a deadline is going to slip you will hear that rather than silence.

## Scope

**In scope**

- Authentication and session handling: JWT issuing/verification, refresh-token handling, the
  role gate on privileged endpoints.
- Authorisation: any path that lets one user read or modify another user's library, positions,
  bookmarks or account.
- Injection of any kind, path traversal in the library scanner / file endpoints, SSRF from
  user-supplied URLs, and stored XSS in the web app.
- Credential handling: anything that writes third-party credentials or tokens somewhere they
  can be read back (logs, backups, API responses).
- Anything that lets an unauthenticated caller reach data or an action that should need a login.

**Out of scope**

- Anything that requires the operator's own credentials or shell access on the host. An admin
  who can already change the settings is not an attacker in this model.
- **The admin-gated SSRF path.** Import sources may be pointed at private-network addresses on
  purpose (`allow_private=True`) — a self-hosted Audiobookshelf usually *is* on the LAN. That
  path is restricted to admins by design and is pinned by tests. Reports that it exists are not
  vulnerabilities; a way to reach it *without* the admin role is.
- Findings that assume the raw API port is published to the internet without a reverse proxy
  (see the checklist below) — that is a deployment mistake, and the docs say not to do it.
- Missing hardening headers, rate limits or version disclosure with no demonstrated impact,
  and automated scanner output with no analysis attached.
- Vulnerabilities in a dependency that are already reported by `pip-audit` / `npm audit` in CI,
  unless you can show they are actually reachable in this code.

## Threat model, briefly

A Tandem instance is single-tenant in the sense that matters: **every account on one server is
someone the operator chose to create.** The role system separates admins from regular users and
regular users from each other's data, and breaking that separation is a real bug — but the
software assumes it is running on a machine the operator controls, for people the operator
trusts, behind a reverse proxy they configured. It is not designed as a multi-tenant public
service.

## Deploying this on the internet — checklist

If you expose an instance beyond your LAN, at minimum:

- [ ] **Set the mandatory secrets, as files.** The server refuses to start without them rather
      than inventing defaults — `JWT_SECRET_KEY`, `POSTGRES_PASSWORD`, and `CREDENTIAL_ENC_KEYS`
      if you store third-party credentials. Generate them randomly; never reuse an example value.
      Pass them as compose `secrets:` (the `<NAME>_FILE` form the template ships), not in
      `environment:` — an environment variable is printed by `docker inspect` and readable in
      `/proc/1/environ`. See `docs/operations.md`, "Secrets".
- [ ] **Close public registration** (`ALLOW_PUBLIC_REGISTRATION=false`) or approve accounts
      manually. It is on by default for first-run convenience on a home LAN.
- [ ] **Terminate TLS in front of the app.** Nothing in this repo does TLS; use a reverse proxy
      (see `Caddyfile.example` and `docs/operations.md`).
- [ ] **Publish only the proxy.** Do not expose the raw API port. The bundled nginx proxies only
      `/api/`, while the API port itself serves `/docs` and `/openapi.json` unauthenticated —
      harmless on a LAN, an inventory of your endpoints on the internet.
- [ ] **Keep the transcription worker off the public internet.** It speaks plain HTTP and
      assumes a trusted network; it should be reachable from the server and nothing else.
- [ ] **Back up before you upgrade**, and keep the backups somewhere the server cannot write to.
      See `docs/operations.md`.

## Fixes and disclosure

Accepted reports are fixed on `main` with a regression test — this project's rule is that a
security fix without a test is not finished, and that sibling code paths with the same shape get
fixed in the same change. The advisory is published once a release carrying the fix is out.
