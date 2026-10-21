"""
Script dong goi phat hanh sach (clean release zip) cho AURA STUDIO.
Loai tru: .env, .git, __pycache__, .pytest_cache, .ruff_cache, .venv, node_modules,
app/data/aura_store.db*, app/data/orders.jsonl, app/data/users.json.
"""
import argparse
import datetime
import os
import sys
import zipfile

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "node_modules", ".vscode", ".idea"
}

EXCLUDE_FILES = {
    ".env", "app/data/orders.jsonl", "app/data/users.json"
}


def should_exclude(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    parts = norm.split("/")
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    base = os.path.basename(norm)
    if base.startswith(".env") and not norm.endswith(".example"):
        return True
    if "app/data/aura_store.db" in norm:
        return True
    if norm in EXCLUDE_FILES or base in {"aura_store.db", "aura_store.db-wal", "aura_store.db-shm"}:
        return True
    if norm.endswith(".zip"):
        return True
    return False


def create_release_zip(output_path: str = None) -> str:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not output_path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(root_dir, f"aura_studio_release_{ts}.zip")
    else:
        if not os.path.isabs(output_path):
            output_path = os.path.join(root_dir, output_path)

    print(f"Creating release package: {os.path.basename(output_path)}")
    count = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                full_path = os.path.join(base, f)
                rel_path = os.path.relpath(full_path, root_dir)
                if not should_exclude(rel_path):
                    zf.write(full_path, rel_path)
                    count += 1

    print(f"Packed {count} files into: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Package clean release zip for AURA Studio")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output zip filename")
    args = parser.parse_args()
    create_release_zip(args.output)


if __name__ == "__main__":
    main()
