# -*- coding: utf-8 -*-
"""
全屏提示横幅
===========
- 托盘图标左键单击：显示 / 关闭横幅
- 托盘图标右键菜单：显示/关闭、开机启动、退出
- 横幅显示时：屏蔽键盘（除 Esc 外），按 Esc 关闭（不屏蔽鼠标）
- 防锁死：横幅显示期间按 Ctrl+Alt+Shift+Esc 强制解除屏蔽并关闭横幅；
  托盘右键菜单里也有同一入口
- 每个显示器各显示一条横幅：优先用与屏幕分辨率完全一致的
  banner_{宽}x{高}.png（1:1 显示、不缩放）；没有精确图时按宽度铺满，
  上下未填满/超出的部分为纯黑（OLED 黑色像素不发光）
- 自定义图片：托盘菜单「选择自定义图片…」导入任意格式/尺寸的图，也可把
  banner.png / banner.jpg 等放进 %LOCALAPPDATA%\\Banner\\ 自动识别；
  与屏幕比例不一致时按 cover（铺满裁切）/ contain（完整显示）/ fill（拉伸）适配
- OLED 防烧屏：横幅定时微移几个像素、整体略降亮度；无人操作一段时间后
  切换为暗屏守护（全黑 + 一行游走的暗色小字），动鼠标、点击或按键立即恢复横幅
- 单实例运行：再次启动会让已运行实例切换横幅
- 空闲开销：横幅显示时 200ms 轮询、隐藏时 300ms 轮询；是否有人操作靠
  GetLastInputInfo 判断（鼠标点击/滚轮/按键都算），不再每帧轮询光标位置

运行数据位置：
- 日志/配置/生成的图片：%LOCALAPPDATA%\\Banner\\
- 读取的横幅图片仍在本脚本同目录（banner.png / banner_{宽}x{高}.png）

命令行参数：
- --quiet  开机自启项使用（保留兼容；启动气泡已不再弹出，因为通知浮层会盖住横幅）

横幅显示期间会临时隐藏输入法状态条（它处于比 topmost 更高的窗口层级），
关闭横幅后自动恢复。

依赖：Pillow（tkinter、ctypes 为标准库）
运行：双击 banner.pyw（无控制台窗口）
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
import os
import queue
import random
import sys
import threading
import time
import traceback
import winreg

import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

VERSION = "1.4"
QUIET = "--quiet" in sys.argv       # 开机自启用：不弹“已启动”气泡
_DEBUG_KEYS = os.environ.get("BANNER_DEBUG_KEYS") == "1"   # 排查用：记录钩子收到的每个按键
# 自检用：弹出出图确认条后自动选择要执行的动作（generate / cover）
_PROMPT_AUTOTEST = os.environ.get("BANNER_TEST_PROMPT_CHOICE", "")
if getattr(sys, "frozen", False):
    # 打包成 exe 后：可写/可替换的东西放 exe 旁边，只读资源（图片/图标）在 _internal 里
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = APP_DIR
_DATA_ROOT = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
DATA_DIR = os.path.join(_DATA_ROOT, "Banner")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
LOG_PATH = os.path.join(DATA_DIR, "banner.log")
CONFIG_PATH = os.path.join(DATA_DIR, "banner.json")
IMAGE_PATH = os.path.join(APP_DIR, "banner.png")          # 兜底横幅图（可被用户替换）
IMAGE_FALLBACKS = (os.path.join(RES_DIR, "banner.png"), os.path.join(DATA_DIR, "placeholder.png"))
TRAY_ICO = os.path.join(APP_DIR, "tray.ico")              # 托盘图标（缺失自动生成）
TRAY_ICO_FALLBACKS = (os.path.join(RES_DIR, "tray.ico"), os.path.join(DATA_DIR, "tray.ico"))

for _d in (DATA_DIR, CACHE_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError:
        pass


# ===================== 日志 =====================
MAX_LOG_BYTES = 1 << 20     # 超过 1MB 轮转一份 banner.log.1


def _maybe_rotate_log():
    try:
        if os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            old = LOG_PATH + ".1"
            if os.path.exists(old):
                os.remove(old)
            os.replace(LOG_PATH, old)
    except OSError:
        pass


def log(msg):
    """无控制台运行：信息写入 banner.log，避免 pythonw 下 print 报错。"""
    try:
        _maybe_rotate_log()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (ts, msg))
    except Exception:
        pass


# ===================== 配置 =====================
DEFAULT_CONFIG = {
    # ---- OLED 防烧屏 ----
    "oled_protect": True,        # False 则横幅静止常亮（旧行为）
    "idle_to_guard": 90,         # 无人操作多少秒后切换为暗屏守护
    "shift_interval": 30,        # 横幅每隔多少秒整体微移一次
    "shift_radius": 6,           # 微移半径（像素），沿一圈 8 个点轮换
    "brightness": 0.85,          # 横幅亮度系数（1.0 = 原图亮度）
    "guard_text": "任务运行中 · 请勿触动",
    "guard_color": "#4f5c56",    # 暗屏小字颜色，保持暗淡
    "guard_font": ["Microsoft YaHei UI", 14],
    "guard_move_interval": 12,   # 暗屏小字每隔多少秒换个位置
    "hide_ime_bar": True,        # 横幅显示期间隐藏输入法状态条（它压不住）
    # ---- 出图：屏幕分辨率没有精确匹配的图片时，自动按该分辨率生成 ----
    "auto_generate": True,       # 总开关；False 则一律按宽度铺满（允许裁切）
    "ask_before_generate": True, # True 时先弹非模态确认条询问
    "banner_status": "// SYSTEM STATUS : BUSY",
    "banner_title": ["此计算机", "正在运行任务"],
    "banner_plate": "请 勿 触 动",
    "banner_sub": ["任务执行中，触碰键盘或鼠标可能导致数据丢失或系统异常",
                   "感谢你的理解与配合 —— 让它安静地跑完吧。"],
    # ---- 自定义图片：导入自己的横幅底图（任意格式/尺寸） ----
    "banner_image": "",          # 自定义图片绝对路径；空 = 不用
    "banner_fit": "cover",       # 比例不一致时：cover 铺满裁切 / contain 完整显示 / fill 拉伸
}


# ---- 自定义图片适配方式 ----
FIT_COVER, FIT_CONTAIN, FIT_FILL = "cover", "contain", "fill"
FIT_CHOICES = (FIT_COVER, FIT_CONTAIN, FIT_FILL)
FIT_LABELS = {FIT_COVER: "铺满裁切", FIT_CONTAIN: "完整显示", FIT_FILL: "拉伸铺满"}


def _normalize_fit(value):
    v = str(value or "").strip().lower()
    return v if v in FIT_CHOICES else FIT_COVER


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_CONFIG:
                    if k in data:
                        cfg[k] = data[k]
                for k in data:
                    if k not in DEFAULT_CONFIG:
                        log("配置项 %r 未知，已忽略" % k)
                # 版本升级后把新增的默认项补写进配置文件，方便用户看到可调项
                merged = dict(data)
                for k, v in DEFAULT_CONFIG.items():
                    merged.setdefault(k, v)
                if merged != data:
                    try:
                        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                            json.dump(merged, f, ensure_ascii=False, indent=2)
                        added = ", ".join(k for k in DEFAULT_CONFIG if k not in data)
                        log("配置已补充新增默认项：%s" % added)
                    except Exception as e:
                        log("配置回写失败：%s" % e)
        except Exception as e:
            log("读取配置失败，使用默认配置：%s" % e)
    else:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            log("已生成默认配置：%s" % CONFIG_PATH)
        except Exception as e:
            log("写入默认配置失败：%s" % e)
    return cfg


CFG = load_config()

# ---- OLED 防烧屏（自配置读取） ----
OLED_PROTECT = bool(CFG["oled_protect"])
IDLE_TO_GUARD = float(CFG["idle_to_guard"])
SHIFT_INTERVAL = float(CFG["shift_interval"])
SHIFT_RADIUS = int(CFG["shift_radius"])
BANNER_BRIGHTNESS = float(CFG["brightness"])
GUARD_TEXT = str(CFG["guard_text"])
GUARD_COLOR = str(CFG["guard_color"])
GUARD_FONT = tuple(CFG["guard_font"])
GUARD_MOVE_INTERVAL = float(CFG["guard_move_interval"])
HIDE_IME_BAR = bool(CFG["hide_ime_bar"])
AUTO_GENERATE = bool(CFG["auto_generate"])
ASK_BEFORE_GENERATE = bool(CFG["ask_before_generate"])
BANNER_IMAGE = str(CFG.get("banner_image") or "").strip()
BANNER_FIT = _normalize_fit(CFG.get("banner_fit"))

# ===================== 全局状态 =====================
commands = queue.Queue()       # 线程间命令：("toggle",) / ("esc",) / ("activity",) / ("exit",)
keyboard_blocked = False       # 当前是否屏蔽键盘
hook_ready = False             # 键盘钩子是否安装成功
hook_failed = False            # 键盘钩子安装失败
hook_warned = False            # 是否已提示过钩子失败
banner_windows = []            # 每个显示器一项：窗口、画布及其上的图片/小字
banner_images = []             # PhotoImage 引用（防垃圾回收）
banner_mode = None             # None / "full"（横幅）/ "guard"（暗屏守护）
last_activity = 0.0            # 最近一次鼠标/键盘活动（time.monotonic）
_mods_down = set()             # 当前按住的修饰键（钩子里自己跟踪，被屏蔽的按键系统不更新状态）
_last_input_dw = None          # GetLastInputInfo 上次的 dwTime
TICK_VISIBLE = 200             # 横幅显示时的轮询间隔（毫秒）
TICK_IDLE = 300                # 横幅关闭时的轮询间隔（毫秒）
next_shift_at = 0.0
next_guard_move_at = 0.0
_next_topmost_at = 0.0
_menu_open = False             # 托盘菜单打开期间不要重申置顶，否则会盖住菜单
shift_index = 0
_quitting = False
_stopping = False
_shutdown_done = False
SHIFT_ORBIT = [(round(SHIFT_RADIUS * math.cos(k * math.pi / 4)),
                round(SHIFT_RADIUS * math.sin(k * math.pi / 4))) for k in range(8)]

# ===================== Win32 常量 =====================
# 托盘
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_NULL = 0x0000
WM_QUIT = 0x0012
WM_TRAYICON = WM_USER + 20
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
HWND_MESSAGE = -3
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_GRAYED = 0x0001
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
IDM_TOGGLE = 1001
IDM_EXIT = 1002
IDM_STARTUP = 1003
IDM_FORCE_UNLOCK = 1004
IDM_INFO = 1005
IDM_REGEN = 1006
IDM_PICK_IMAGE = 1007
IDM_CLEAR_IMAGE = 1008
IDM_FIT_COVER = 1101
IDM_FIT_CONTAIN = 1102
IDM_FIT_FILL = 1103
FIT_MENU_IDS = {FIT_COVER: IDM_FIT_COVER, FIT_CONTAIN: IDM_FIT_CONTAIN, FIT_FILL: IDM_FIT_FILL}
NIM_MODIFY = 0x00000001
NIM_SETVERSION = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NOTIFYICON_VERSION = 3   # 必须是 3：设成 4(NOTIFYICON_VERSION_4) 会改变回调消息格式，导致点击无效

# 键盘钩子
WH_KEYBOARD_LL = 13
VK_ESCAPE = 0x1B
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12       # Alt
# 低级键盘钩子里修饰键上报的是左右具体键码（0xA0~0xA5），不是 0x10~0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
_MOD_VK = {
    VK_SHIFT: VK_SHIFT, VK_LSHIFT: VK_SHIFT, VK_RSHIFT: VK_SHIFT,
    VK_CONTROL: VK_CONTROL, VK_LCONTROL: VK_CONTROL, VK_RCONTROL: VK_CONTROL,
    VK_MENU: VK_MENU, VK_LMENU: VK_MENU, VK_RMENU: VK_MENU,
}
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# 单实例 / 线程通信
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
MUTEX_NAME = "Local\\BannerOverlay.SingleInstance"
TOGGLE_EVENT_NAME = "Local\\BannerOverlay.ToggleEvent"

# 置顶维持：任务栏、输入法状态条、通知浮层都是 topmost 窗口，
# 会在通知弹出等时机排到横幅上面，所以横幅显示期间要周期性重申置顶
GA_ROOT = 2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
HWND_TOPMOST = wt.HWND(-1)
TOP_REASSERT_INTERVAL = 1.0      # 秒
# 输入法状态条处于比普通 topmost 更高的窗口层级，压不住，只能在横幅显示期间隐藏、
# 关闭横幅时恢复。类名按关键字匹配（微信输入法 wetype.statusbar.window 等）。
OVERLAY_KEYWORDS = ("statusbar", "status_bar", "imebar", "ime_bar")
OVERLAY_MAX_W, OVERLAY_MAX_H = 800, 120
SW_HIDE, SW_SHOW = 0, 5

# ===================== Win32 结构与函数签名 =====================
WPARAM_T = ctypes.c_size_t
LPARAM_T = ctypes.c_ssize_t
LRESULT_T = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", WPARAM_T),
        ("lParam", LPARAM_T),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG), ("top", wt.LONG),
        ("right", wt.LONG), ("bottom", wt.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wt.DWORD),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("dwTime", wt.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT_T, wt.HWND, wt.UINT, WPARAM_T, LPARAM_T)
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(LRESULT_T, ctypes.c_int, WPARAM_T, LPARAM_T)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---- 托盘 / 窗口相关 ----
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.DefWindowProcW.restype = LRESULT_T
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wt.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT_T
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.PostMessageW.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, WPARAM_T, LPARAM_T]
user32.PostThreadMessageW.restype = wt.BOOL

# ---- 托盘图标 ----
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

# ---- 托盘右键菜单 ----
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, WPARAM_T, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL

# ---- 键盘钩子 ----
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc, wt.HMODULE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, WPARAM_T, LPARAM_T]
user32.CallNextHookEx.restype = LRESULT_T
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL

# ---- 模块句柄 / 显示器 ----
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [ENUMWINDOWSPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL

# ---- 单实例 / 事件 / 线程 ----
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
kernel32.OpenEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.SetEvent.argtypes = [wt.HANDLE]
kernel32.SetEvent.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GetTickCount.restype = wt.DWORD
kernel32.GetLastError.restype = wt.DWORD


# ===================== DPI 感知 =====================
def set_dpi_awareness():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass


# ===================== 显示器枚举 =====================
def get_monitors():
    result = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)

    def _cb(hmon, hdc, rect, data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        result.append((r.left, r.top, r.right, r.bottom))
        return True

    proc = MonitorEnumProc(_cb)
    user32.EnumDisplayMonitors.argtypes = [
        wt.HDC, ctypes.POINTER(RECT), MonitorEnumProc, wt.LPARAM]
    user32.EnumDisplayMonitors.restype = wt.BOOL
    user32.EnumDisplayMonitors(None, None, proc, 0)
    return result


# ===================== 单实例 =====================
_single_handle = None       # 本进程持有的互斥量句柄
_toggle_event = None        # 供二次启动通知本进程切换横幅
_tray_tid = 0
_hook_tid = 0


def acquire_single_instance():
    """返回 True 表示本进程是唯一实例；False 表示已有实例在运行。"""
    global _single_handle
    _single_handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not _single_handle:
        log("警告：单实例互斥量创建失败，按唯一实例继续启动")
        return True
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(_single_handle)
        _single_handle = None
        return False
    return True


def notify_existing_instance():
    """通知已运行实例切换横幅；成功返回 True。"""
    h = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, TOGGLE_EVENT_NAME)
    if not h:
        log("未能联系到已运行实例（事件不存在）")
        return False
    try:
        kernel32.SetEvent(h)
    finally:
        kernel32.CloseHandle(h)
    return True


def _create_toggle_event():
    global _toggle_event
    _toggle_event = kernel32.CreateEventW(None, False, False, TOGGLE_EVENT_NAME)
    if not _toggle_event:
        log("警告：线程通信事件创建失败，二次启动将无法切换横幅")


def _event_thread():
    """等待二次启动的通知，转成主线程命令。"""
    while not _stopping:
        try:
            r = kernel32.WaitForSingleObject(_toggle_event, 500)
        except Exception:
            log("事件等待异常:\n" + traceback.format_exc())
            return
        if r == WAIT_OBJECT_0 and not _stopping:
            commands.put(("toggle",))


# ===================== 横幅图片解析 =====================
_photo_cache = {}           # (path, mtime, mw, mh, exact, brightness) -> (PhotoImage, y0)
_fallback_image = None      # 程序目录不可写时，占位图改放数据目录


def _file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


# ---- 自定义图片：导入、适配、渲染缓存 ----
USER_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif", ".gif", ".tif", ".tiff")
USER_IMAGE_STEMS = ("banner_custom", "banner")    # 固定目录投图的文件名（不含后缀）


def _dropin_image_path():
    """固定目录里的投图（banner.* / banner_custom.*），无需菜单即可导入。"""
    for stem in USER_IMAGE_STEMS:
        for ext in USER_IMAGE_EXTS:
            p = os.path.join(DATA_DIR, stem + ext)
            if os.path.isfile(p):
                return p
    return None


def _user_image_path():
    """当前生效的自定义横幅图：配置里的路径优先，其次固定目录投图。"""
    if BANNER_IMAGE and os.path.isfile(BANNER_IMAGE):
        return BANNER_IMAGE
    return _dropin_image_path()


def _shift_margin():
    """自定义图四周预留的安全边，抵消 30s 微移在屏幕边缘露出的黑边。"""
    return SHIFT_RADIUS if (OLED_PROTECT and SHIFT_RADIUS > 0) else 0


def _open_rgb(path, target=None):
    """打开图片并转成 RGB；带透明通道的图合成到黑底（横幅本身是黑底）。"""
    im = Image.open(path)
    if target:
        try:
            im.draft("RGB", target)      # JPEG 可先降采样，避免超大图全量解码
        except Exception:
            pass
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def _render_custom_image(src, mw, mh, fit):
    """把自定义图渲染成目标屏幕尺寸（含微移安全边）。返回 RGB Image。"""
    m = _shift_margin()
    tw, th = mw + m * 2, mh + m * 2
    im = _open_rgb(src, (tw, th))
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        raise ValueError("图片尺寸无效：%r" % (im.size,))
    if fit == FIT_FILL:
        out = im.resize((tw, th), Image.LANCZOS)
    elif fit == FIT_CONTAIN:
        k = min(tw / float(iw), th / float(ih))
        nw, nh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
        out = Image.new("RGB", (tw, th), (0, 0, 0))
        out.paste(im.resize((nw, nh), Image.LANCZOS), ((tw - nw) // 2, (th - nh) // 2))
    else:  # cover：等比放大到能盖住目标区域，再居中裁切
        k = max(tw / float(iw), th / float(ih))
        nw, nh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
        big = im.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - tw) // 2, (nh - th) // 2
        out = big.crop((left, top, left + tw, top + th))
    if OLED_PROTECT and BANNER_BRIGHTNESS < 1.0:
        out = ImageEnhance.Brightness(out).enhance(BANNER_BRIGHTNESS)
    return out


def _custom_cache_path(src, mw, mh, fit):
    """自定义图渲染结果的磁盘缓存名；源图改动/换适配方式会自动失效。"""
    try:
        st = os.stat(src)
        sig = "%s|%d|%d|%s|%dx%d|%.4f|%d" % (
            os.path.normcase(os.path.abspath(src)), st.st_size, int(st.st_mtime),
            fit, mw, mh, BANNER_BRIGHTNESS, _shift_margin())
    except OSError:
        sig = "missing|%s" % src
    digest = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, "custom_%s_%dx%d.png" % (digest, mw, mh))


def _custom_photo(src, mw, mh, fit):
    """取自定义图的 PhotoImage，返回 (photo, x0, y0)；命中磁盘缓存直接读。"""
    m = _shift_margin()
    want = (mw + m * 2, mh + m * 2)
    cache = _custom_cache_path(src, mw, mh, fit)
    img = None
    if os.path.isfile(cache):
        try:
            cached = Image.open(cache).convert("RGB")
            if cached.size == want:
                img = cached
        except Exception:
            log("自定义横幅缓存读取失败，重新渲染：%s" % cache)
    if img is None:
        img = _render_custom_image(src, mw, mh, fit)
        try:
            tmp = cache[:-4] + ".part.png"
            img.save(tmp)
            os.replace(tmp, cache)
            log("已生成自定义横幅缓存：%s" % cache)
        except Exception as e:
            log("自定义横幅缓存写入失败（不影响显示）：%s" % e)
    return ImageTk.PhotoImage(img), -m, -m


def _legacy_image_for_monitor(mw, mh):
    for d in (APP_DIR, RES_DIR, CACHE_DIR):
        p = os.path.join(d, "banner_%dx%d.png" % (mw, mh))
        if os.path.isfile(p):
            return p, True
    for p in (IMAGE_PATH,) + IMAGE_FALLBACKS:
        if os.path.isfile(p):
            return p, False
    return None, False


def image_for_monitor(mw, mh):
    """为显示器挑选横幅图片。

    返回 (path, exact, fit)：
    - 用户导入的自定义图优先，fit 为 cover / contain / fill；
    - 否则用与屏幕像素尺寸完全一致的 banner_{宽}x{高}.png（exact=True，1:1 显示）；
    - 都没有则回退 banner.png（按宽度铺满，高度超出部分居中裁切）。
    找不到任何图片时返回 (None, False, None)。
    """
    src = _user_image_path()
    if src:
        return src, False, BANNER_FIT
    path, exact = _legacy_image_for_monitor(mw, mh)
    return path, exact, None


def get_photo(path, mw, mh, exact, fit=None):
    """取（并缓存）某块屏幕要用的 PhotoImage。返回 (photo, x0, y0)。"""
    key = (path, _file_mtime(path), mw, mh, exact, fit, BANNER_BRIGHTNESS)
    hit = _photo_cache.get(key)
    if hit is not None:
        return hit
    if fit:
        photo, x0, y0 = _custom_photo(path, mw, mh, fit)
    else:
        img = Image.open(path).convert("RGB")
        if exact and img.size != (mw, mh):
            # 文件名对得上但实际像素不对，退回铺满，避免拉伸变形
            exact = False
        if OLED_PROTECT and BANNER_BRIGHTNESS < 1.0:
            img = ImageEnhance.Brightness(img).enhance(BANNER_BRIGHTNESS)
        x0 = 0
        if exact:
            y0 = 0
        else:
            iw, ih = img.size
            bh = max(1, round(mw * ih / iw))
            img = img.resize((mw, bh), Image.LANCZOS)
            y0 = (mh - bh) // 2
        photo = ImageTk.PhotoImage(img)
    if len(_photo_cache) >= 4:
        _photo_cache.clear()
    _photo_cache[key] = (photo, x0, y0)
    return photo, x0, y0


# ===================== 出图（分辨率不匹配时自动生成） =====================
_generating = False
_prompt_win = None
_prompt_widgets = {}


def _banner_text_cfg():
    return {
        "status": str(CFG.get("banner_status", "")),
        "title": [str(x) for x in CFG.get("banner_title", [])][:2],
        "plate": str(CFG.get("banner_plate", "")),
        "sub": [str(x) for x in CFG.get("banner_sub", [])][:2],
    }


def _missing_generation():
    """返回当前显示器中缺少精确尺寸图片的 (宽,高) 列表（去重）。

    已启用自定义图片时不需要出图，直接返回空列表。
    """
    if _user_image_path():
        return []
    out, seen = [], set()
    for (left, top, right, bottom) in get_monitors():
        mw, mh = right - left, bottom - top
        if (mw, mh) in seen:
            continue
        path, exact, _fit = image_for_monitor(mw, mh)
        if not exact:
            seen.add((mw, mh))
            out.append((mw, mh))
    return out


def _update_config(updates):
    global CFG, AUTO_GENERATE, ASK_BEFORE_GENERATE
    CFG.update(updates)
    if "auto_generate" in updates:
        AUTO_GENERATE = bool(updates["auto_generate"])
    if "ask_before_generate" in updates:
        ASK_BEFORE_GENERATE = bool(updates["ask_before_generate"])
    try:
        data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        data.update(updates)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log("配置已保存：%s" % ", ".join("%s=%r" % kv for kv in updates.items()))
    except Exception as e:
        log("配置保存失败：%s" % e)


def _generate_one(mw, mh):
    try:
        import make_banner
    except Exception as e:
        log("无法出图：导入 make_banner 失败（%s）" % e)
        return False
    out = os.path.join(CACHE_DIR, "banner_%dx%d.png" % (mw, mh))
    tmp = out[:-4] + ".part.png"      # 保留 .png 后缀，PIL 靠后缀判断格式
    try:
        make_banner.render(tmp, mw, mh, text=_banner_text_cfg())
        os.replace(tmp, out)
        log("已生成横幅：%s" % out)
        return True
    except Exception:
        log("生成横幅失败 %dx%d:\n%s" % (mw, mh, traceback.format_exc()))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _generation_worker(sizes):
    global _generating
    ok = True
    for (mw, mh) in sizes:
        if not _generate_one(mw, mh):
            ok = False
    _generating = False
    commands.put(("generated", ok))


def _start_generation(sizes=None):
    global _generating
    if _generating:
        log("已有出图任务在进行中，忽略本次请求")
        return
    if sizes is None:
        sizes = _missing_generation()
    if not sizes:
        log("所有显示器都已有精确尺寸图片，无需出图")
        return
    _generating = True
    log("开始生成横幅：%s" % ", ".join("%dx%d" % s for s in sizes))
    threading.Thread(target=_generation_worker, args=(sizes,), daemon=True).start()


def _refresh_banner_images():
    """出图/换图完成后把当前横幅换成新图片（不重启、不闪屏）。"""
    for b in banner_windows:
        try:
            path, exact, fit = image_for_monitor(b["mw"], b["mh"])
            if not path:
                continue
            photo, x0, y0 = get_photo(path, b["mw"], b["mh"], exact, fit)
            b["cv"].itemconfigure(b["img"], image=photo)
            b["cv"].coords(b["img"], x0, y0)
            b["x0"], b["y0"] = x0, y0
            banner_images.append(photo)
        except Exception:
            log("替换横幅图片失败:\n" + traceback.format_exc())


def _hide_generate_prompt():
    global _prompt_win, _prompt_widgets
    if _prompt_win is not None:
        try:
            _prompt_win.destroy()
        except Exception:
            pass
    _prompt_win = None
    _prompt_widgets = {}


def _show_generate_prompt(sizes):
    """屏幕右下角弹非模态确认条：重新出图 / 本次铺满 / 不再询问。"""
    global _prompt_win, _prompt_widgets
    _hide_generate_prompt()
    monitors = get_monitors()
    if not monitors:
        return
    left, top, right, bottom = monitors[0]
    pw, ph = 760, 190
    x, y = right - pw - 24, bottom - ph - 24
    try:
        win = tk.Toplevel(root)
    except Exception:
        return
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg="#111b18")
    win.geometry("%dx%d+%d+%d" % (pw, ph, x, y))
    frame = tk.Frame(win, bg="#111b18", padx=20, pady=16)
    frame.pack(fill="both", expand=True)
    msg = tk.Label(
        frame,
        text="这块屏幕（%s）还没有精确尺寸的横幅图片。\n"
             "重新出图会按当前分辨率生成（约 2–3 秒）；本次铺满则上下可能被裁。"
             % "、".join("%d×%d" % s for s in sizes),
        bg="#111b18", fg="#d7e6df", justify="left", anchor="w",
        font=("Microsoft YaHei UI", 13))
    msg.pack(fill="x")
    row = tk.Frame(frame, bg="#111b18")
    row.pack(side="bottom", fill="x", pady=(12, 0))
    var = tk.BooleanVar(value=False)
    btn_font = ("Microsoft YaHei UI", 11)
    btn_gen = tk.Button(row, text="重新出图", width=10, font=btn_font, padx=10, pady=4,
                        command=lambda: _prompt_choice("generate"))
    btn_cover = tk.Button(row, text="本次铺满", width=10, font=btn_font, padx=10, pady=4,
                          command=lambda: _prompt_choice("cover"))
    chk = tk.Checkbutton(row, text="不再询问", variable=var,
                         bg="#111b18", fg="#9fb4ab",
                         activebackground="#111b18", activeforeground="#d7e6df",
                         selectcolor="#111b18", highlightthickness=0, bd=0,
                         font=("Microsoft YaHei UI", 10))
    btn_gen.pack(side="left")
    btn_cover.pack(side="left", padx=(10, 0))
    chk.pack(side="left", padx=(16, 0))
    win.update_idletasks()
    _prompt_win = win
    _prompt_widgets = dict(win=win, msg=msg, btn_gen=btn_gen, btn_cover=btn_cover,
                           var=var, hwnd=_banner_hwnd(win))
    log("已弹出出图确认条：%s" % ", ".join("%dx%d" % s for s in sizes))
    if _PROMPT_AUTOTEST in ("generate", "cover"):
        log("自检：0.8s 后自动选择 %s" % _PROMPT_AUTOTEST)
        root.after(800, lambda: _prompt_choice(_PROMPT_AUTOTEST))


def _prompt_choice(kind):
    var = _prompt_widgets.get("var")
    remember = bool(var.get()) if var is not None else False
    if kind == "generate":
        if remember:
            _update_config({"ask_before_generate": False})
        _start_generation()
        w = _prompt_widgets
        if w:
            try:
                w["msg"].configure(text="正在按当前分辨率生成横幅（约 2–3 秒）…")
                w["btn_gen"].configure(state="disabled")
                w["btn_cover"].configure(state="disabled")
            except Exception:
                pass
    else:
        if remember:
            # 以后直接铺满，不再出图也不再多问
            _update_config({"ask_before_generate": False, "auto_generate": False})
        _hide_generate_prompt()


def _maybe_offer_generation():
    if not AUTO_GENERATE:
        return
    missing = _missing_generation()
    if not missing:
        return
    if ASK_BEFORE_GENERATE:
        _show_generate_prompt(missing)
    else:
        _start_generation(missing)


# ===================== 横幅显示 / 关闭 =====================
def _banner_hwnd(win):
    """取窗口真正的顶层 HWND（Tk 的 winfo_id 是子窗口，要往上找 GA_ROOT）。"""
    try:
        hwnd = int(win.winfo_id())
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)
    except Exception:
        return 0


def _raise_banner_above():
    """周期性把横幅重新插到 topmost 层顶部。

    任务栏(Shell_TrayWnd)、输入法状态条、通知浮层都是 topmost 窗口，
    通知弹出时会把它们抬到横幅上面，所以横幅显示期间每秒重申一次。
    托盘菜单打开时跳过，否则会把菜单盖住。
    """
    if _menu_open:
        return
    for b in banner_windows:
        hwnd = b.get("hwnd") or 0
        if not hwnd:
            continue
        user32.SetWindowPos(wt.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
    _hide_ime_overlays()
    # 出图确认条要始终压在横幅上面，否则每秒重申置顶会把它盖住
    if _prompt_win is not None:
        phwnd = _prompt_widgets.get("hwnd") or 0
        if phwnd:
            user32.SetWindowPos(wt.HWND(phwnd), HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)


_hidden_overlays = set()      # 被临时隐藏的输入法状态条窗口


def _find_ime_overlays():
    """找出当前可见的输入法状态条窗口。"""
    found = []

    def _cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(160)
            user32.GetClassNameW(hwnd, cls, 160)
            low = cls.value.lower()
            if not any(k in low for k in OVERLAY_KEYWORDS):
                return True
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return True
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if 0 < w <= OVERLAY_MAX_W and 0 < h <= OVERLAY_MAX_H:
                found.append(int(hwnd))
        except Exception:
            pass
        return True

    proc = ENUMWINDOWSPROC(_cb)
    user32.EnumWindows(proc, 0)
    return found


def _hide_ime_overlays():
    """横幅显示期间隐藏输入法状态条（它比横幅的 topmost 层级更高）。"""
    if not HIDE_IME_BAR or not banner_windows:
        return
    for hwnd in _find_ime_overlays():
        if user32.ShowWindow(wt.HWND(hwnd), SW_HIDE):
            _hidden_overlays.add(hwnd)


def _restore_ime_overlays():
    for hwnd in list(_hidden_overlays):
        try:
            if user32.IsWindow(wt.HWND(hwnd)):
                user32.ShowWindow(wt.HWND(hwnd), SW_SHOW)
        except Exception:
            pass
    _hidden_overlays.clear()


def show_banner():
    global keyboard_blocked
    hide_banner()
    monitors = get_monitors()
    if not monitors:
        log("警告：未找到任何显示器")
        return
    prepared = []
    for (left, top, right, bottom) in monitors:
        mw, mh = right - left, bottom - top
        path, exact, fit = image_for_monitor(mw, mh)
        if path is None:
            log("警告：没有可用的横幅图片（缺少 %s）" % IMAGE_PATH)
            return
        try:
            photo, x0, y0 = get_photo(path, mw, mh, exact, fit)
        except Exception as e:
            if not fit:
                log("横幅图片加载失败 %s: %s" % (path, e))
                return
            # 自定义图读不出来（损坏/格式不支持）时，本屏退回默认横幅，不整块黑板
            log("自定义图片加载失败（%s），本屏改用默认横幅：%s" % (path, e))
            path, exact = _legacy_image_for_monitor(mw, mh)
            fit = None
            if path is None:
                return
            try:
                photo, x0, y0 = get_photo(path, mw, mh, exact, None)
            except Exception as e2:
                log("横幅图片加载失败 %s: %s" % (path, e2))
                return
        prepared.append((left, top, mw, mh, photo, x0, y0,
                         os.path.basename(path), exact, fit))
    for (left, top, mw, mh, photo, x0, y0, name, exact, fit) in prepared:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="black")
        win.geometry("%dx%d+%d+%d" % (mw, mh, left, top))
        cv = tk.Canvas(win, width=mw, height=mh, bg="black", bd=0, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        img_item = cv.create_image(x0, y0, image=photo, anchor="nw")
        text_item = cv.create_text(mw // 2, mh // 2, text=GUARD_TEXT, fill=GUARD_COLOR,
                                   font=GUARD_FONT, state="hidden")
        banner_windows.append(dict(win=win, cv=cv, img=img_item, text=text_item,
                                   mw=mw, mh=mh, x0=x0, y0=y0, hwnd=0))
        win.update_idletasks()
        banner_windows[-1]["hwnd"] = _banner_hwnd(win)
        banner_images.append(photo)
    keyboard_blocked = hook_ready
    _mods_down.clear()          # 防止上一次遗留的修饰键状态误触发组合键
    _enter_full()
    _raise_banner_above()
    detail = "；".join(
        "%dx%d%s" % (p[2], p[3],
                     ("（%s）" % FIT_LABELS.get(p[9], p[9])) if p[9]
                     else ("" if p[8] else "（铺满）"))
        for p in prepared)
    log("横幅已显示：%d 屏 %s" % (len(banner_windows), detail))
    if not hook_ready and not hook_failed:
        log("提示：键盘钩子尚未就绪，本次显示暂不屏蔽键盘")
    _maybe_offer_generation()


def hide_banner():
    global keyboard_blocked, banner_mode
    was_visible = bool(banner_windows)
    keyboard_blocked = False
    banner_mode = None
    for b in banner_windows:
        try:
            b["win"].destroy()
        except Exception:
            pass
    banner_windows.clear()
    banner_images.clear()
    _hide_generate_prompt()
    if _hidden_overlays:
        _restore_ime_overlays()   # 恢复被隐藏的输入法状态条
    if was_visible:
        log("横幅已关闭")


# ===================== OLED 防烧屏 =====================
def _enter_full():
    """显示完整横幅（亮屏状态）。"""
    global banner_mode, last_activity, next_shift_at, shift_index
    banner_mode = "full"
    now = time.monotonic()
    last_activity = now
    next_shift_at = now + SHIFT_INTERVAL
    shift_index = 0
    for b in banner_windows:
        b["cv"].coords(b["img"], b["x0"], b["y0"])
        b["cv"].itemconfigure(b["img"], state="normal")
        b["cv"].itemconfigure(b["text"], state="hidden")
        b["cv"].configure(cursor="")


def _enter_guard():
    """暗屏守护：全黑，只留一行游走的暗色小字。"""
    global banner_mode
    banner_mode = "guard"
    for b in banner_windows:
        b["cv"].itemconfigure(b["img"], state="hidden")
        b["cv"].itemconfigure(b["text"], state="normal")
        b["cv"].configure(cursor="none")
    _move_guard_text()
    _hide_generate_prompt()      # 暗屏守护期间不留亮色确认条
    idle = _input_idle_seconds()
    log("进入暗屏守护（已空闲 %s）" % ("--" if idle is None else "%.0fs" % idle))


def _move_guard_text():
    global next_guard_move_at
    for b in banner_windows:
        x = random.randint(int(b["mw"] * 0.15), int(b["mw"] * 0.85))
        y = random.randint(int(b["mh"] * 0.10), int(b["mh"] * 0.90))
        b["cv"].coords(b["text"], x, y)
    next_guard_move_at = time.monotonic() + GUARD_MOVE_INTERVAL


def _shift_banner():
    """横幅沿小圆周轮换位置，避免同一批像素长期显示同一内容。"""
    global shift_index, next_shift_at
    shift_index += 1
    dx, dy = SHIFT_ORBIT[shift_index % len(SHIFT_ORBIT)]
    for b in banner_windows:
        b["cv"].coords(b["img"], b["x0"] + dx, b["y0"] + dy)
    next_shift_at = time.monotonic() + SHIFT_INTERVAL


def _on_activity():
    global last_activity
    last_activity = time.monotonic()
    if banner_mode == "guard":
        _enter_full()


def _input_dw_time():
    """系统最后一次输入事件的 tick（毫秒）；失败返回 None。"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return lii.dwTime


