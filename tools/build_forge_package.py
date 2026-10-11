"""Build the Forge engine package that "Enable Forge Engine" downloads.

Produces ``forge-engine-v<N>-windows-x64.zip`` holding everything the advisor
needs to run with nothing installed:

    jre/                      a Java runtime cut down with jlink
    lib/00-advisor.jar        forge_advisor/ compiled
    lib/01-forge-gui.jar      Forge, built from source
    lib/100-*.jar ...         Forge's libraries, in classpath order
    res/                      Forge's card scripts, editions, AI profiles
    forge-package.json        package version, Forge commit, build date
    LICENSE, SOURCES.md       GPL-3.0 and where the source of every part is

Needs a JDK 17 (with jmods, for jlink) and Maven 3.9; neither has to be
installed system-wide:

    python tools/build_forge_package.py --jdk <jdk-17> --maven <apache-maven-3.9> \
        [--forge-src <Forge checkout at FORGE_COMMIT>] [--pin]

Without ``--forge-src`` Forge is cloned from GitHub at FORGE_COMMIT. ``--pin``
writes the zip's SHA-256 and size into ForgeEngine/installer.py, which is what
makes a bot accept that download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ForgeEngine import paths  # noqa: E402

FORGE_REPO = "https://github.com/Card-Forge/forge.git"
FORGE_COMMIT = "16f2d354c410e1c528f1e4f054513553450ca007"
JAVA_MODULES = "java.se,jdk.unsupported,jdk.crypto.ec,jdk.charsets,jdk.zipfs"
# Parts of res/ a headless rules engine never opens: other game modes' data
# and the translations (Forge runs in en-US here).
RES_LEFT_OUT = {
    "adventure", "music", "skins", "sound", "quest", "conquest", "deckgendecks",
    "geneticaidecks", "cube", "draft", "puzzle", "planar_conquest",
}
LANGUAGES_KEPT = {"en-US.properties"}


def run(command: list, cwd: Path | None = None, env: dict | None = None) -> None:
    print("+", " ".join(str(c) for c in command[:6]), "..." if len(command) > 6 else "")
    subprocess.run([str(c) for c in command], cwd=cwd, env=env, check=True)


def exe(jdk: Path, name: str) -> Path:
    return jdk / "bin" / (name + (".exe" if os.name == "nt" else ""))


def fetch_forge(work: Path) -> Path:
    source = work / "forge-src"
    if not (source / "pom.xml").exists():
        shutil.rmtree(source, ignore_errors=True)
        run(["git", "clone", "--filter=blob:none", "--no-checkout", FORGE_REPO, source])
        run(["git", "checkout", FORGE_COMMIT], cwd=source)
    return source


def build_forge(forge: Path, jdk: Path, maven: Path, classpath_file: Path) -> None:
    env = dict(os.environ, JAVA_HOME=str(jdk), PATH=str(jdk / "bin") + os.pathsep + os.environ.get("PATH", ""))
    mvn = maven / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")
    # Package and classpath in one reactor run: Forge's poms use ${revision},
    # which only resolves inside the reactor.
    run([
        mvn, "-B", "-ntp", "-q", "-pl", "forge-gui", "-am", "-DskipTests", "-Dcheckstyle.skip=true",
        "package", "dependency:build-classpath", f"-Dmdep.outputFile={classpath_file}",
    ], cwd=forge, env=env)


def compile_advisor(jdk: Path, classpath: list[Path], work: Path) -> Path:
    classes = work / "advisor-classes"
    shutil.rmtree(classes, ignore_errors=True)
    classes.mkdir(parents=True)
    sources = sorted((REPO / "forge_advisor" / "src").rglob("*.java"))
    args = work / "javac.args"
    separator = ";" if os.name == "nt" else ":"
    args.write_text(
        "-encoding UTF-8\n--release 17\n-Xlint:-options\n"
        f'-d "{classes.as_posix()}"\n'
        f'-cp "{separator.join(p.as_posix() for p in classpath)}"\n'
        + "\n".join(f'"{s.as_posix()}"' for s in sources) + "\n",
        encoding="utf-8",
    )
    run([exe(jdk, "javac"), f"@{args}"])
    jar = work / "advisor.jar"
    jar.unlink(missing_ok=True)
    run([exe(jdk, "jar"), "--create", "--file", jar, "-C", classes, "."])
    return jar


def long_path(path: Path) -> Path:
    """Windows' extended-length form: some card script paths pass 260 characters."""
    if os.name != "nt":
        return path
    text = str(path.resolve())
    prefix = "\\\\?\\"
    return Path(text if text.startswith(prefix) else prefix + text)


