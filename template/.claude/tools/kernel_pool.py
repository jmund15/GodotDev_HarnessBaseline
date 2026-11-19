#!/usr/bin/env python3
"""Windows kernel pool tags by live size, without admin: the first read when free RAM is low and
process memory totals do not explain it.

    python3 .claude/tools/kernel_pool.py [--top N]
    python3 .claude/tools/kernel_pool.py --launch-test N

Each row: tag, paged MB and live objects, nonpaged MB and live objects. A tag's live count is
allocations minus frees. A large stock may still be held by a user-mode process: the session-start
`Env:` line's `handles:` entry names any process holding more than 200k handles.

`--launch-test N` launches N console-less `cmd /c exit` children and prints each watched tag's live
change per launch. A driver that leaks per process exit reads about +1.0 on its tag (`D2d ` for the
AMD iGPU driver); background churn makes `File` and `Toke` noisy, so read them over a large N.
"""
import argparse
import ctypes
import os
import subprocess
import sys

LAUNCH_TAGS = ("D2d ", "File", "Toke")


class _PoolTag(ctypes.Structure):
    _fields_ = [("tag", ctypes.c_char * 4), ("paged_allocs", ctypes.c_ulong), ("paged_frees", ctypes.c_ulong),
                ("paged_used", ctypes.c_size_t), ("nonpaged_allocs", ctypes.c_ulong),
                ("nonpaged_frees", ctypes.c_ulong), ("nonpaged_used", ctypes.c_size_t)]


def pool_tags():
    """{tag: (paged bytes, paged live, nonpaged bytes, nonpaged live)} from SystemPoolTagInformation;
    None off Windows or when the query fails."""
    if os.name != "nt":
        return None
    ntdll = ctypes.WinDLL("ntdll")
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = ctypes.c_ulong()
        status = ntdll.NtQuerySystemInformation(22, buf, size, ctypes.byref(needed)) & 0xFFFFFFFF
        if status == 0xC0000004 and size < 1 << 28:
            size *= 2
            continue
        if status:
            return None
        break
    count = ctypes.c_ulong.from_buffer(buf).value
    rows = (_PoolTag * count).from_buffer(buf, ctypes.sizeof(ctypes.c_size_t))
    return {row.tag.decode("latin1"): (row.paged_used, row.paged_allocs - row.paged_frees,
                                       row.nonpaged_used, row.nonpaged_allocs - row.nonpaged_frees)
            for row in rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--launch-test", type=int, metavar="N")
    args = parser.parse_args(argv)
    tags = pool_tags()
    if tags is None:
        print("UNKNOWN: pool tags unreadable (Windows only)")
        return 1
    if args.launch_test:
        for _ in range(args.launch_test):
            subprocess.run(["cmd", "/c", "exit"], creationflags=subprocess.CREATE_NO_WINDOW)
        after = pool_tags()
        for tag in LAUNCH_TAGS:
            before_live = tags.get(tag, (0, 0, 0, 0))
            after_live = after.get(tag, (0, 0, 0, 0))
            delta = (after_live[1] + after_live[3]) - (before_live[1] + before_live[3])
            print(f"{tag!r:8} {delta / args.launch_test:+.2f} per launch ({delta:+,} over {args.launch_test})")
        return 0
    mb = 1 << 20
    for tag, (paged, paged_live, nonpaged, nonpaged_live) in sorted(
            tags.items(), key=lambda item: -(item[1][0] + item[1][2]))[:args.top]:
        print(f"{tag!r:8} paged {paged / mb:9,.0f} MB {paged_live:>12,} live | "
              f"nonpaged {nonpaged / mb:7,.0f} MB {nonpaged_live:>10,} live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