def _input_idle_seconds():
    """距最后一次键鼠输入过去了多少秒；失败返回 None。

    用 GetLastInputInfo 而不是轮询光标：鼠标点击、滚轮、按键都算活动，
    并且每次 tick 只有一次系统调用（不再每 50ms 查一次光标坐标）。
    """
    tick = _input_dw_time()
    if tick is None:
        return None
    return ((kernel32.GetTickCount() - tick) & 0xFFFFFFFF) / 1000.0


def _oled_tick():
    global _last_input_dw, _next_topmost_at
    if not banner_windows:
        return
    now = time.monotonic()
    if now >= _next_topmost_at:
        _raise_banner_above()
        _next_topmost_at = now + TOP_REASSERT_INTERVAL
    tick = _input_dw_time()
    if tick is not None:
        if _last_input_dw is not None and tick != _last_input_dw:
            _on_activity()          # 有新的键鼠输入（点击、滚轮、按键都算）
        _last_input_dw = tick
    if not OLED_PROTECT:
        return
    if banner_mode == "full":
        # 从横幅亮起（或最近一次操作）算起，空闲够久才进入暗屏守护，
        # 保证手动切出横幅时不会立刻变黑
        if now - last_activity >= IDLE_TO_GUARD:
            _enter_guard()
        elif now >= next_shift_at:
            _shift_banner()
    elif banner_mode == "guard" and now >= next_guard_move_at:
        _move_guard_text()


