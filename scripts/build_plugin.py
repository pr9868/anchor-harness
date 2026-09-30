"""Build a reproducible plugin ZIP from an explicit public-file allowlist."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist/anchor.plugin")
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in ("README.md", "LICENSE", "CHANGELOG.md")]
    for name in (".claude-plugin", "assets", "skills", "docs", "examples"):
        files.extend(path for path in (root / name).rglob("*") if path.is_file()
                     and not any(part == "__pycache__" or part.endswith(".egg-info") for part in path.parts)
                     and path.suffix not in (".pyc", ".pyo") and path.name != ".DS_Store")
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = ZipInfo(path.relative_to(root).as_posix(), date_time=(2026, 9, 29, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    print(output)


if __name__ == "__main__":
    main()
