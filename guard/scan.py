#!/usr/bin/env python3
"""個資掃描閘門：commit／push 前擋下人名、email、個人路徑等。

用法：
  python guard/scan.py --staged        掃暫存區（pre-commit 用）
  python guard/scan.py --history       掃全部歷史（pre-push 用）
  python guard/scan.py --paths a b     掃指定檔案或資料夾
有命中結束碼 1，並印出「檔名:行號:命中字串」；blocklist 讀不到、或沒有任何有效規則，結束碼 2（失敗就擋）。
"""
import argparse
import os
import re
import subprocess
import sys
from collections import namedtuple

Pattern = namedtuple("Pattern", "source regex")
Hit = namedtuple("Hit", "name line_no matched")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BLOCKLIST = os.path.join(ROOT, "private", "blocklist.txt")

# 放過清單：先把這些字串遮掉再比對，所以「允許清單優先」。
# GitHub 帳號只在 GitHub 情境放過：網址、owner/repo、noreply 信箱；其他網域的 email 照擋。
_HANDLE = "bird" + "iebir"
ALLOW_PATTERNS = [
    re.compile(r"[A-Za-z0-9._+\-]*" + _HANDLE + r"@users\.noreply\.github\.com", re.I),
    re.compile(r"github\.com[/:]" + _HANDLE + r"(?![A-Za-z0-9_@\-])", re.I),
    re.compile(r"(?<![A-Za-z0-9_@.\-])" + _HANDLE + r"/[A-Za-z0-9._\-]+", re.I),
    re.compile(r"noreply@anthropic\.com", re.I),
]
ALLOW_EMAIL_DOMAINS = ["@example.com", "@users.noreply.github.com"]
# 提交者資料（作者、提交者的名稱與信箱）另外允許：名稱剛好就是這個帳號。
META_ALLOW = [re.compile(r"^" + _HANDLE + r"$", re.I)]

_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# 內建規則（不靠 blocklist 也會擋）。路徑字面用字元類別寫，避免掃描器原始碼被自己擋。
# 涵蓋 C:\、C:/、Git Bash 的 /c/ 三種寫法。
_DRIVE = r"(?:[A-Za-z]:[\\/]{1,2}|/[a-z]/)"
BUILTIN = [
    ("email", _EMAIL),
    ("win-user-path", re.compile(_DRIVE + r"User[s][\\/]{1,2}", re.I)),
    ("mac-user-path", re.compile(r"/User[s]/(?!example(?:/|\b))[^/\s]+(?:/|$)")),
    ("personal-path", re.compile(_DRIVE + r"Person[a]l\b", re.I)),
]
SKIP_DIRS = {".git", "__pycache__", "node_modules", "private", ".superpowers"}


def load_patterns(path):
    """讀 blocklist。檔案不存在會丟 FileNotFoundError。"""
    patterns = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip().lstrip("\ufeff")
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                regex = re.compile(line[3:], re.I)
            else:
                regex = re.compile(re.escape(line), re.I)
            patterns.append(Pattern(line, regex))
    return patterns


def _mask_allowed(line, extra=()):
    for rx in list(ALLOW_PATTERNS) + list(extra):
        line = rx.sub(lambda m: " " * len(m.group(0)), line)
    return line


def scan_text(text, patterns, name, extra_allow=()):
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        line = _mask_allowed(line, extra_allow)
        for p in patterns:
            for m in p.regex.finditer(line):
                hits.append(Hit(name, no, m.group(0)))
        for _kind, rx in BUILTIN:
            for m in rx.finditer(line):
                s = m.group(0)
                if _kind == "email" and any(s.lower().endswith(d) for d in ALLOW_EMAIL_DOMAINS):
                    continue
                hits.append(Hit(name, no, s))
    return hits


def _git(args, root, binary=False):
    r = subprocess.run(["git"] + args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("git %s 失敗：%s" % (" ".join(args), r.stderr.decode("utf-8", "replace")))
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def scan_staged(patterns, root):
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], root, binary=True)
    hits = []
    for rel in [p for p in out.decode("utf-8", "replace").split("\0") if p]:
        hits += scan_text(rel, patterns, rel + "(檔名)")
        content = _git(["show", ":" + rel], root)
        hits += scan_text(content, patterns, rel)
    return hits


def scan_history(patterns, root):
    """掃全部歷史：內容、提交訊息，以及每個提交的作者與提交者（名稱、信箱）。"""
    try:
        _git(["rev-parse", "--verify", "-q", "HEAD"], root)
    except RuntimeError:
        return []
    # core.quotepath=false：中文檔名不要變成八進位跳脫，否則名單比對不到
    meta = _git(["-c", "core.quotepath=false", "log", "--all", "--format=%an%n%ae%n%cn%n%ce"], root)
    hits = scan_text(meta, patterns, "提交者資料", extra_allow=META_ALLOW)
    text = _git(["-c", "core.quotepath=false", "log", "--all", "-p", "--format=commit %H%n%B"], root)
    return hits + scan_text(text, patterns, "歷史")


def scan_paths(paths, patterns):
    hits = []
    files = []
    for p in paths:
        if os.path.isdir(p):
            for d, dirs, names in os.walk(p):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
                files += [os.path.join(d, n) for n in names]
        else:
            files.append(p)
    for fp in files:
        with open(fp, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        hits += scan_text(text, patterns, fp)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description="個資掃描閘門")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true", help="掃暫存區")
    g.add_argument("--history", action="store_true", help="掃全部歷史")
    g.add_argument("--paths", nargs="+", help="掃指定檔案或資料夾")
    ap.add_argument("--blocklist", default=DEFAULT_BLOCKLIST)
    ap.add_argument("--root", default=ROOT, help="git repo 根目錄")
    a = ap.parse_args(argv)
    try:
        patterns = load_patterns(a.blocklist)
    except FileNotFoundError:
        print("找不到名單檔 %s。沒有名單就不放行；請照 private.example/blocklist.txt 建立。" % a.blocklist,
              file=sys.stderr)
        return 2
    if not patterns:
        print("名單檔 %s 存在，但沒有任何有效規則（只有空行或註解）。沒有名單就不放行；"
              "請照 private.example/blocklist.txt 填入。" % a.blocklist, file=sys.stderr)
        return 2
    if a.staged:
        hits = scan_staged(patterns, a.root)
    elif a.history:
        hits = scan_history(patterns, a.root)
    else:
        hits = scan_paths(a.paths, patterns)
    for h in hits:
        print("%s:%d:%s" % h)
    if hits:
        print("共 %d 處疑似個資，已擋下。" % len(hits), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