def toggle_banner():
    if banner_windows:
        hide_banner()
    else:
        show_banner()


# ===================== 键盘钩子 =====================
_hook_proc = None
_hook_handle = None


def _keyboard_proc(nCode, wParam, lParam):
    if nCode >= 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        up = wParam in (WM_KEYUP, WM_SYSKEYUP)
        # 按键被拦下时系统不会更新修饰键状态，所以这里自己跟踪；
        # 左右修饰键要归一到 SHIFT/CONTROL/MENU
        mod = _MOD_VK.get(vk)
        if mod is not None:
            if down:
                _mods_down.add(mod)
            elif up:
                _mods_down.discard(mod)
        if keyboard_blocked:
            if _DEBUG_KEYS:
                log("KEY vk=0x%02X wparam=0x%04X mods=%s" % (vk, wParam, sorted(_mods_down)))
            if down:
                if vk == VK_ESCAPE:
                    if all(m in _mods_down for m in (VK_SHIFT, VK_CONTROL, VK_MENU)):
                        log("钩子内检测到 Ctrl+Alt+Shift+Esc（mods=%s）" % sorted(_mods_down))
                        commands.put(("force_unlock",))   # Ctrl+Alt+Shift+Esc 防锁死
                    else:
                        commands.put(("esc",))
                else:
                    commands.put(("activity",))
            return 1  # 屏蔽该按键（Esc 已被我们消费，同样屏蔽）
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def _keyboard_thread():
    try:
        _keyboard_thread_body()
    except Exception:
        log("键盘线程异常:\n" + traceback.format_exc())


