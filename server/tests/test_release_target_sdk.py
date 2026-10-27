"""
The release build must ship the targetSdk the build script declares (#308).

`BuildConfigPinsTest` pins the *declaration* in `android/app/build.gradle.kts`,
but Play reads the manifest packaged into the bundle, and a variant override or
a manifest merge can make the two differ without any test noticing. CI builds
the release bundle already; `.github/scripts/release_target_sdk.py` reads its
packaged manifest and fails the job when the shipped value is not the declared
one. The script lives outside `android/` and is tested here, like the changelog
gate.
"""

import importlib.util
import os

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_SCRIPT = os.path.join(_REPO_ROOT, ".github", "scripts", "release_target_sdk.py")
_WORKFLOW = os.path.join(_REPO_ROOT, ".github", "workflows", "android-tests.yml")
_GRADLE = os.path.join(_REPO_ROOT, "android", "app", "build.gradle.kts")


def _check():
    assert os.path.isfile(_SCRIPT), ".github/scripts/release_target_sdk.py is missing"
    spec = importlib.util.spec_from_file_location("release_target_sdk", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(target="36"):
    uses_sdk = (
        f'<uses-sdk android:minSdkVersion="26" android:targetSdkVersion="{target}" />'
        if target is not None else '<uses-sdk android:minSdkVersion="26" />'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.example.app">\n'
        f"    {uses_sdk}\n"
        "    <application />\n"
        "</manifest>\n"
    )


_GRADLE_36 = """
android {
    compileSdk = 37
    defaultConfig {
        minSdk = 26
        targetSdk = 36
    }
}
"""


def _run(tmp_path, manifest_text, gradle_text=_GRADLE_36):
    manifest = tmp_path / "AndroidManifest.xml"
    if manifest_text is not None:
        manifest.write_text(manifest_text, encoding="utf-8")
    gradle = tmp_path / "build.gradle.kts"
    gradle.write_text(gradle_text, encoding="utf-8")
    return _check().main([str(manifest), str(gradle)])


def test_passes_when_the_shipped_target_is_the_declared_one(tmp_path, capsys):
    assert _run(tmp_path, _manifest("36")) == 0
    assert "36" in capsys.readouterr().out


def test_fails_when_the_shipped_target_differs(tmp_path, capsys):
    assert _run(tmp_path, _manifest("35")) == 1
    err = capsys.readouterr().err
    assert "35" in err and "36" in err


def test_fails_when_the_manifest_declares_no_target(tmp_path, capsys):
    assert _run(tmp_path, _manifest(None)) == 1
    assert "targetSdkVersion" in capsys.readouterr().err


def test_fails_loudly_when_the_manifest_is_not_where_expected(tmp_path, capsys):
    """An AGP upgrade that moves the file must break the job, not skip the check."""
    assert _run(tmp_path, None) == 1
    assert "AndroidManifest.xml" in capsys.readouterr().err


@pytest.mark.parametrize("gradle_text", [
    "android { defaultConfig { minSdk = 26 } }",
    "android { defaultConfig { targetSdk = 36 } }\nandroid { defaultConfig { targetSdk = 35 } }",
])
def test_fails_when_the_declaration_is_missing_or_ambiguous(tmp_path, capsys, gradle_text):
    assert _run(tmp_path, _manifest("36"), gradle_text) == 1
    assert "targetSdk" in capsys.readouterr().err


def test_reads_the_real_build_script():
    """The real declaration parses — a reformat of build.gradle.kts must not
    turn this check into a crash in CI."""
    with open(_GRADLE, encoding="utf-8") as f:
        assert _check().declared_target_sdk(f.read()) >= 36


def test_the_android_workflow_runs_it_on_the_release_bundle():
    with open(_WORKFLOW, encoding="utf-8") as f:
        workflow = f.read()
    assert "release_target_sdk.py" in workflow
    assert workflow.index("release_target_sdk.py") > workflow.index(":app:bundleRelease"), (
        "the check reads the release build's output, so it must run after bundleRelease"
    )
    assert "packaged_manifests/release" in workflow
