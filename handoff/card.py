"""產生接力卡 Markdown。"""
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import expand, norm_path

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
ROLE_LABEL = {"user": "使用者", "assistant": "助理", "tool": "工具"}


@dataclass
class Card:
    path: str
    text: str
    source_cli: str
    source_session: str
    cwd: str
    claude_session: str = None   # 這條接力鏈最早的 Claude session id


def run_git_status(cwd):
    """git status --short；不是 git repo 或失敗回 None。"""
    if not cwd:
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        if r.stdout.strip() != "true":
            return None
        r = subprocess.run(["git", "-C", cwd, "status", "--short"],
                           capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
        return r.stdout.rstrip()
    except (OSError, subprocess.SubprocessError):
        return None


def select_messages(messages, limits):
    """從最後往前取：使用者訊息最多 max_user_messages 則、總長最多 max_chars 字。"""
    max_user = limits.get("max_user_messages", 10)
    max_chars = limits.get("max_chars", 15000)
    per_msg = limits.get("max_message_chars", 3000)
    picked, users, total = [], 0, 0
    for m in reversed(messages):
        text = m.text
        if len(text) > per_msg:
            text = text[:per_msg] + "…（截斷）"
        if m.role == "user":
            if users >= max_user:
                break
            users += 1
        if picked and total + len(text) > max_chars:
            break
        total += len(text)
        picked.append((m, text))
    picked.reverse()
    return picked


def find_inherited_claude_id(card_dir, cwd):
    """接力鏈：同一工作資料夾最近一張有 Claude session 的卡，取其 id。"""
    d = Path(expand(card_dir))
    if not d.exists():
        return None
    want = norm_path(cwd)
    for f in sorted(d.glob("20*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            txt = f.read_text(encoding="utf-8")
        except OSError:
            continue
        wd = re.search(r"^- 工作資料夾：(.+)$", txt, re.M)
        cs = re.search(r"^- Claude session：(\S+)", txt, re.M)
        if wd and cs and norm_path(wd.group(1).strip()) == want and UUID_RE.match(cs.group(1)):
            return cs.group(1)
    return None


CARD_REF_RE = re.compile(r"接力卡\s+([^\r\n，。]+?\.md)")


def claude_id_from_prompt(transcript, card_dir):
    """Codex／agy 對話的使用者提示裡若有「先讀接力卡 <路徑>」，讀那張卡的 `- Claude session：` 欄。

    只讀 card_dir 底下的卡；找不到、讀不到、不是 UUID 都回 None（呼叫端再退回同資料夾最新一張卡）。
    """
    base = norm_path(str(card_dir))
    for m in transcript.messages:
        if m.role != "user":
            continue
        ref = CARD_REF_RE.search(m.text)
        if not ref:
            continue
        p = Path(ref.group(1).strip())
        if norm_path(str(p.parent)) != base:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        cs = re.search(r"^- Claude session：(\S+)", txt, re.M)
        if cs and UUID_RE.match(cs.group(1)):
            return cs.group(1)
    return None


def screening_text(transcript, config):
    """agy 關鍵字檢查用：接力卡會帶的最後幾則使用者訊息與助理文字（不含工具摘要）。"""
    picked = select_messages(transcript.messages, config.get("limits", {}))
    return "\n".join(text for m, text in picked if m.role in ("user", "assistant"))


def render_card(transcript, config, git_status, now, claude_session):
    limits = config.get("limits", {})
    picked = select_messages(transcript.messages, limits)
    n_user = sum(1 for m, _ in picked if m.role == "user")
    folder = Path(transcript.cwd or "").name or "（未知資料夾）"
    L = [
        f"# 接力卡：{folder}（{now:%Y-%m-%d %H:%M}）",
        "",
        f"- 交出：{transcript.cli}（自動產生，agent-handoff）",
        f"- 來源 CLI：{transcript.cli}",
        f"- 來源 session：{transcript.session_id}",
        f"- 工作資料夾：{transcript.cwd or '—'}",
        f"- Claude session：{claude_session or '—'}",
        f"- 開始時間：{transcript.start or '—'}",
        f"- 最後時間：{transcript.end or '—'}",
    ]
    if transcript.title:
        L.append(f"- 對話標題：{transcript.title}")
    if transcript.compacted:
        L.append("- 備註：來源對話曾 compact，只取最後一次 compact 之後的內容")
    for n in transcript.notes:
        L.append(f"- 備註：{n}")
    L += ["", "## git status --short", ""]
    if git_status is None:
        L.append("（不是 git repo，或讀取失敗）")
    elif git_status == "":
        L.append("（工作樹乾淨）")
    else:
        L += ["```", git_status, "```"]
    L += ["", f"## 最後幾輪對話（自動擷取，使用者訊息 {n_user} 則；工具輸出已省略，改了什麼請用 git diff 驗證）", ""]
    for m, text in picked:
        if m.role == "tool":
            L.append(f"- {text}")
        else:
            L += [f"**{ROLE_LABEL[m.role]}**：", "", text, ""]
    if not picked:
        L.append("（沒有可擷取的對話文字）")
    L += ["", "## 接手方紀錄", "",
          "（接手方填寫：開始時間、做了哪幾步、改了哪些檔、跑了哪些驗證、卡在哪、"
          "要原本主力回來改的共用檔內容）", ""]
    return "\n".join(L)


def card_filename(now, cli):
    return f"{now:%Y-%m-%d_%H%M}_{cli}.md"


def make_card(transcript, config, now=None, git_status="auto", write=True):
    """產生接力卡；write=False 時只回傳內容與預計路徑，不寫檔（dry-run 用）。"""
    now = now or datetime.now()
    card_dir = Path(expand(config["card_dir"]))
    if transcript.cli == "claude":
        claude_id = transcript.session_id
    else:
        # 優先從對話裡的接力卡路徑回推（精確），找不到才用同資料夾最新一張卡
        claude_id = (claude_id_from_prompt(transcript, card_dir)
                     or find_inherited_claude_id(card_dir, transcript.cwd))
    if git_status == "auto":
        git_status = run_git_status(transcript.cwd)
    text = render_card(transcript, config, git_status, now, claude_id)
    path = card_dir / card_filename(now, transcript.cli)
    i = 2
    while path.exists():
        path = card_dir / f"{now:%Y-%m-%d_%H%M}_{transcript.cli}_{i}.md"
        i += 1
    if write:
        card_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Card(str(path), text, transcript.cli, transcript.session_id, transcript.cwd, claude_id)
