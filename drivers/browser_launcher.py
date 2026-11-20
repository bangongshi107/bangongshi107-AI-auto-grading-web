"""启动“阅卷专用浏览器”：Chromium 内核（Edge/Chrome），带调试端口和独立登录目录。

独立目录保证：不影响用户平时用的浏览器，且调试参数一定生效；登录状态会保留到下次。
"""
import os
import socket
import subprocess
from typing import List, Optional

START_URL = "https://www.wylkyj.com/yuejuan/"  # 批改页需从任务列表进入，不能直接打开 #/reading

_BROWSER_CANDIDATES = [
    ("Edge", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ("Edge", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ("Chrome", r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ("Chrome", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ("Chrome", r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_browser() -> Optional[str]:
    for _name, template in _BROWSER_CANDIDATES:
        path = os.path.expandvars(template)
        if os.path.isfile(path):
            return path
    return None


def profile_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AIGradingEdgeProfile")


def build_args(exe: str, port: int, url: str = START_URL) -> List[str]:
    return [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir()}",
        # 窗口被遮挡/在后台时不要让页面休眠，否则取图和换卷判断会变慢
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
        url,
    ]


def is_debug_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def launch(port: int, url: str = START_URL) -> str:
    """启动专用浏览器。返回提示文字；找不到浏览器时抛 FileNotFoundError。"""
    if is_debug_port_open(port):
        return "阅卷专用浏览器已经在运行，请直接在里面登录并进入阅卷页面。"
    exe = find_browser()
    if not exe:
        raise FileNotFoundError("没有找到 Edge 或 Chrome 浏览器，请先安装其中之一。")
    subprocess.Popen(build_args(exe, port, url))
    return "已打开阅卷专用浏览器。请在里面登录阅卷网站并进入批改页面（登录状态下次会保留）。"