def _keyboard_thread_body():
    global _hook_proc, _hook_handle, _hook_tid, hook_ready, hook_failed
    _hook_proc = LowLevelKeyboardProc(_keyboard_proc)
    hhook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_proc,
                                     kernel32.GetModuleHandleW(None), 0)
    if not hhook:
        hook_failed = True
        log("警告：键盘钩子安装失败（可能被安全软件拦截），键盘不会被屏蔽")
        return
    _hook_handle = hhook
    _hook_tid = kernel32.GetCurrentThreadId()
    hook_ready = True
    log("键盘钩子已安装")
    msg = MSG()
    while not _stopping:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hhook)
    _hook_handle = None


# ===================== 托盘图标 =====================
_tray_wndproc_obj = None
_tray_nid = None
_tray_hicon = None


def _tray_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAYICON:
        if lparam == WM_LBUTTONUP:
            commands.put(("toggle",))
        elif lparam == WM_RBUTTONUP:
            _show_tray_menu(hwnd)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _screen_info_text():
    """托盘菜单“当前屏幕/图片信息”的内容。"""
    parts = []
    src = _user_image_path()
    for (left, top, right, bottom) in get_monitors():
        mw, mh = right - left, bottom - top
        path, exact, fit = image_for_monitor(mw, mh)
        if path is None:
            parts.append("%dx%d → 无图" % (mw, mh))
        elif fit:
            parts.append("%dx%d → %s（自定义·%s）"
                         % (mw, mh, os.path.basename(path), FIT_LABELS.get(fit, fit)))
        else:
            parts.append("%dx%d → %s%s" % (mw, mh, os.path.basename(path),
                                          "" if exact else "（铺满/裁切）"))
    info = "；".join(parts) if parts else "未检测到显示器"
    if src:
        info += "\n自定义图：%s\n适配方式：%s" % (src, FIT_LABELS.get(BANNER_FIT, BANNER_FIT))
    return info


