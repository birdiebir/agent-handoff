"""handoff 入口邏輯：找出這一段 → 選目標 → 產生接力卡 → 取得啟動指令。"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import card as C
from . import launch as L
from . import state as ST
from . import sessions as S
from . import transcripts as T
from .adapters import ADAPTERS
from .config import HandoffError, is_under, is_work_path, load_config


def agy_blocked(cwd, config, text=None, cwd_fallback=False):
    """工作專案（cwd 未知／只是退回值、接力卡內容含工作關鍵字）時 agy 不可用；回傳原因字串，可用則回 None。"""
    try:
        ADAPTERS["agy"].check_allowed(cwd, config, text=text, cwd_fallback=cwd_fallback)
        return None
    except HandoffError as e:
        return str(e)


def choose_target(source_cli, cwd, config, explicit=None, text=None, cwd_fallback=False):
    """決定交給誰。explicit 指定時驗證；否則照備援順序取來源之後的下一家，工作專案跳過 agy。"""
    chain = config["chain"]
    if explicit:
        if explicit not in ADAPTERS or explicit not in chain:
            raise HandoffError(f"不認得的目標：{explicit}（可用：{', '.join(chain)}）")
        if explicit == source_cli:
            raise HandoffError(f"來源已經是 {explicit}，不用交給自己。")
        if explicit == "agy":
            why = agy_blocked(cwd, config, text, cwd_fallback)
            if why:
                raise HandoffError(why)
        return explicit
    idx = chain.index(source_cli) if source_cli in chain else -1
    for cand in chain[idx + 1:]:
        if cand == "agy" and agy_blocked(cwd, config, text, cwd_fallback):
            continue
        return cand
    extra = "（工作專案已跳過 agy）" if is_work_path(cwd, config) else ""
    raise HandoffError(f"{source_cli} 之後沒有下一家了{extra}。要回主力請打 handoff claude。")


is_noninteractive = S.is_noninteractive   # 保留舊名稱（實作在 sessions.py）


def pick_source(args, config, cwd_now, input_fn=input, err=sys.stderr, wt_session=None):
    clis = [args.from_cli] if args.from_cli else None
    if args.session:
        info = S.find_session(config, args.session, args.from_cli)
        if not info:
            raise HandoffError(f"找不到 session：{args.session}")
        return info
    if args.list:
        infos, notes = S.list_sessions(config, clis=clis)
        for n in notes:
            print(f"（{n}）", file=err)
        if not infos:
            raise HandoffError(f"最近 {config.get('list_hours', 24)} 小時沒有任何對話。")
        print(S.format_list(infos), file=err)
        try:
            # 提示要走 stderr：PowerShell 會吃掉 stdout，只留最後一行 JSON
            print("選哪一段？打編號（直接 Enter 取消）：", end="", file=err, flush=True)
            ans = input_fn("").strip()
        except EOFError:
            ans = ""
        if not ans.isdigit() or not (1 <= int(ans) <= len(infos)):
            raise HandoffError("已取消。")
        return infos[int(ans) - 1]
    # 沒有明確指定：先用這個 Windows Terminal 分頁的登記紀錄（SessionStart hook 寫的），多視窗不會接錯
    wt = os.environ.get("WT_SESSION") if wt_session is None else wt_session
    for e in ST.lookup(ST.state_dir(config), wt):
        if clis and e.get("cli") not in clis:
            continue
        info = S.find_session(config, e["session_id"], e.get("cli"))
        if info and not is_noninteractive(info):
            print(f"（依此分頁登記取得：{info.cli} {info.session_id}）", file=err)
            return info
    infos, _ = S.list_sessions(config, hours=config.get("cwd_lookup_hours", 168), clis=clis)
    cands = [s for s in infos if s.cwd and is_under(s.cwd, cwd_now) and not is_noninteractive(s)]
    if cands:
        # 同資料夾 24 小時內有好幾段互動對話：不猜，請使用者自己指定（猜錯會把別段對話交出去）
        amb_hours = config.get("ambiguous_hours", 24)
        recent = [s for s in cands if s.mtime >= time.time() - amb_hours * 3600]
        if len(recent) > 1:
            lines = "\n".join(f"  {s.cli} {s.session_id}（{s.mtime_str()}）{s.title or s.last_user or ''}" for s in recent[:6])
            raise HandoffError(
                f"{cwd_now} 最近 {amb_hours} 小時有 {len(recent)} 段互動對話，無法確定要交哪一段：\n{lines}\n"
                "請用 handoff --list 自己挑，或 --session <id>。")
        return cands[0]
    if args.from_cli == "agy":
        # agy skill 是在正要交出的那段對話裡執行的，那段剛被寫過；cwd 未知時取最近剛更新的那段
        fresh = config["adapters"]["agy"].get("recent_fallback_sec", 120)
        recent = [s for s in infos if not s.cwd and s.mtime >= time.time() - fresh]
        if len(recent) > 1:
            # 兩個 agy 視窗同時剛更新：不猜
            ids = "、".join(s.session_id for s in recent[:4])
            raise HandoffError(f"{fresh} 秒內有 {len(recent)} 段 agy 對話都剛更新（{ids}），無法確定要交哪一段。"
                               "請用 handoff --list 自己挑，或 --session <id>。")
        if recent:
            print(f"（agy 紀錄沒有工作資料夾，取 {fresh} 秒內剛更新的那段：{recent[0].session_id}）", file=err)
            return recent[0]
    raise HandoffError(f"{cwd_now} 最近 {config.get('cwd_lookup_hours', 168)} 小時沒有任何對話。"
                       "可用 handoff --list 自己挑，或 --session <id>。")


def run(argv, config=None, now=None, input_fn=input, out=None, err=None, cwd_now=None,
        wt_session=None, launch_fn=None):
    """回傳要執行的 dict {exe, args, cwd}。HandoffError 由呼叫端處理。"""
    out = out or sys.stdout
    err = err or sys.stderr
    ap = argparse.ArgumentParser(prog="handoff", description="在 Claude／Codex／agy 之間交接工作")
    ap.add_argument("target", nargs="?", help="claude | codex | agy；不給就照備援順序取下一家")
    ap.add_argument("--list", action="store_true", help="列出最近對話讓你選")
    ap.add_argument("--session", help="指定要交出的 session id")
    ap.add_argument("--from", dest="from_cli", choices=["claude", "codex", "agy"], help="限定來源 CLI")
    ap.add_argument("--dry-run", action="store_true", help="只印計畫，不匯入、不寫卡")
    ap.add_argument("--launch", choices=["tab"], help="自己在同視窗開新分頁啟動目標（不印 JSON）")
    ap.add_argument("--config", help="設定檔路徑（取代 private/chain.local.json）")
    args = ap.parse_args(argv)
    config = config or load_config(args.config)
    now = now or datetime.now()
    cwd_now = cwd_now or os.getcwd()

    info = pick_source(args, config, cwd_now, input_fn, err, wt_session)
    tr = T.read_transcript(info.cli, info.path, config["limits"].get("tool_summary_chars", 100))
    cwd = tr.cwd or info.cwd
    if not tr.cwd and info.cwd:
        tr.cwd = info.cwd   # agy 從 history.jsonl 補的 cwd 也寫進接力卡
    cwd_fallback = False
    if not cwd:
        cwd = cwd_now
        tr.cwd = cwd
        cwd_fallback = True   # 退回值不是真的工作資料夾：agy 一律拒絕
        tr.notes.append("來源紀錄沒有 cwd，改用執行 handoff 時的資料夾")
    target = choose_target(info.cli, cwd, config, args.target,
                           text=C.screening_text(tr, config), cwd_fallback=cwd_fallback)

    print(f"來源：{info.cli} {info.session_id}（{info.mtime_str()}）", file=err)
    print(f"工作資料夾：{cwd}", file=err)
    print(f"目標：{target}" + ("　[dry-run]" if args.dry_run else ""), file=err)

    card = C.make_card(tr, config, now=now, write=not args.dry_run)
    print(f"接力卡：{card.path}" + ("（dry-run，未寫入）" if args.dry_run else ""), file=err)
    print(f"對話：{len(tr.messages)} 則（使用者 {len(tr.user_messages())} 則）"
          + ("，曾 compact" if tr.compacted else ""), file=err)

    # --launch tab 多半從 hook 進來（使用者在 Claude 裡等著）：Codex 匯入逾時壓短
    plan = ADAPTERS[target].build_launch(card, info, cwd, config, dry_run=args.dry_run,
                                         hook=args.launch == "tab")
    for n in plan.pop("notes", []):
        print(f"- {n}", file=err)
    plan["target"] = target
    plan["card"] = card.path
    if not args.dry_run:
        sd = ST.state_dir(config)
        claude_id = info.session_id if info.cli == "claude" else card.claude_session
        if args.launch == "tab":
            # 啟動成功才寫／清標記：launch_fn 丟例外就不留下「已交出去」的假標記
            how = (launch_fn or L.launch_tab)(plan)
            plan["launched"] = how
            print(f"已在{'同視窗新分頁' if how == 'wt' else '新的 PowerShell 視窗'}啟動 {target}。", file=err)
        if target == "claude":
            ST.clear_active(sd, claude_id or "unknown")   # 只清這段的標記（沒有 id 時是 unknown 那份）
        else:
            ST.write_active(sd, target, card.path, claude_id, info.session_id, cwd, now)
        msg = ST.set_baton(config, target, f"handoff {info.cli} -> {target}")
        if msg:
            print(f"- {msg}", file=err)
            plan["baton"] = msg
    return plan


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    try:
        plan = run(sys.argv[1:] if argv is None else argv)
    except HandoffError as e:
        print(f"handoff：{e}", file=sys.stderr)
        return 2
    except T.UnsupportedTranscript as e:
        print(f"handoff：{e}", file=sys.stderr)
        return 2
    if plan.get("launched"):
        return 0
    # 最後一行 JSON；ensure_ascii 讓 PowerShell 5.1 不受主控台編碼影響
    print(json.dumps(plan, ensure_ascii=True))
    return 0
