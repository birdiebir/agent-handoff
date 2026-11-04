"""載入設定（config/chain.example.json 預設 + private/chain.local.json 本機覆蓋 + 環境變數），並提供路徑與工作資料夾判斷。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = ROOT / "config" / "chain.example.json"
LOCAL_CONFIG = ROOT / "private" / "chain.local.json"
DEFAULT_CONFIG = EXAMPLE_CONFIG   # 相容舊名稱

# 單一欄位的環境變數覆蓋（優先序最高）
ENV_OVERRIDES = {
    "HANDOFF_CARD_DIR": "card_dir",
    "HANDOFF_BATON_SCRIPT": "baton_script",
}


class HandoffError(Exception):
    """可預期的使用錯誤（訊息直接給使用者看）。"""


def _read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _merge(base, over):
    """dict 遞迴合併，其他型別（含 list）整個取代。"""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def local_config_path():
    """本機設定檔位置：環境變數 HANDOFF_LOCAL_CONFIG，否則 private/chain.local.json。"""
    env = os.environ.get("HANDOFF_LOCAL_CONFIG")
    return Path(expand(env)) if env else LOCAL_CONFIG


def load_config(path=None):
    """設定讀取順序（後者蓋前者）：
    config/chain.example.json（中性預設）< 本機設定檔（private/chain.local.json 或
    HANDOFF_LOCAL_CONFIG 指的檔，可只寫要改的欄位）< 環境變數（HANDOFF_CARD_DIR、
    HANDOFF_BATON_SCRIPT）。給 path（--config）時，改用該檔取代本機設定檔。"""
    cfg = _read_json(EXAMPLE_CONFIG)
    local = Path(path) if path else local_config_path()
    if local.is_file():
        cfg = _merge(cfg, _read_json(local))
    elif path:
        raise FileNotFoundError(path)
    for env, key in ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def expand(p):
    """展開 ~ 與環境變數。"""
    return os.path.expandvars(os.path.expanduser(str(p)))


def norm_path(p):
    """比較用的路徑正規化：絕對、小寫、反斜線、去尾斜線。"""
    if not p:
        return ""
    s = os.path.normpath(expand(p)).replace("/", "\\").rstrip("\\")
    return s.lower()


def is_under(child, parent):
    c, p = norm_path(child), norm_path(parent)
    if not c or not p:
        return False
    return c == p or c.startswith(p + "\\")


def work_keyword_hit(text, config):
    """text 含 config["work_keywords"] 任一關鍵字（不分大小寫）就回傳那個關鍵字，否則 None。"""
    low = (text or "").lower()
    for k in config.get("work_keywords", []):
        if k and k.lower() in low:
            return k
    return None


def is_work_path(cwd, config):
    """cwd 是否在工作路徑底下。cwd 未知時回傳 None（呼叫端要保守處理）。"""
    if not cwd:
        return None
    return any(is_under(cwd, w) for w in config.get("work_paths", []))
