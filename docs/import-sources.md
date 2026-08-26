# Import sources: ACSM and Audible

Tandem can import books directly from two vendor pipelines in addition to plain
file placement. This page describes how they work and what the server image
does and does not contain.

## Scope

The importers act only on content the operator lawfully purchased on their own
account, on their own hardware, for personal format-shifting between the ebook
and audiobook they already own. No keys, licenses, or copyrighted content are
in this repository.

## ACSM (Adobe ADEPT) — Google Play Books, Nook

Clicking "Download EPUB" on a DRM title at these vendors yields a small `.acsm`
license file, not the book. The importer (`server/services/import_sources/acsm.py`)
accepts `.acsm` files two ways — web upload, or a watched inbox folder at
`$IMPORTS_DIR/acsm/inbox/` polled every minute — and converts each into an EPUB
in the library via Calibre. A plain DRM-free `.epub` dropped in the inbox is
placed directly with no conversion.

The conversion itself depends on two third-party Calibre plugins:

- **DeACSM** (Leseratte10's acsm-calibre-plugin) — fulfills the `.acsm`
  against Adobe's ADEPT servers using the operator's own Adobe ID or an
  anonymous authorization, downloading the encrypted EPUB plus its license.
- **DeDRM** (noDRM fork) — performs the decryption step using the account key
  DeACSM provisioned.

## Audible

The Audible importer (`server/services/import_sources/audible.py`)
authenticates once against the operator's own Audible account (browser OAuth
via the `audible` library; the encrypted auth blob is stored server-side under
the Fernet keys in `CREDENTIAL_ENC_KEYS`). Sync lists the account's library,
dedupes against what Tandem already has, and uses `audible-cli` to download and
convert new titles to `.m4b` with the account's own activation data.

## What the image contains

**The default server image ships no DRM plugins.** When built with

```bash
docker compose build --build-arg INSTALL_DRM_PLUGINS=1
```

the image downloads the third-party DeACSM and DeDRM plugins from their
upstream GitHub releases at build time and installs them into Calibre. Without
that flag the plugins are not present and ACSM decryption is unavailable
(`.acsm` conversion fails; DRM-free EPUB intake and everything else still
works). The `audible`/`audible-cli` Python packages are part of the server's
requirements regardless of the flag, but they only operate with credentials the
operator supplies for their own account.
