#!/usr/bin/env python3
"""Parse .godot/uid_cache.bin and audit committed .tres/.tscn refs/headers against it.

The editor's uid cache (.godot/uid_cache.bin) is the save-time authority for
ext_resource reference uids: a ref carrying a uid that is NOT registered there
is rewritten at the next save (the editor mints/uses its own), which marks the
file dirty and re-arms the strip window. Hand-authored/minted uids (the
normalize sweep's backfills) are exactly this class when the target already had
a cache registration.

Header uids are a different rule: a .tres/.tscn with NO header uid is invisible to every
inspector picker (editor_quick_open_dialog.cpp skips INVALID_ID files), and the editor
never backfills one. `--backfill-headers` adds one to each staged new file (never a committed one): the cache uid when the path is
registered, else a fresh mint the next editor scan adopts (`EditorFileSystem` add_id).
Refs to it stay path-only until the editor registers it -- run the default audit after the
next editor boot.

Usage:
  python3 .claude/hooks/uid_cache_audit.py            # report (committed files only)
  python3 .claude/hooks/uid_cache_audit.py --apply    # align refs/headers to the cache
  python3 .claude/hooks/uid_cache_audit.py --backfill-headers [--apply]  # add missing header uids to staged new files
  --repo <path>   operate on another checkout/worktree (default: the working directory's checkout)
The cache is always the main checkout's: the one that owns the target's git common dir.
"""
import pathlib
import re
import secrets
import struct
import subprocess
import sys



def _git_path(cwd: pathlib.Path, *args: str) -> pathlib.Path:
    out = subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args], capture_output=True, text=True).stdout.strip()
    if not out:
        sys.exit(f"uid_cache_audit: {cwd} is not inside a git checkout; pass --repo <checkout>")
    return pathlib.Path(out).resolve()


REPO = (pathlib.Path(sys.argv[sys.argv.index("--repo") + 1]).resolve() if "--repo" in sys.argv
        else _git_path(pathlib.Path.cwd(), "rev-parse", "--show-toplevel"))
MAIN_REPO = _git_path(REPO, "rev-parse", "--path-format=absolute", "--git-common-dir").parent
CACHE = MAIN_REPO / ".godot" / "uid_cache.bin"
EXCLUDE_PREFIX = "Jmodot/"
# Vendored addons keep their own headers; the backfill never edits them.
BACKFILL_EXCLUDE_PREFIXES = (EXCLUDE_PREFIX, "addons/")

B34_LOW = "abcdefghijklmnopqrstuvwxyz0123456789"  # godot: a-y=0-24, 0-8=25-33


def id_to_text(uid: int) -> str:
    if uid == 0 or uid == 0xFFFFFFFFFFFFFFFF:
        return ""
    digs = []
    n = uid
    while n > 0:
        d = n % 34
        digs.append(chr(ord("a") + d) if d < 25 else chr(ord("0") + (d - 25)))
        n //= 34
    return "uid://" + "".join(reversed(digs))


def parse_cache(data: bytes):
    """4.7.1 format (core/io/resource_uid.cpp save_to_cache/load_from_cache):
    u32 count, then count x (u64 uid, u32 len, len bytes path). No version field,
    no reverse map on disk. update_cache() appends entries and rewrites the head
    count, so a path can appear multiple times -- load order makes the LAST
    occurrence win the in-memory reverse_cache, so overwrite keeps last.
    """
    pos = 0

    def u32():
        nonlocal pos
        v = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return v

    def u64():
        nonlocal pos
        v = struct.unpack_from("<Q", data, pos)[0]
        pos += 8
        return v

    count = u32()
    path_to_uid = {}
    uid_to_path = {}
    for _ in range(count):
        uid = u64()
        n = u32()
        s = data[pos:pos + n].decode("utf-8", errors="replace")
        pos += n
        path_to_uid[s] = uid
        uid_to_path[uid] = s
    return path_to_uid, uid_to_path


REF_RE = re.compile(r'\[ext_resource type="([^"]+)"(?: uid="(uid://[^"]+)")?[^\n]*path="res://([^"]+)"[^\n]*\]')
HDR_RE = re.compile(r'^\[(?:gd_resource|gd_scene)[^\]]*uid="(uid://[^"]+)"')


# Files with uncommitted edits are skipped so an in-flight edit is never rewritten; they are named at exit.
SKIPPED_DIRTY: set[str] = set()


