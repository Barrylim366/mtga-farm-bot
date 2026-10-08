"""Build Forge (the modules the advisor needs) and compile the advisor against it.

    python scripts/build_advisor.py            # compile the advisor only (Forge already built)
    python scripts/build_advisor.py --forge    # rebuild Forge first, e.g. after `git pull` in forge/

Uses the JDK and Maven unpacked under tools/, so nothing has to be installed system-wide.
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge_bridge.paths import ADVISOR_DIR as ADVISOR, forge_dir, tools_dir  # noqa: E402

FORGE = forge_dir()
TOOLS = tools_dir()
BUILD = ADVISOR / "build"
CLASSPATH_FILE = BUILD / "forge-deps.classpath"


def find_jdk() -> Path:
    env = os.environ.get("BURNING_FORGE_JAVA_HOME")
    if env:
        return Path(env)
    found = sorted(glob.glob(str(TOOLS / "jdk-*")))
    if not found:
        sys.exit(f"No JDK found in {TOOLS} and BURNING_FORGE_JAVA_HOME is not set. See FORGE.md.")
    return Path(found[-1])


def find_maven() -> Path:
    found = sorted(glob.glob(str(TOOLS / "apache-maven-*")))
    if not found:
        sys.exit(f"No Maven found in {TOOLS}. See FORGE.md.")
    return Path(found[-1])


def exe(path: Path) -> str:
    return str(path) + (".exe" if os.name == "nt" else "")


def run(cmd: list[str], cwd: Path, env: dict) -> None:
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    result = subprocess.run(cmd, cwd=str(cwd), env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)


def build_forge(jdk: Path, env: dict) -> None:
    mvn = find_maven() / "bin" / ("mvn.cmd" if os.name == "nt" else "mvn")
    BUILD.mkdir(parents=True, exist_ok=True)
    # package + classpath in one reactor run: Forge's poms use ${revision}, which only resolves inside the reactor
    run(
        [
            str(mvn), "-B", "-ntp", "-q", "-pl", "forge-gui", "-am",
            "-DskipTests", "-Dcheckstyle.skip=true",
            "package", "dependency:build-classpath",
            f"-Dmdep.outputFile={CLASSPATH_FILE}",
        ],
        FORGE,
        env,
    )


def forge_classpath() -> str:
    if not CLASSPATH_FILE.exists():
        sys.exit("Forge is not built yet. Run: python scripts/build_advisor.py --forge")
    jars = glob.glob(str(FORGE / "forge-gui" / "target" / "forge-gui-*.jar"))
    if not jars:
        sys.exit("forge-gui jar missing. Run: python scripts/build_advisor.py --forge")
    return CLASSPATH_FILE.read_text(encoding="utf-8").strip() + os.pathsep + jars[0]


def write_profile() -> None:
    """Keep Forge's preferences and caches inside this project instead of the user's AppData."""
    run_dir = ADVISOR / "run"
    (run_dir / "user").mkdir(parents=True, exist_ok=True)
    (run_dir / "cache").mkdir(parents=True, exist_ok=True)
    profile = FORGE / "forge-gui" / "forge.profile.properties"
    profile.write_text(
        "userDir={}\ncacheDir={}\n".format(
            (run_dir / "user").as_posix(), (run_dir / "cache").as_posix()
        ),
        encoding="utf-8",
    )


def compile_advisor(jdk: Path, env: dict) -> None:
    classes = BUILD / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    sources = [str(p) for p in (ADVISOR / "src").rglob("*.java")]
    args_file = BUILD / "javac.args"
    # an argument file keeps the very long classpath off the command line
    args_file.write_text(
        '-encoding UTF-8\n--release 17\n-Xlint:-options\n-d "{}"\n-cp "{}"\n{}\n'.format(
            classes.as_posix(),
            forge_classpath().replace("\\", "/"),
            "\n".join('"{}"'.format(s.replace("\\", "/")) for s in sources),
        ),
        encoding="utf-8",
    )
    run([exe(jdk / "bin" / "javac"), f"@{args_file}"], ROOT, env)
    print(f"advisor compiled -> {classes}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forge", action="store_true", help="rebuild Forge's modules first")
    args = parser.parse_args()

    jdk = find_jdk()
    env = dict(os.environ)
    env["JAVA_HOME"] = str(jdk)
    env["PATH"] = str(jdk / "bin") + os.pathsep + env.get("PATH", "")

    if args.forge or not CLASSPATH_FILE.exists():
        build_forge(jdk, env)
    write_profile()
    compile_advisor(jdk, env)


if __name__ == "__main__":
    main()
