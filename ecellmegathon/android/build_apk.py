"""
Build the Incident Assistant APK (android/app) without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.

  .venv/Scripts/python android/build_apk.py                       # -> android/build/IncidentAssistant.apk
  .venv/Scripts/python android/build_apk.py --sdk <android-sdk> --install    # also adb install -r

Needs an Android SDK with build-tools and a platform (default: ../../engines/android-sdk next to the repo, or
ANDROID_HOME), and a JDK (javac, keytool on PATH). The APK is signed with a debug key kept outside the repo
(<sdk>/debug.keystore, created on the first build). Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
BUILD = HERE / "build"
REPO = HERE.parent
MIN_SDK = 28            # Android 9: WebView permission API, loopback cleartext config
WIN = os.name == "nt"


def run(cmd: list, **kw) -> None:
    print("  $ " + " ".join(str(c) for c in cmd[:6]) + (" …" if len(cmd) > 6 else ""), flush=True)
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        raise SystemExit(f"failed: {cmd[0]}")


def newest(folder: Path, prefix: str = "") -> Path:
    found = sorted((p for p in folder.iterdir() if p.is_dir() and p.name.startswith(prefix)),
                   key=lambda p: [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", p.name.split("-")[-1])])
    if not found:
        raise SystemExit(f"nothing in {folder}")
    return found[-1]


def jdk_tool(name: str) -> str:
    """javac / keytool / java from JAVA_HOME, the PATH, or the newest JDK under Program Files."""
    exe = name + (".exe" if WIN else "")
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / exe).exists():
        return str(Path(home) / "bin" / exe)
    if WIN:
        jdks = sorted(Path(os.environ.get("ProgramFiles", r"C:\Program Files"), "Java").glob("jdk*"))
        for j in reversed(jdks):
            if (j / "bin" / exe).exists():
                return str(j / "bin" / exe)
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found: install a JDK or set JAVA_HOME")
    return found


def tool(bt: Path, name: str) -> Path:
    for cand in (bt / f"{name}.exe", bt / f"{name}.bat", bt / name):
        if cand.exists():
            return cand
    raise SystemExit(f"{name} not found in {bt}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    default_sdk = os.environ.get("ANDROID_HOME") or str((REPO / ".." / ".." / "engines" / "android-sdk").resolve())
    ap.add_argument("--sdk", default=default_sdk)
    ap.add_argument("--install", action="store_true", help="adb install -r the APK afterwards")
    ap.add_argument("--version-name", default="1.0")
    ap.add_argument("--version-code", default="1")
    a = ap.parse_args()

    sdk = Path(a.sdk)
    bt = newest(sdk / "build-tools")
    platform = newest(sdk / "platforms", "android-")
    android_jar = platform / "android.jar"
    target = re.search(r"(\d+)", platform.name).group(1)
    print(f"SDK {sdk}\n  build-tools {bt.name} · platform {platform.name} · min SDK {MIN_SDK}")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "res").mkdir(parents=True)
    (BUILD / "gen").mkdir()
    (BUILD / "classes").mkdir()
    (BUILD / "dex").mkdir()

    print("1. resources (aapt2)")
    run([tool(bt, "aapt2"), "compile", "--dir", APP / "res", "-o", BUILD / "res" / "res.zip"])
    run([tool(bt, "aapt2"), "link", "-I", android_jar, "--manifest", APP / "AndroidManifest.xml",
         "--min-sdk-version", MIN_SDK, "--target-sdk-version", target,
         "--version-code", a.version_code, "--version-name", a.version_name,
         "--java", BUILD / "gen", "-o", BUILD / "unsigned.apk", BUILD / "res" / "res.zip"])

    print("2. Java (javac)")
    sources = [str(p) for p in (APP / "src").rglob("*.java")] + [str(p) for p in (BUILD / "gen").rglob("*.java")]
    run([jdk_tool("javac"), "--release", "11", "-encoding", "UTF-8", "-classpath", android_jar,
         "-d", BUILD / "classes", "-Xlint:-options", *sources])

    print("3. dex (d8)")
    classes = [str(p) for p in (BUILD / "classes").rglob("*.class")]
    # The SDK's .bat wrappers are unreliable from Python on Windows; run their jars directly.
    run([jdk_tool("java"), "-cp", bt / "lib" / "d8.jar", "com.android.tools.r8.D8", "--release", "--min-api", MIN_SDK, "--lib", android_jar,
         "--output", BUILD / "dex", *classes])
    with zipfile.ZipFile(BUILD / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as z:
        z.write(BUILD / "dex" / "classes.dex", "classes.dex")

    print("4. align + sign (zipalign, apksigner)")
    run([tool(bt, "zipalign"), "-p", "-f", "4", BUILD / "unsigned.apk", BUILD / "aligned.apk"])
    ks = sdk / "debug.keystore"
    if not ks.exists():
        run([jdk_tool("keytool"), "-genkeypair", "-keystore", ks, "-storepass", "android", "-keypass", "android",
             "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Android Debug,O=Android,C=US"])
    out = BUILD / "IncidentAssistant.apk"
    run([jdk_tool("java"), "-jar", bt / "lib" / "apksigner.jar", "sign", "--ks", ks, "--ks-pass", "pass:android", "--key-pass", "pass:android",
         "--out", out, BUILD / "aligned.apk"])
    run([jdk_tool("java"), "-jar", bt / "lib" / "apksigner.jar", "verify", out])
    print(f"built {out} ({out.stat().st_size:,} bytes)")

    if a.install:
        adb = shutil.which("adb") or str(sdk / "platform-tools" / ("adb.exe" if WIN else "adb"))
        run([adb, "install", "-r", out])
        print("installed on the board")
    return 0


if __name__ == "__main__":
    sys.exit(main())