def copy_res(forge: Path, out: Path) -> None:
    source = long_path(forge / "forge-gui" / "res")
    target = long_path(out / "res")
    for entry in source.iterdir():
        if entry.name in RES_LEFT_OUT:
            continue
        if entry.is_dir():
            shutil.copytree(entry, target / entry.name)
        else:
            shutil.copy2(entry, target / entry.name)
    languages = target / "languages"
    if languages.is_dir():
        for entry in languages.iterdir():
            if entry.is_file() and entry.name not in LANGUAGES_KEPT:
                entry.unlink()


def make_runtime(jdk: Path, out: Path) -> None:
    run([
        exe(jdk, "jlink"), "--module-path", jdk / "jmods", "--add-modules", JAVA_MODULES,
        "--strip-debug", "--no-man-pages", "--no-header-files", "--compress=2", "--output", out / "jre",
    ])


def write_notes(out: Path, forge: Path) -> None:
    shutil.copy2(REPO / "LICENSE", out / "LICENSE")
    try:
        bot_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        bot_commit = "unknown"
    (out / "SOURCES.md").write_text(
        "# Sources\n\n"
        "Everything in this package is free software under the GNU GPL v3 (see LICENSE).\n\n"
        f"- Forge rules engine, AI and card scripts (`lib/01-forge-gui.jar`, `res/`): "
        f"{FORGE_REPO.removesuffix('.git')} at commit `{FORGE_COMMIT}`.\n"
        "- The advisor (`lib/00-advisor.jar`): `forge_advisor/` in "
        f"https://github.com/Barrylim366/mtga-farm-bot at commit `{bot_commit}`. It is adapted from "
        "https://github.com/Funny-Bones/mtga-play-bot (GPL-3.0).\n"
        "- Forge's libraries (`lib/1xx-*.jar`): each from Maven Central, under its own licence.\n"
        "- Java runtime (`jre/`): Eclipse Temurin / OpenJDK 17, GPLv2 with the Classpath Exception.\n",
        encoding="utf-8",
    )
    manifest = {
        "package_version": paths.PACKAGE_VERSION,
        "forge_commit": FORGE_COMMIT,
        "bot_commit": bot_commit,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out / "forge-package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def zip_folder(folder: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    folder = long_path(folder)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with path.open("rb") as handle:
                    archive.writestr(info, handle.read(), compresslevel=9)


def pin(zip_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size_mb = max(1, round(zip_path.stat().st_size / (1 << 20)))
    installer = REPO / "ForgeEngine" / "installer.py"
    text = installer.read_text(encoding="utf-8")
    text = re.sub(r'^PACKAGE_SHA256 = ".*"$', f'PACKAGE_SHA256 = "{digest}"', text, flags=re.M)
    text = re.sub(r"^PACKAGE_SIZE_MB = \d+$", f"PACKAGE_SIZE_MB = {size_mb}", text, flags=re.M)
    installer.write_text(text, encoding="utf-8")
    return digest, size_mb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jdk", required=True, type=Path)
    parser.add_argument("--maven", required=True, type=Path)
    parser.add_argument("--forge-src", type=Path, help="a Forge checkout at FORGE_COMMIT (else cloned)")
    parser.add_argument("--work", type=Path, default=REPO / "build" / "forge-engine")
    parser.add_argument("--skip-forge-build", action="store_true", help="reuse an earlier Maven build")
    parser.add_argument("--pin", action="store_true", help="write the zip's checksum into installer.py")
    args = parser.parse_args()

    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    forge = (args.forge_src or fetch_forge(work)).resolve()
    classpath_file = work / "forge-deps.classpath"
    if not args.skip_forge_build or not classpath_file.exists():
        build_forge(forge, args.jdk, args.maven, classpath_file)
    deps = [Path(p) for p in classpath_file.read_text(encoding="utf-8").strip().replace("\n", ";").split(";") if p.strip()]
    forge_jar = next((forge / "forge-gui" / "target").glob("forge-gui-*.jar"))
    advisor_jar = compile_advisor(args.jdk, deps + [forge_jar], work)

    out = work / "package"
    shutil.rmtree(long_path(out), ignore_errors=True)
    (out / "lib").mkdir(parents=True)
    (out / "res").mkdir()
    shutil.copy2(advisor_jar, out / "lib" / "00-advisor.jar")
    shutil.copy2(forge_jar, out / "lib" / "01-forge-gui.jar")
    for index, dep in enumerate(deps):
        shutil.copy2(dep, out / "lib" / f"{100 + index}-{dep.name}")
    print("+ copying res/")
    copy_res(forge, out)
    make_runtime(args.jdk, out)
    write_notes(out, forge)

    target = work / f"forge-engine-v{paths.PACKAGE_VERSION}-windows-x64.zip"
    print(f"+ zipping {target}")
    zip_folder(out, target)
    size = target.stat().st_size
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"package: {target}\nsize:    {size / (1 << 20):.1f} MB\nsha256:  {digest}")
    if args.pin:
        pin(target)
        print("pinned into ForgeEngine/installer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