def tracked_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", "*.tres", "*.tscn"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.splitlines()
    dirty = set(subprocess.run(
        ["git", "--no-optional-locks", "-C", str(REPO), "status", "--porcelain", "--", "*.tres", "*.tscn"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.splitlines())
    dirty_paths = set()
    for line in dirty:
        p = line[3:].strip()
        if p:
            dirty_paths.add(p.replace("/", "\\"))
    skipped = [p for p in out if p.replace("/", "\\") in dirty_paths]
    SKIPPED_DIRTY.update(skipped)
    return [p for p in out if p.replace("/", "\\") not in dirty_paths]


def staged_new_files():
    """Files added to the index and absent from HEAD: the only files a header backfill may stamp."""
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(REPO), "diff", "--cached", "--name-only", "--diff-filter=A", "--", "*.tres", "*.tscn"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.splitlines()


def resolve_target_uid(tgt: str) -> str | None:
    """The authoritative uid for a ref target, from any source the editor uses:
    uid cache (registered), .import companion (imported assets), .cs.uid
    companion (scripts, Jmodot submodule included). None = never registered.
    """
    if tgt.endswith((".png", ".jpg", ".svg", ".wav", ".ogg", ".ttf", ".glb", ".fbx",
                     ".dae", ".obj", ".hdr", ".exr", ".webp", ".mp3", ".ogv")):
        imp = REPO / (tgt.replace("/", "\\") + ".import")
        if imp.exists():
            t = imp.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'uid="(uid://[^"]+)"', t)
            return m.group(1) if m else None
    if tgt.endswith(".cs"):
        comp = REPO / (tgt.replace("/", "\\").removesuffix(".cs") + ".cs.uid")
        if comp.exists():
            return comp.read_text(encoding="utf-8", errors="replace").strip()
    if tgt.endswith((".tres", ".tscn")):
        tp = REPO / tgt.replace("/", "\\")
        if tp.exists():
            head = tp.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            m = re.search(r'uid="(uid://[^"]+)"', head)
            if m:
                u = m.group(1)
                return u if re.fullmatch(r"uid://[a-y0-8]+", u) else None
    return None


def audit(path_to_uid):
    mismatches = []
    unregistered = []
    missing_uid = []
    for rel in tracked_files():
        if rel.startswith(EXCLUDE_PREFIX):
            continue
        p = REPO / rel.replace("/", "\\")
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in REF_RE.finditer(text):
            ref_uid, tgt = m.group(2), m.group(3)
            cache_uid = path_to_uid.get("res://" + tgt)
            if not ref_uid:
                # Path-only ref: the saver writes the target's uid ONLY when the
                # path is registered in the cache (measured: the editor's save
                # kept path-only refs to unregistered targets). Dirty iff
                # registered. Header/.cs.uid/.import uids are NOT written for
                # unregistered paths -- never add them.
                if cache_uid is not None:
                    missing_uid.append((rel, tgt, id_to_text(cache_uid)))
                continue
            if cache_uid is None:
                # Carried uid, target never registered: the saver writes nothing
                # -> the ref must become path-only. (.cs.uid/.import values are
                # not what the saver writes for an unregistered path.)
                unregistered.append((rel, tgt, ref_uid, ""))
                continue
            want = id_to_text(cache_uid)
            if ref_uid != want:
                mismatches.append((rel, tgt, ref_uid, want))
        hm = HDR_RE.match(text)
        if hm:
            my_uid = hm.group(1)
            cache_uid = path_to_uid.get("res://" + rel.replace("\\", "/"))
            if cache_uid is not None:
                want = id_to_text(cache_uid)
                if my_uid != want:
                    mismatches.append((rel, rel + " [header]", my_uid, want))
    return mismatches, unregistered, missing_uid


HDR_LINE = re.compile(r'^\[(gd_resource|gd_scene)\b[^\]]*\]')


def backfill_headers(path_to_uid, uid_to_path, apply: bool):
    """Add a header uid to every staged new .tres/.tscn (outside Jmodot/ and addons/) that lacks one."""
    used = {id_to_text(u) for u in uid_to_path}
    for rel in tracked_files():
        p = REPO / rel.replace("/", "\\")
        if p.exists():
            m = re.search(r'uid="(uid://[^"]+)"', p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0])
            if m:
                used.add(m.group(1))
    todo, minted, from_cache = [], 0, 0
    for rel in staged_new_files():
        if rel.startswith(BACKFILL_EXCLUDE_PREFIXES):
            continue
        p = REPO / rel.replace("/", "\\")
        text = p.read_text(encoding="utf-8", errors="replace")
        head, sep, rest = text.partition("\n")
        if not HDR_LINE.match(head) or ' uid="' in head:
            continue
        cache_uid = path_to_uid.get("res://" + rel)
        if cache_uid is not None:
            uid = id_to_text(cache_uid)
            from_cache += 1
        else:
            while True:
                uid = id_to_text(secrets.randbits(63) or 1)
                if uid not in used:
                    break
            used.add(uid)
            minted += 1
        todo.append((rel, head[:-1] + f' uid="{uid}"]' + sep + rest))
    print(f"{len(todo)} header(s) missing a uid ({from_cache} from cache, {minted} freshly minted)")
    if apply:
        for rel, new_text in todo:
            (REPO / rel.replace("/", "\\")).write_text(new_text, encoding="utf-8", newline="\n")
        print(f"APPLIED: wrote {len(todo)} header uid(s) under {REPO}")
    else:
        for rel, _ in todo[:20]:
            print(f"  {rel}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more (pass --apply to write)")


