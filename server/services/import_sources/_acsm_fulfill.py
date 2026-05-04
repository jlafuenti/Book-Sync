"""
Headless ACSM fulfillment for the DeACSM Calibre plugin.

Driven via:  calibre-debug -e _acsm_fulfill.py -- <acsm_path> <output_path>

The script:
  1. Loads the DeACSM plugin's bundled libadobe* modules from the installed
     plugin zip (same trick as _acsm_authorize.py).
  2. Calls libadobeFulfill.fulfill() to ask Adobe for the download URL +
     license token for the .acsm.
  3. Downloads the resulting EPUB or PDF straight to <output_path>.
  4. Critically, skips DeACSM's own step of appending META-INF/rights.xml
     to the EPUB. That file makes Calibre's EPUB Input plugin think the
     book is still DRMed even after the content has been decrypted, which
     is what was making `ebook-convert input.acsm output.epub` fail with
     calibre.ebooks.DRMError.

For PDFs we still patch the DRM-info atom because some readers refuse PDFs
that look mid-fulfillment. EPUBs ship clean.

Exit codes:
  0  success
  2  argument / usage error
  3  could not load plugin modules
  4  fulfillment call failed (auth not set up, ACSM expired, etc.)
  5  download failed (HTTP error or wrong content-type)
"""

import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile


def _resolve_plugin_path():
    """Extract DeACSM.zip to a tempdir and put it on sys.path."""
    candidates = [
        os.path.expanduser("~/.config/calibre/plugins/DeACSM.zip"),
        "/root/.config/calibre/plugins/DeACSM.zip",
    ]
    zip_path = next((p for p in candidates if os.path.exists(p)), None)
    if not zip_path:
        raise RuntimeError(
            "DeACSM.zip not found under ~/.config/calibre/plugins. "
            "Was the plugin installed via calibre-customize -a?"
        )
    extract_dir = tempfile.mkdtemp(prefix="deacsm_")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    sys.path.insert(0, extract_dir)
    return extract_dir


def _detect_type(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"PK"):
        return ".epub"
    if head.startswith(b"%PDF"):
        return ".pdf"
    return None


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        print("Usage: <acsm_path> <output_path>", file=sys.stderr)
        sys.exit(2)
    acsm_path, out_path = args

    if not os.path.isfile(acsm_path):
        print(f"ACSM not found: {acsm_path}", file=sys.stderr)
        sys.exit(2)

    try:
        _resolve_plugin_path()
        from lxml import etree
        from libadobe import sendHTTPRequest_DL2FILE
        from libadobeFulfill import fulfill
        from libpdf import patch_drm_into_pdf
    except Exception as e:
        print(f"Could not load DeACSM plugin: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(3)

    # --- 1. Ask Adobe to fulfill the ACSM ---
    try:
        success, reply = fulfill(acsm_path)
    except Exception as e:
        print(f"Fulfillment call crashed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(4)
    if not success:
        # `reply` here is an error message string from libadobeFulfill.
        print(f"Fulfillment refused: {reply}", file=sys.stderr)
        sys.exit(4)

    # --- 2. Pull download URL out of the reply XML ---
    try:
        adNS = lambda tag: "{http://ns.adobe.com/adept}" + tag
        resp = etree.fromstring(reply)
        download_url = resp.find(
            f"./{adNS('fulfillmentResult')}/{adNS('resourceItemInfo')}/{adNS('src')}"
        ).text
    except Exception as e:
        print(f"Could not parse Adobe fulfillment response: {e}", file=sys.stderr)
        sys.exit(5)

    # --- 3. Download the actual content ---
    tmp_download = out_path + ".part"
    try:
        t0 = time.time()
        rc = sendHTTPRequest_DL2FILE(download_url, tmp_download)
        dt_ms = int((time.time() - t0) * 1000)
    except Exception as e:
        print(f"Download crashed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(5)
    if rc != 200:
        print(f"Download failed with HTTP {rc}", file=sys.stderr)
        try:
            os.unlink(tmp_download)
        except OSError:
            pass
        sys.exit(5)

    filetype = _detect_type(tmp_download)
    if filetype is None:
        print("Downloaded content is neither EPUB nor PDF — refusing.", file=sys.stderr)
        os.unlink(tmp_download)
        sys.exit(5)

    # --- 4. Final placement, with format-specific touch-up ---
    if filetype == ".epub":
        # Move into place WITHOUT the META-INF/rights.xml that DeACSM's
        # own download() function would otherwise append. That file is what
        # makes Calibre's EPUB Input plugin reject the book as DRMed even
        # though the content has already been decrypted.
        shutil.move(tmp_download, out_path)
        print(f"OK: epub fulfilled to {out_path} (download {dt_ms} ms)")
        sys.exit(0)

    if filetype == ".pdf":
        # PDFs still want the rights blob patched in or some readers reject.
        # Reuse libpdf.patch_drm_into_pdf on the temp file.
        try:
            from libadobeFulfill import buildRights
            license_token_node = resp.find(
                f"./{adNS('fulfillmentResult')}/{adNS('resourceItemInfo')}/{adNS('licenseToken')}"
            )
            rights_xml_str = buildRights(license_token_node)
            adobe_resp = etree.fromstring(rights_xml_str)
            resource = adobe_resp.find(
                f"./{adNS('licenseToken')}/{adNS('resource')}"
            ).text
            ok = patch_drm_into_pdf(tmp_download, rights_xml_str, out_path, resource)
        except Exception as e:
            print(f"PDF patch failed: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(5)
        try:
            os.unlink(tmp_download)
        except OSError:
            pass
        if not ok:
            print("PDF DRM-patching failed.", file=sys.stderr)
            sys.exit(5)
        print(f"OK: pdf fulfilled to {out_path} (download {dt_ms} ms)")
        sys.exit(0)


if __name__ == "__main__":
    main()