def _regenerate_now():
    """强制按当前分辨率重新出图（用于改了文案/想重画时）。"""
    if _user_image_path():
        log("当前使用自定义图片，忽略重新出图请求")
        show_balloon("全屏提示", "当前用的是自定义图片；先「恢复默认出图」再重新生成。")
        return
    sizes, seen = [], set()
    for (left, top, right, bottom) in get_monitors():
        size = (right - left, bottom - top)
        if size not in seen:
            seen.add(size)
            sizes.append(size)
    if not sizes:
        return
    show_balloon("全屏提示", "正在按当前分辨率重新出图…")
    _start_generation(sizes)


# ===================== 自定义图片（导入 / 适配 / 缓存） =====================
def _monitor_sizes():
    """当前所有显示器的像素尺寸（去重）。"""
    sizes = []
    for (left, top, right, bottom) in get_monitors():
        size = (right - left, bottom - top)
        if size not in sizes:
            sizes.append(size)
    return sizes


def _ensure_custom_cache(sizes):
    """为每个屏幕尺寸生成自定义横幅缓存（已存在则跳过）。返回是否全部成功。"""
    src = _user_image_path()
    if not src:
        return False
    ok = True
    for (mw, mh) in sizes:
        cache = _custom_cache_path(src, mw, mh, BANNER_FIT)
        if os.path.isfile(cache):
            continue
        try:
            img = _render_custom_image(src, mw, mh, BANNER_FIT)
            tmp = cache[:-4] + ".part.png"
            img.save(tmp)
            os.replace(tmp, cache)
            log("已生成自定义横幅缓存：%s" % cache)
        except Exception:
            ok = False
            log("自定义横幅生成失败 %dx%d:\n%s" % (mw, mh, traceback.format_exc()))
    return ok


