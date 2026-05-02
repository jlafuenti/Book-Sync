"""
Headless Adobe ID authorization for the DeACSM Calibre plugin.

Driven via:  calibre-debug -e _acsm_authorize.py -- <mode> [email] [password]

  mode = "anonymous"  → register without an Adobe ID (works for most
                         Google Play Books / Nook ACSMs)
  mode = "adobeid"    → register with a specific Adobe ID; email + password
                         must follow

This script runs inside Calibre's Python environment via `calibre-debug -e`
so the DeACSM plugin's bundled modules (libadobe, libadobeAccount, etc.)
are importable. The plugin stores its keys under
~/.config/calibre/plugins/DeACSM/account/ once authorization completes.
"""

import sys
import traceback


def _resolve_plugin_path():
    """Find the DeACSM plugin's unpacked directory inside Calibre."""
    import zipfile, os, tempfile

    # Calibre stores plugin zips at ~/.config/calibre/plugins/<Name>.zip
    # but it also unpacks the modules dir alongside. The plugin's own
    # Python files live inside the .zip; we extract a fresh copy to a
    # tempdir and put it on sys.path so the plugin's submodules import.
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


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: <mode> [email] [password] [ade_version]", file=sys.stderr)
        sys.exit(2)

    mode = args[0]
    email = args[1] if len(args) > 1 else ""
    password = args[2] if len(args) > 2 else ""
    ade_version = int(args[3]) if len(args) > 3 else 2  # ADE 3.0 default

    if mode not in ("anonymous", "adobeid"):
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(2)
    if mode == "adobeid" and (not email or not password):
        print("adobeid mode requires email and password", file=sys.stderr)
        sys.exit(2)

    try:
        _resolve_plugin_path()

        # These imports require sys.path to contain the unpacked plugin dir.
        from libadobe import createDeviceKeyFile, VAR_VER_SUPP_CONFIG_NAMES
        from libadobeAccount import (
            createDeviceFile, createUser, signIn, activateDevice,
        )
    except Exception as e:
        print(f"Could not load DeACSM plugin: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(3)

    if ade_version >= len(VAR_VER_SUPP_CONFIG_NAMES):
        print(f"Invalid ADE version {ade_version}", file=sys.stderr)
        sys.exit(2)

    try:
        createDeviceKeyFile()

        success = createDeviceFile(True, ade_version)
        if not success:
            print("Could not create device file.", file=sys.stderr)
            sys.exit(4)

        success, resp = createUser(ade_version, None)
        if not success:
            print(f"Could not create user: {resp}", file=sys.stderr)
            sys.exit(4)

        if mode == "anonymous":
            success, resp = signIn("anonymous", "", "")
        else:
            success, resp = signIn("AdobeID", email, password)

        if not success:
            print(f"Login failed: {resp}", file=sys.stderr)
            sys.exit(5)

        success, resp = activateDevice(ade_version, None)
        if not success:
            print(f"Device activation failed: {resp}", file=sys.stderr)
            sys.exit(6)
    except Exception as e:
        print(f"Authorization failed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(7)

    label = "anonymous" if mode == "anonymous" else email
    print(f"OK: authorized as {label}")


if __name__ == "__main__":
    main()
