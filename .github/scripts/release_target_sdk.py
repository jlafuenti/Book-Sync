"""Fail the Android job when the release bundle ships a targetSdk other than the declared one.

`BuildConfigPinsTest` pins `targetSdk` in `android/app/build.gradle.kts`, but
Play reads the manifest packaged into the bundle. A variant override or a
manifest merge could make the two differ, and nothing would notice until Play
rejected an upload or the app ran under a target nobody reviewed (#308).

The Play floor itself is BuildConfigPinsTest's job; this only checks that the
shipped value is the declared one.

Usage (from the workflow, after `:app:bundleRelease`):

    python .github/scripts/release_target_sdk.py \\
        android/app/build/intermediates/packaged_manifests/release/processReleaseManifestForPackage/AndroidManifest.xml \\
        android/app/build.gradle.kts

Tested by server/tests/test_release_target_sdk.py.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET

_ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
_DECLARATION = re.compile(r"^\s*targetSdk\s*=\s*(\d+)\s*$", re.MULTILINE)


def declared_target_sdk(gradle_text: str) -> int:
    """The one `targetSdk = N` in the build script. Raises ValueError otherwise."""
    found = _DECLARATION.findall(gradle_text)
    if len(found) != 1:
        raise ValueError(
            f"expected exactly one `targetSdk = N` line in the build script, found {len(found)}"
        )
    return int(found[0])


def manifest_target_sdk(manifest_text: str) -> int:
    """`android:targetSdkVersion` from the manifest's <uses-sdk>. Raises ValueError otherwise."""
    root = ET.fromstring(manifest_text)
    uses_sdk = root.find("uses-sdk")
    value = uses_sdk.get(f"{_ANDROID_NS}targetSdkVersion") if uses_sdk is not None else None
    if value is None:
        raise ValueError("the packaged manifest has no <uses-sdk android:targetSdkVersion>")
    return int(value)


def main(argv: list[str]) -> int:
    manifest_path, gradle_path = argv
    if not os.path.isfile(manifest_path):
        print(
            f"No packaged release manifest at {manifest_path}. Did bundleRelease run, or "
            f"did an Android Gradle Plugin upgrade move AndroidManifest.xml? Update the "
            f"path in the workflow; do not drop the check.",
            file=sys.stderr,
        )
        return 1
    try:
        with open(gradle_path, encoding="utf-8") as f:
            declared = declared_target_sdk(f.read())
        with open(manifest_path, encoding="utf-8") as f:
            shipped = manifest_target_sdk(f.read())
    except ValueError as e:
        print(f"Release targetSdk check: {e}", file=sys.stderr)
        return 1
    if shipped != declared:
        print(
            f"The release bundle ships targetSdkVersion={shipped}, but build.gradle.kts "
            f"declares targetSdk = {declared}. Something between the declaration and the "
            f"packaged manifest changed it.",
            file=sys.stderr,
        )
        return 1
    print(f"Release bundle ships targetSdkVersion={shipped}, as declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