def report_skipped_dirty():
    for rel in sorted(SKIPPED_DIRTY):
        print(f"skipped (uncommitted edits): {rel}")


def main():
    data = CACHE.read_bytes()
    path_to_uid, uid_to_path = parse_cache(data)
    print(f"cache entries={len(path_to_uid)}")
    if "--backfill-headers" in sys.argv:
        backfill_headers(path_to_uid, uid_to_path, "--apply" in sys.argv)
        return
    mismatches, unregistered, missing_uid = audit(path_to_uid)
    print(f"{len(mismatches)} uid mismatches in committed files:")
    for rel, tgt, disk, want in mismatches:
        print(f"  {rel}: {tgt} disk={disk} cache={want}")
    print(f"{len(unregistered)} carried refs to cache-unregistered targets (strip to path-only):")
    for rel, tgt, disk, _ in unregistered[:40]:
        print(f"  {rel}: {tgt} disk={disk} [UNREGISTERED]")
    if len(unregistered) > 40:
        print(f"  ... and {len(unregistered) - 40} more")
    print(f"{len(missing_uid)} path-only refs to cache-registered targets (add the uid):")
    for rel, tgt, want in missing_uid[:40]:
        print(f"  {rel}: {tgt} want={want} [MISSING-UID]")
    if len(missing_uid) > 40:
        print(f"  ... and {len(missing_uid) - 40} more")
    if "--apply" in sys.argv:
        fixed = 0
        for rel, tgt, disk, want in mismatches:
            p = REPO / rel.replace("/", "\\")
            text = p.read_text(encoding="utf-8")
            n = text.count(disk)
            text = text.replace(disk, want)
            p.write_text(text, encoding="utf-8", newline="\n")
            fixed += n
        # Never-registered targets: no uid is a fixed point; strip to path-only
        # (the editor writes nothing for an INVALID target uid -> path-only stays).
        stripped = 0
        by_file = {}
        for rel, tgt, disk, _ in unregistered:
            by_file.setdefault(rel, set()).add(disk)
        for rel, bad_uids in by_file.items():
            p = REPO / rel.replace("/", "\\")
            text = p.read_text(encoding="utf-8")
            new = text
            for m in REF_RE.finditer(text):
                if m.group(2) in bad_uids:
                    new = new.replace(f' uid="{m.group(2)}"', "", 1)
            if new != text:
                p.write_text(new, encoding="utf-8", newline="\n")
                stripped += 1
        # Path-only refs to cache-registered targets: the saver writes the cache
        # uid -> the ref must carry it.
        added = 0
        for rel, tgt, want in missing_uid:
            p = REPO / rel.replace("/", "\\")
            text = p.read_text(encoding="utf-8")
            m = re.search(
                r'\[ext_resource type="([^"]+)" path="res://' + re.escape(tgt) + r'"',
                text)
            if m:
                # Insert AFTER the type attribute's closing quote (m.end(1)+1).
                # Inserting at m.end(1) -- inside the quotes -- corrupts the
                # attribute into type="T uid="..."" (hit 3x, 2026-08-13/14).
                insert_at = m.end(1) + 1
                new = text[:insert_at] + f' uid="{want}"' + text[insert_at:]
                if new != text:
                    p.write_text(new, encoding="utf-8", newline="\n")
                    added += 1
        print(f"APPLIED: replaced {fixed} uid token(s), stripped {stripped} unregistered ref(s), added {added} missing uid(s)")


if __name__ == "__main__":
    main()
    report_skipped_dirty()
