"""
Headless Adobe ADEPT EPUB decryption for the ACSM import pipeline.

DeACSM only *fulfills* an .acsm — it downloads the still-encrypted EPUB and the
Adobe license token. The actual ADEPT decryption is performed here:

  1. Export the account's user key (DER) from the DeACSM plugin's on-disk
     authorization (`libadobeAccount.exportAccountEncryptionKeyDER`).
  2. Hand that key to DeDRM's `ineptepub.decryptBook(userkey, in, out)`, which
     unwraps the per-book key from the EPUB's META-INF/rights.xml and AES-CBC
     decrypts the content.

Both run inside Calibre's Python environment, so this module is imported by
`_acsm_fulfill.py` (already driven via `calibre-debug -e`) and can also be run
standalone:

    calibre-debug -e _acsm_decrypt.py -- <in_epub> <out_epub>

`decrypt_epub` returns the ineptepub status code: 0 = decrypted, 1 = input was
already DRM-free, 2 = decryption failed.
"""

import os
import sys
import tempfile
import zipfile


def _resolve_deacsm_path() -> str:
    """Extract DeACSM.zip to a tempdir and put it on sys.path (so libadobe* import)."""
    # Calibre's own resolution order, mirrored (issue #180): the container runs
    # as an unprivileged uid now, so `calibre-customize` installed the plugin
    # under $HOME (or CALIBRE_CONFIG_DIRECTORY), not under /root. The root path
    # stays last so an image built before that change still finds its plugin.
    # This runs in Calibre's interpreter via `calibre-debug -e`, so it cannot
    # import the resolver in acsm.py — hence the duplication.
    config_dir = os.environ.get("CALIBRE_CONFIG_DIRECTORY")
    candidates = [
        os.path.join(config_dir, "plugins", "DeACSM.zip") if config_dir else "",
        os.path.expanduser("~/.config/calibre/plugins/DeACSM.zip"),
        "/root/.config/calibre/plugins/DeACSM.zip",
    ]
    zip_path = next((p for p in candidates if os.path.exists(p)), None)
    if not zip_path:
        raise RuntimeError(
            "DeACSM.zip not found under ~/.config/calibre/plugins. "
            "Was the plugin installed via calibre-customize -a?"
        )
    extract_dir = tempfile.mkdtemp(prefix="deacsm_dec_")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    sys.path.insert(0, extract_dir)
    return extract_dir


def export_user_key_der() -> bytes:
    """Return the Adobe ADEPT user key (DER bytes) from the DeACSM account.

    Raises RuntimeError if the plugin isn't authorized / the key can't be built.
    """
    _resolve_deacsm_path()
    from libadobeAccount import exportAccountEncryptionKeyDER

    fd, der_path = tempfile.mkstemp(suffix=".der")
    os.close(fd)
    try:
        ret = exportAccountEncryptionKeyDER(der_path)
        if ret is False or not os.path.exists(der_path) or os.path.getsize(der_path) == 0:
            raise RuntimeError(
                "could not export DeACSM account key — is the server authorized "
                "with Adobe? (Import Sources → Authorize Adobe)"
            )
        with open(der_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(der_path)
        except OSError:
            pass


def decrypt_epub(in_epub: str, out_epub: str) -> int:
    """Decrypt an ADEPT-encrypted EPUB. Returns ineptepub's status code:
    0 = decrypted, 1 = already DRM-free, 2 = failed.
    """
    userkey = export_user_key_der()
    from calibre_plugins.dedrm.ineptepub import decryptBook
    return decryptBook(userkey, in_epub, out_epub)


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 2:
        print("Usage: <in_epub> <out_epub>", file=sys.stderr)
        return 2
    in_epub, out_epub = args
    if not os.path.isfile(in_epub):
        print(f"Input EPUB not found: {in_epub}", file=sys.stderr)
        return 2

    try:
        rc = decrypt_epub(in_epub, out_epub)
    except Exception as e:
        import traceback
        print(f"Decryption error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 5

    if rc == 0:
        print(f"OK: decrypted -> {out_epub}")
        return 0
    if rc == 1:
        print("Input EPUB was already DRM-free")
        return 1
    print(f"Decryption failed (ineptepub rc={rc})", file=sys.stderr)
    return 5


if __name__ == "__main__":
    sys.exit(main())
