"""在同一個 Windows Terminal 視窗開新分頁啟動目標 CLI。

用 PowerShell -EncodedCommand（UTF-16LE base64）傳指令：中文路徑、引號、換行、
wt 的分號分隔符號都不會出問題。
"""
import base64
import ctypes
import shutil
import subprocess
import sys
from ctypes import wintypes

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
CONSOLE = 0x00000010                  # CREATE_NEW_CONSOLE
QUOTES = "'‘’‚‛"  # PowerShell 把這些都當單引號


def ps_quote(s):
    out = "".join(c * 2 if c in QUOTES else c for c in str(s))
    return "'" + out + "'"


def build_ps_command(plan):
    # -ErrorAction Stop：切不到資料夾就整串中止，不要在錯的資料夾啟動目標 CLI
    parts = [f"Set-Location -LiteralPath {ps_quote(plan['cwd'])} -ErrorAction Stop",
             "& " + " ".join(ps_quote(x) for x in [plan["exe"]] + list(plan["args"]))]
    return "; ".join(parts)


def encode_command(cmd):
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def build_wt_argv(plan, wt_path):
    enc = encode_command(build_ps_command(plan))
    return [wt_path, "-w", "0", "new-tab", "-d", plan["cwd"],
            "powershell", "-NoExit", "-EncodedCommand", enc]


def build_ps_argv(plan):
    return ["powershell", "-NoExit", "-EncodedCommand", encode_command(build_ps_command(plan))]


def current_desktop():
    """這個程序所在的桌面名稱（一般是 Default）；非 Windows 或讀不到回空字串。"""
    if sys.platform != "win32":
        return ""
    try:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        h = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        need = wintypes.DWORD()
        if not u32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):  # 2＝UOI_NAME
            return ""
        return buf.value
    except (AttributeError, OSError):
        return ""


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def spawn_on_default_desktop(argv):
    """在使用者看得到的桌面（WinSta0\\Default）啟動 argv。subprocess 沒辦法指定桌面，只好直接呼叫 CreateProcessW。"""
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "WinSta0\\Default"
    pi = _PROCESS_INFORMATION()
    cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    k32 = ctypes.windll.kernel32
    if not k32.CreateProcessW(None, cmd, None, None, False, DETACHED, None, None,
                              ctypes.byref(si), ctypes.byref(pi)):
        raise ctypes.WinError()
    k32.CloseHandle(pi.hThread)
    k32.CloseHandle(pi.hProcess)


def launch_tab(plan, popen=subprocess.Popen, which=shutil.which, desktop=current_desktop,
               spawn_default=spawn_on_default_desktop):
    """開新分頁；回傳 "wt" 或 "powershell"（wt 找不到時退回另開 PowerShell 視窗）。"""
    wt = which("wt.exe") or which("wt")
    if wt:
        argv = build_wt_argv(plan, wt)
        name = desktop()
        if name and name.lower() != "default":
            # agy 的 run_command 跑在看不見的桌面（exebox-…），照常開的分頁使用者看不到
            spawn_default(argv)
        else:
            popen(argv, creationflags=DETACHED, close_fds=True,
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "wt"
    popen(build_ps_argv(plan), cwd=plan["cwd"], creationflags=CONSOLE, close_fds=True)
    return "powershell"