_custom_render_seq = 0


def _start_custom_render(sizes=None):
    """后台把自定义图渲染成各屏尺寸并写缓存，完成后刷新已显示的横幅。"""
    global _custom_render_seq
    src = _user_image_path()
    if not src:
        return
    if sizes is None:
        sizes = _monitor_sizes()
    if not sizes:
        return
    _custom_render_seq += 1
    seq = _custom_render_seq
    log("开始准备自定义横幅图：%s（%s，%s）"
        % (os.path.basename(src), FIT_LABELS.get(BANNER_FIT, BANNER_FIT),
           ", ".join("%dx%d" % s for s in sizes)))

    def _work():
        ok = _ensure_custom_cache(sizes)
        commands.put(("custom_ready", ok, seq))

    threading.Thread(target=_work, daemon=True).start()


def _apply_custom_image(path):
    """设置自定义横幅图（主线程调用）。"""
    global BANNER_IMAGE
    BANNER_IMAGE = os.path.abspath(path)
    _update_config({"banner_image": BANNER_IMAGE})
    _photo_cache.clear()
    log("已设置自定义横幅图：%s（适配方式 %s）"
        % (BANNER_IMAGE, FIT_LABELS.get(BANNER_FIT, BANNER_FIT)))
    _start_custom_render()


def _clear_custom_image():
    """清除配置里的自定义图，恢复按分辨率出图的默认横幅。"""
    global BANNER_IMAGE
    BANNER_IMAGE = ""
    _update_config({"banner_image": ""})
    _photo_cache.clear()
    dropin = _dropin_image_path()
    if dropin:
        msg = ("配置里的自定义图已清除，但固定目录里还有 %s，仍会继续使用。"
               "想彻底恢复默认横幅，请移走该文件。" % os.path.basename(dropin))
    else:
        msg = "已恢复默认横幅（按屏幕分辨率出图）"
    log(msg)
    show_balloon("全屏提示", msg)
    if banner_windows:
        _refresh_banner_images()


