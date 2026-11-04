#!/usr/bin/env python3
"""安裝 git hooks：pre-commit 跑 --staged、pre-push 跑 --history。Windows（Git Bash）與 macOS 通用。"""
import os
import stat
import subprocess
import sys

TEMPLATE = """#!/bin/sh
# 由 guard/install_git_hooks.py 產生：個資掃描閘門（{mode}）
ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$ROOT" || exit 1
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
  PY=python
else
  echo "找不到可用的 Python，個資掃描無法執行，已擋下。" >&2
  exit 1
fi
exec "$PY" -X utf8 guard/scan.py {mode}
"""


def install(repo_root):
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=repo_root,
                       stdout=subprocess.PIPE, universal_newlines=True, check=True)
    hooks = os.path.join(repo_root, r.stdout.strip())
    os.makedirs(hooks, exist_ok=True)
    for name, mode in (("pre-commit", "--staged"), ("pre-push", "--history")):
        path = os.path.join(hooks, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(TEMPLATE.format(mode=mode))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print("已安裝 %s" % path)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    install(root)
    sys.exit(0)
