"""Validate all public packages and rebuild the community artifacts."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    folder = ROOT / "contributions"
    files = sorted(folder.glob("*.json"))
    unexpected = [p for p in folder.rglob("*") if p.is_file() and (p.suffix != ".json" or p.parent != folder)]
    if unexpected:
        raise SystemExit("contributions accepts only flat JSON packages")
    if not files:
        raise SystemExit("No contribution packages")
    args = [str(p) for p in files]
    subprocess.run([sys.executable, "community.py", "validate", *args], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "community.py", "merge", *args, "--db", "exports/community.sqlite", "--html", "exports/community.html"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