def _set_fit(fit):
    """切换自定义图的适配方式。"""
    global BANNER_FIT
    fit = _normalize_fit(fit)
    if fit == BANNER_FIT:
        return
    BANNER_FIT = fit
    _update_config({"banner_fit": fit})
    _photo_cache.clear()
    log("适配方式改为：%s" % FIT_LABELS.get(fit, fit))
    if _user_image_path():
        _start_custom_render()
    if banner_windows:
        _refresh_banner_images()


def _pick_custom_image():
    """选一张自定义图并应用；横幅显示时先收起，让文件对话框可见可操作。"""
    was_visible = bool(banner_windows)
    if was_visible:
        hide_banner()
    path = ""
    try:
        initial = os.path.dirname(BANNER_IMAGE) if BANNER_IMAGE else os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=root,
            title="选择横幅图片（任意尺寸，按适配方式自动缩放）",
            initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
            filetypes=[("图片文件", " ".join("*" + e for e in USER_IMAGE_EXTS)),
                       ("所有文件", "*.*")])
    except Exception:
        log("打开图片选择框失败:\n" + traceback.format_exc())
    if not path:
        log("已取消选择自定义图片")
        if was_visible:
            show_banner()
        return
    _apply_custom_image(path)
    if was_visible:
        show_banner()


def _show_tray_menu(hwnd):
    global _menu_open
    hmenu = user32.CreatePopupMenu()
    user32.AppendMenuW(hmenu, MF_STRING, IDM_TOGGLE, "显示/关闭横幅")
    user32.AppendMenuW(hmenu, MF_STRING, IDM_FORCE_UNLOCK, "强制解除键盘屏蔽")
    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    custom_active = bool(_user_image_path())
    user32.AppendMenuW(hmenu, MF_STRING, IDM_PICK_IMAGE, "选择自定义图片…")
    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    fit_base = MF_STRING | (0 if custom_active else MF_GRAYED)
    user32.AppendMenuW(hmenu, fit_base | (MF_CHECKED if BANNER_FIT == FIT_COVER else 0),
                       IDM_FIT_COVER, "适配：铺满裁切")
    user32.AppendMenuW(hmenu, fit_base | (MF_CHECKED if BANNER_FIT == FIT_CONTAIN else 0),
                       IDM_FIT_CONTAIN, "适配：完整显示")
    user32.AppendMenuW(hmenu, fit_base | (MF_CHECKED if BANNER_FIT == FIT_FILL else 0),
                       IDM_FIT_FILL, "适配：拉伸铺满")
    user32.AppendMenuW(hmenu, MF_STRING | (0 if custom_active else MF_GRAYED),
                       IDM_CLEAR_IMAGE, "恢复默认出图")
    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(hmenu, MF_STRING, IDM_INFO, "当前屏幕/图片信息")
    user32.AppendMenuW(hmenu, MF_STRING, IDM_REGEN, "重新出图（当前分辨率）")
    startup_flag = MF_STRING | (MF_CHECKED if is_startup_enabled() else 0)
    user32.AppendMenuW(hmenu, startup_flag, IDM_STARTUP, "开机启动")
    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, "退出")
    user32.SetForegroundWindow(hwnd)
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    _menu_open = True
    try:
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, hwnd, None)
    finally:
        _menu_open = False
    user32.DestroyMenu(hmenu)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    if cmd == IDM_TOGGLE:
        commands.put(("toggle",))
    elif cmd == IDM_FORCE_UNLOCK:
        commands.put(("force_unlock",))
    elif cmd == IDM_PICK_IMAGE:
        commands.put(("pick_image",))
    elif cmd == IDM_CLEAR_IMAGE:
        commands.put(("clear_image",))
    elif cmd in FIT_MENU_IDS.values():
        for fit, menu_id in FIT_MENU_IDS.items():
            if menu_id == cmd:
                commands.put(("set_fit", fit))
                break
    elif cmd == IDM_INFO:
        show_balloon("当前屏幕/图片", _screen_info_text())
    elif cmd == IDM_REGEN:
        _regenerate_now()
    elif cmd == IDM_STARTUP:
        _toggle_startup()
    elif cmd == IDM_EXIT:
        commands.put(("exit",))


def show_balloon(title, text):
    """托盘气泡提示。

    横幅显示期间不发：气泡是系统通知浮层，层级高于横幅，会盖住画面，
    此时只记录日志。系统关闭通知时气泡也可能不显示，但日志一定留痕。
    """
    if banner_windows:
        log("提示（横幅显示中，仅记录不弹气泡）：%s - %s" % (title, text))
        return
    if _tray_nid is None:
        return
    nid = _tray_nid
    nid.uFlags = NIF_INFO
    nid.szInfoTitle = title
    nid.szInfo = text
    nid.dwInfoFlags = NIIF_INFO
    if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
        log("气泡提示发送失败：%s" % text)


def _tray_thread():
    try:
        _tray_thread_body()
    except Exception:
        log("托盘线程异常:\n" + traceback.format_exc())


