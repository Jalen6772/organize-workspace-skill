"""Build reproducible ZIPs from an explicit public file list. No network or upload."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL_FILES = (
    "organize-workspace/SKILL.md",
    "organize-workspace/agents/openai.yaml",
    "organize-workspace/references/layouts.md",
    "organize-workspace/references/reuse.md",
)
PUBLIC_FILES = (
    ".gitignore", "README.md", "LICENSE", *SKILL_FILES,
    "examples/move-plan.json", "tests/README.md", "tests/RESULTS.md", "tests/results-2026-09-14.json",
    "tests/fixture.py", "tests/verify_fixture.py", "tests/check_harness.py", "tools/build_release.py",
)


def read_public_file(rel):
    path = ROOT / rel
    for part in (path, *path.parents):
        if part == ROOT:
            break
        if part.is_symlink():
            raise ValueError("Public file must not traverse a symlink: " + rel)
    if ROOT not in path.resolve().parents:
        raise ValueError("Path escaped repository: " + rel)
    return path.read_bytes()


def make_zip(path, entries):
    # Refuse to replace an existing release with different content.
    from io import BytesIO
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    data = output.getvalue()
    if path.is_symlink() or (path.exists() and path.read_bytes() != data):
        raise ValueError("Release artifact already exists with different content: " + path.name)
    if not path.exists():
        with path.open("xb") as out:
            out.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    files = {rel: read_public_file(rel) for rel in PUBLIC_FILES}
    version = re.search(r'^  version: "([0-9]+\.[0-9]+\.[0-9]+)"$',
                        files[SKILL_FILES[0]].decode(), re.M).group(1)
    dist = ROOT / "dist"
    if dist.is_symlink():
        raise ValueError("dist must not be a symlink")
    dist.mkdir(exist_ok=True)
    source_entries = {"organize-workspace-skill/" + p: b for p, b in files.items()}
    skill_entries = {p: files[p] for p in SKILL_FILES}
    skill_entries["organize-workspace/LICENSE"] = files["LICENSE"]
    sums = {}
    for name, entries in [
        ("organize-workspace-source-v" + version + ".zip", source_entries),
        ("organize-workspace-v" + version + ".zip", skill_entries),
    ]:
        sums[name] = make_zip(dist / name, entries)
    manifest = {"version": version, "files": [
        {"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        for p, b in sorted(files.items())]}
    for name, text in {
        "SHA256SUMS": "".join(sha + "  " + name + "\n" for name, sha in sorted(sums.items())),
        "MANIFEST.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }.items():
        dest = dist / name
        if dest.is_symlink() or (dest.exists() and dest.read_text(encoding="utf-8") != text):
            raise ValueError("Metadata already differs: " + name)
        if not dest.exists():
            with dest.open("x", encoding="utf-8") as out:
                out.write(text)
    print(json.dumps({"version": version, "public_file_count": len(files), "artifacts": sums}, indent=2))


if __name__ == "__main__":
    main()
