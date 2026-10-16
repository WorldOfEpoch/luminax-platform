#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path


def sync_block(installer_text: str, rel_path: str, body: str) -> tuple[str, int]:
    pattern = re.compile(
        rf'cat > "\$BASE_DIR/{re.escape(rel_path)}" <<\'EOF\'\n.*?\nEOF\n',
        re.DOTALL,
    )
    replacement = f'cat > "$BASE_DIR/{rel_path}" <<\'EOF\'\n{body}\nEOF\n'
    return pattern.subn(lambda _m: replacement, installer_text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync payload files into matching luminax.sh heredoc blocks.")
    parser.add_argument("--installer", required=True, help="Path to luminax.sh")
    parser.add_argument("--payload-root", required=True, help="Path to payload root directory")
    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        required=True,
        help="Relative payload file to sync (repeatable), e.g. discord-bot/bot.py",
    )
    parser.add_argument("--apply", action="store_true", help="Allow writing the explicitly selected legacy archive")
    args = parser.parse_args()
    if not args.apply:
        print("PLAN: update only the explicitly selected legacy archive; no files read or written. Add --apply to execute.")
        return 0

    installer_path = Path(args.installer)
    payload_root = Path(args.payload_root)

    if not installer_path.exists():
        print(f"[sync] installer not found: {installer_path}", file=sys.stderr)
        return 1
    if not payload_root.exists():
        print(f"[sync] payload root not found: {payload_root}", file=sys.stderr)
        return 1

    text = installer_path.read_text(encoding="utf-8")
    for rel in args.files:
        payload_file = payload_root / rel
        if not payload_file.exists():
            print(f"[sync] payload file not found: {payload_file}", file=sys.stderr)
            return 1
        body = payload_file.read_text(encoding="utf-8").rstrip("\n")
        text, count = sync_block(text, rel, body)
        if count != 1:
            print(f"[sync] installer block not found for: {rel}", file=sys.stderr)
            return 1
        print(f"[sync] updated block: {rel}")

    installer_path.write_text(text, encoding="utf-8")
    print(f"[sync] installer updated: {installer_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