def _tray_thread_body():
    global _tray_wndproc_obj, _tray_nid, _tray_hicon, _tray_tid
    hinst = kernel32.GetModuleHandleW(None)

    _tray_wndproc_obj = WNDPROC(_tray_wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _tray_wndproc_obj
    wc.hInstance = hinst
    wc.lpszClassName = "BannerTrayWndClass"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log("托盘窗口类注册失败")
        return

    hwnd = user32.CreateWindowExW(0, "BannerTrayWndClass", "", 0, 0, 0, 0, 0,
                                  ctypes.c_void_p(HWND_MESSAGE), None, hinst, None)
    if not hwnd:
        log("托盘窗口创建失败")
        return

    _tray_hicon = user32.LoadImageW(None, tray_icon_path(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = _tray_hicon
    nid.szTip = "全屏提示"
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        log("托盘图标添加失败")
    _tray_nid = nid
    _tray_tid = kernel32.GetCurrentThreadId()
    # 设置托盘图标版本；启动气泡一律不发——通知浮层层级高于横幅，会盖住画面
    nid.uTimeoutOrVersion = NOTIFYICON_VERSION
    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
    log("静默启动（不弹启动气泡）" if QUIET else "启动完成（不弹启动气泡）")

    msg = MSG()
    while not _stopping:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _remove_tray_icon():
    if _tray_nid is not None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(_tray_nid))


# ===================== 开机启动 =====================
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "全屏提示"


def _startup_command():
    if getattr(sys, "frozen", False):
        # 打包成 exe 后直接指向 exe，不再依赖 Python 路径
        return '"%s" --quiet' % os.path.abspath(sys.executable)
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw
    return '"%s" "%s" --quiet' % (exe, os.path.abspath(__file__))


def read_startup_command():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return None


def is_startup_enabled():
    return read_startup_command() is not None


def set_startup(enabled):
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE)
        except OSError:
            pass


def heal_startup():
    """已开启自启时，校验注册表里的命令是否仍指向当前程序，失效则自动修正。"""
    current = read_startup_command()
    if current is None:
        return
    want = _startup_command()
    if os.path.normcase(current) == os.path.normcase(want):
        return
    try:
        set_startup(True)
        log("开机启动项已修正：%s" % want)
    except Exception as e:
        log("开机启动项修正失败：%s" % e)


def _toggle_startup():
    enabled = not is_startup_enabled()
    try:
        set_startup(enabled)
        log("开机启动已%s" % ("开启" if enabled else "关闭"))
        show_balloon("全屏提示", "开机启动已%s" % ("开启" if enabled else "关闭"))
    except Exception as e:
        log("切换开机启动失败：%s" % e)


# ===================== 占位资源生成 =====================
def _find_cjk_font():
    for p in (r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(p):
            return p
    return None


def ensure_placeholder():
    global _fallback_image
    if any(os.path.isfile(p) for p in (IMAGE_PATH,) + IMAGE_FALLBACKS):
        return
    W, H = 1920, 480
    img = Image.new("RGB", (W, H), (185, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, W - 9, H - 9], outline=(255, 255, 255), width=10)
    text = "请勿触动电脑"
    try:
        font = ImageFont.truetype(_find_cjk_font(), 140) if _find_cjk_font() else None
    except Exception:
        font = None
    if font is None:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (W - tw) // 2 - bbox[0]
    y = (H - th) // 2 - bbox[1]
    d.text((x, y), text, font=font, fill=(255, 255, 255))
    for p in (IMAGE_PATH, os.path.join(DATA_DIR, "placeholder.png")):
        try:
            img.save(p)
            _fallback_image = p
            log("已生成占位横幅: %s" % p)
            return
        except Exception as e:
            log("占位横幅写入失败 %s: %s" % (p, e))


def _draw_padlock(d, S):
    """在画布上画一个金色锁头（以 64 为基准等比缩放）。"""
    gold = (255, 193, 7, 255)
    dark = (90, 65, 0, 255)
    k = S / 64.0
    s = lambda v: v * k
    # 锁梁（U 形，先画，让锁体盖住两端）
    d.arc([s(16), s(8), s(48), s(40)], 180, 360, fill=gold, width=max(2, int(round(s(8)))))
    # 锁体
    d.rounded_rectangle([s(10), s(26), s(54), s(58)], radius=int(round(s(12))), fill=gold)
    # 钥匙孔
    d.ellipse([s(27), s(34), s(37), s(44)], fill=dark)
    d.rectangle([s(30), s(41), s(34), s(52)], fill=dark)


def tray_icon_path():
    """返回可用的托盘图标路径；程序目录不可写时生成到数据目录。"""
    for p in (TRAY_ICO,) + TRAY_ICO_FALLBACKS:
        if os.path.isfile(p):
            return p
    cached = os.path.join(DATA_DIR, "tray.ico")
    S = 64
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_padlock(d, S)
    for p in (cached, TRAY_ICO):
        try:
            img.save(p, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
            log("已生成托盘图标: %s" % p)
            return p
        except Exception as e:
            log("托盘图标写入失败 %s: %s" % (p, e))
    return TRAY_ICO


# ===================== 主循环 =====================
root = None


def shutdown():
    """幂等退出清理：关横幅、删托盘、停线程、卸钩子、关句柄。"""
    global _shutdown_done, _stopping, _quitting
    if _shutdown_done:
        return
    _shutdown_done = True
    _stopping = True
    _quitting = True
    try:
        hide_banner()
    except Exception:
        pass
    try:
        _remove_tray_icon()
    except Exception:
        pass
    if _tray_tid:
        user32.PostThreadMessageW(_tray_tid, WM_QUIT, 0, 0)
    if _hook_tid:
        user32.PostThreadMessageW(_hook_tid, WM_QUIT, 0, 0)
    if _hook_handle:
        user32.UnhookWindowsHookEx(_hook_handle)
    if _toggle_event:
        kernel32.CloseHandle(_toggle_event)
    if _single_handle:
        kernel32.CloseHandle(_single_handle)
    log("程序已退出")
    try:
        root.destroy()
    except Exception:
        pass


def _exit():
    shutdown()


def _handle_command(cmd):
    global keyboard_blocked, banner_mode
    kind = cmd[0]
    if kind == "toggle":
        toggle_banner()
    elif kind == "esc":
        if keyboard_blocked:
            hide_banner()
    elif kind == "force_unlock":
        # 防锁死出口：Ctrl+Alt+Shift+Esc 或托盘菜单
        if banner_windows:
            hide_banner()
        keyboard_blocked = False
        banner_mode = None
        log("收到强制解除：关闭横幅并解除键盘屏蔽")
        show_balloon("全屏提示", "已强制解除键盘屏蔽")
    elif kind == "activity":
        _on_activity()
    elif kind == "generated":
        ok = cmd[1]
        if ok:
            _refresh_banner_images()
            log("横幅已按当前分辨率重新生成并替换")
        else:
            log("出图失败，保持当前铺满显示")
        _hide_generate_prompt()
        _raise_banner_above()
    elif kind == "custom_ready":
        ok, seq = cmd[1], cmd[2]
        if seq != _custom_render_seq:
            log("忽略过期的自定义图生成结果")
        elif ok:
            if banner_windows:
                _refresh_banner_images()
                log("自定义横幅已应用并替换")
        else:
            log("自定义横幅生成失败（显示时会同步重试）")
            if banner_windows:
                _refresh_banner_images()
    elif kind == "pick_image":
        _pick_custom_image()
    elif kind == "clear_image":
        _clear_custom_image()
    elif kind == "set_fit":
        _set_fit(cmd[1])
    elif kind == "exit":
        _exit()


def _check_hook_health():
    global hook_warned
    if hook_failed and not hook_warned:
        hook_warned = True
        show_balloon("键盘屏蔽不可用",
                     "全局键盘钩子安装失败，横幅仍可显示但键盘不会被屏蔽。"
                     "请检查安全软件是否拦截了本程序。")
        log("警告：本次运行键盘不会被屏蔽（钩子安装失败）")


def _poll():
    if _quitting:
        return
    while True:
        try:
            cmd = commands.get_nowait()
        except queue.Empty:
            break
        try:
            _handle_command(cmd)
        except Exception:
            log("命令处理异常:\n" + traceback.format_exc())
        if _quitting:
            return
    try:
        _check_hook_health()
    except Exception:
        log("钩子状态检查异常:\n" + traceback.format_exc())
    try:
        _oled_tick()
    except Exception:
        log("防烧屏逻辑异常:\n" + traceback.format_exc())
    if not _quitting:
        root.after(TICK_VISIBLE if banner_windows else TICK_IDLE, _poll)


def _selftest_schedule():
    """自检模式（无界面验证用）：BANNER_SELFTEST=秒数 显示横幅后自动退出。"""
    raw = os.environ.get("BANNER_SELFTEST")
    if not raw:
        return
    try:
        secs = float(raw)
    except ValueError:
        secs = 6.0
    log("自检模式：1.2s 后显示横幅，%.1fs 后自动退出" % secs)
    root.after(1200, show_banner)
    root.after(int(1200 + secs * 1000), _exit)

    def _watchdog():
        time.sleep(secs + 8)
        log("自检看门狗触发：强制退出")
        os._exit(3)

    threading.Thread(target=_watchdog, daemon=True).start()


def main():
    global root
    set_dpi_awareness()
    if not acquire_single_instance():
        log("检测到已在运行的实例，通知它切换横幅后退出")
        if notify_existing_instance():
            time.sleep(0.3)
        return
    _create_toggle_event()
    ensure_placeholder()
    tray_icon_path()
    heal_startup()
    if _user_image_path():
        log("检测到自定义横幅图：%s（适配方式 %s）"
            % (_user_image_path(), FIT_LABELS.get(BANNER_FIT, BANNER_FIT)))
        _start_custom_render()
    threading.Thread(target=_event_thread, daemon=True).start()
    threading.Thread(target=_keyboard_thread, daemon=True).start()
    threading.Thread(target=_tray_thread, daemon=True).start()
    root = tk.Tk()
    root.withdraw()
    monitors = get_monitors()
    log("程序已启动 v%s（Python %s，%d 屏：%s）"
        % (VERSION, sys.version.split()[0], len(monitors),
           ", ".join("%dx%d" % (r - l, b - t) for (l, t, r, b) in monitors)))
    root.after(50, _poll)
    _selftest_schedule()
    try:
        root.mainloop()
    finally:
        shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("未捕获异常:\n" + traceback.format_exc())
