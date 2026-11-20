"""网页驱动：通过 Playwright 连接用户已登录的 Chromium 内核浏览器（Edge/Chrome）。

设计约束：
- 不往网页注入任何脚本、不修改页面，只读取图片、填分、点按钮。
- 只允许操作 WebProfile 里声明的元素（白名单）。
- 任何不确定的情况（找不到元素、读回不一致、换卷超时）一律抛 DriverError，由调用方停机。
- 日志只记录图片编号，不记录图片链接（链接带临时令牌）。
- Playwright 同步对象必须在创建它的线程里使用：本类应在阅卷线程内创建和使用。
"""
import base64
import hashlib
import time
from io import BytesIO
from typing import Callable, Optional
from urllib.parse import urlsplit

from PIL import Image

from drivers.web_profiles import WebProfile, find_profile_for_url

KIND_CONNECTION = "connection"
KIND_ELEMENT = "element"
KIND_VERIFY = "verify"
KIND_TIMEOUT = "timeout"
KIND_ABNORMAL = "abnormal"
ABNORMAL_CONFIRM_SECONDS = 3.0  # 无图且分数框被禁用，持续这么久才判定为异常卷（避开换卷瞬间）


class DriverError(Exception):
    """网页驱动错误。kind 用于调用方区分处理方式。"""

    def __init__(self, message: str, kind: str = KIND_ELEMENT):
        super().__init__(message)
        self.kind = kind


def fingerprint_from_src(src: str) -> str:
    """用图片地址路径的最后一段（如 1521250083A.png）作为"这份卷"的指纹，丢弃带令牌的查询串。"""
    if src and not src.startswith(("data:", "blob:")):
        name = urlsplit(src).path.rsplit("/", 1)[-1]
        if name:
            return name
    return "h" + hashlib.md5((src or "").encode("utf-8", "ignore")).hexdigest()[:12]


def to_jpeg_data_uri(raw: bytes, quality: int = 75) -> str:
    """原图（通常是 PNG）转 JPEG q75，与屏幕模式的压缩参数一致。"""
    img = Image.open(BytesIO(raw))
    try:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    finally:
        img.close()


class WebDriver:
    def __init__(self, profile: Optional[WebProfile] = None, debug_port: int = 9222, page=None,
                 log: Optional[Callable[[str], None]] = None):
        self.profile = profile  # 为空则 connect() 时按页面网址从规则库中匹配
        self.debug_port = debug_port
        self._page = page  # 允许注入（测试用）；为空则 connect() 时通过 CDP 连接
        self._log = log or (lambda msg: None)
        self._pw = None
        self._browser = None
        self._captured_fp: Optional[str] = None

    # ------------------------------------------------------------------ 连接
    def connect(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            raise DriverError(f"缺少 Playwright 组件：{e}", KIND_CONNECTION)

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.debug_port}")
        except Exception:
            self.close()
            raise DriverError(
                "无法连接到阅卷浏览器。请先用程序提供的“阅卷专用浏览器”启动并登录阅卷网站，"
                "且不要关闭它。", KIND_CONNECTION)

        self._page = self._find_page()
        if self._page is None:
            self.close()
            raise DriverError(
                "浏览器中没有找到已适配的阅卷页面，请在专用浏览器里打开阅卷页面。"
                "（如果是新的阅卷网站，需要先添加它的规则文件。）",
                KIND_CONNECTION)

    def _find_page(self):
        candidates = []
        for ctx in self._browser.contexts:
            for pg in ctx.pages:
                profile = self.profile or find_profile_for_url(pg.url)
                if profile is not None and any(part in pg.url for part in profile.url_contains):
                    candidates.append((pg, profile))
        chosen = None
        for pg, profile in candidates:
            try:
                if pg.evaluate("document.visibilityState") == "visible":
                    chosen = (pg, profile)
                    break
            except Exception:
                continue
        if chosen is None and candidates:
            chosen = candidates[-1]
        if chosen is None:
            return None
        self.profile = chosen[1]
        return chosen[0]

    def close(self) -> None:
        """只断开连接，不关闭用户的浏览器窗口。"""
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------ 元素
    def _first_match(self, selectors, suffix: str = ""):
        """按顺序尝试候选选择器，返回第一个有命中的；都没有则返回第一个（调用方按"找不到"处理）。"""
        for sel in selectors:
            loc = self._page.locator(sel + suffix).first
            try:
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return self._page.locator(selectors[0] + suffix).first

    def _visible_image(self):
        return self._first_match(self.profile.image_selectors, ":visible")

    def _input(self):
        return self._first_match(self.profile.input_selectors)

    def _require_page(self):
        if self._page is None:
            raise DriverError("网页驱动尚未连接", KIND_CONNECTION)
        try:
            if self._page.is_closed():
                raise DriverError("阅卷页面已被关闭", KIND_CONNECTION)
            if self._page.evaluate("document.visibilityState") == "hidden":
                raise DriverError(
                    "阅卷专用浏览器窗口被最小化了，页面已停止渲染。请把它还原（可以被其他窗口盖住，但不要最小化），再重新开始。",
                    KIND_CONNECTION)
        except DriverError:
            raise
        except Exception:
            raise DriverError("与阅卷浏览器的连接已断开", KIND_CONNECTION)

    def _loaded_image_src(self) -> Optional[str]:
        """可见答卷图已加载完成时返回其 src，否则返回 None。"""
        loc = self._visible_image()
        try:
            if loc.count() == 0:
                return None
            ready = loc.evaluate("i => !!(i.complete && i.naturalWidth > 0)")
            return (loc.get_attribute("src") or "") if ready else None
        except Exception:
            return None

    # ------------------------------------------------------------------ 取图
    def capture(self, timeout: float = 15.0) -> str:
        """等待当前这份卷的图加载完成，返回 JPEG data URI，并记住其指纹。"""
        self._require_page()
        deadline = time.time() + timeout
        src = None
        abnormal_since = None
        while time.time() < deadline:
            src = self._loaded_image_src()
            if src:
                break
            if self._looks_abnormal():
                abnormal_since = abnormal_since or time.time()
                if time.time() - abnormal_since >= ABNORMAL_CONFIRM_SECONDS:
                    raise DriverError("当前是异常试卷（图片异常，分数框被禁用）。", KIND_ABNORMAL)
            else:
                abnormal_since = None
            time.sleep(0.2)
        if not src:
            raise DriverError("等待答卷图加载超时：页面上没有可见且已加载的答卷图。", KIND_TIMEOUT)

        fp = fingerprint_from_src(src)
        # 只对页面上已显示的图片元素截图，不在页面之外单独请求图片地址，
        # 避免产生与人工看图不同的网络请求。
        try:
            raw = self._cdp_clip_screenshot()
        except Exception:
            raw = None
        if raw is None:
            try:
                raw = self._visible_image().screenshot(timeout=8000)
            except Exception as e:
                raise DriverError(
                    f"取答卷图失败：{e}。如果浏览器窗口被最小化，请取消最小化（可以被其他窗口盖住）。",
                    KIND_ELEMENT)

        try:
            data_uri = to_jpeg_data_uri(raw)
        except Exception as e:
            raise DriverError(f"答卷图格式无法识别：{e}", KIND_ELEMENT)

        self._captured_fp = fp
        self._log(f"网页取图成功（编号 {fp}）")
        return data_uri

    def _cdp_clip_screenshot(self) -> bytes:
        """用 CDP 按图片所在区域截图，不要求页面"稳定"，窗口被遮挡时也可用。"""
        rect = self._visible_image().evaluate(
            "i => { const r = i.getBoundingClientRect();"
            " return {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height}; }")
        if rect["width"] < 2 or rect["height"] < 2:
            raise DriverError("答卷图尺寸异常", KIND_ELEMENT)
        session = self._page.context.new_cdp_session(self._page)
        try:
            res = session.send("Page.captureScreenshot", {
                "format": "png",
                "clip": {**rect, "scale": 1},
                "fromSurface": True,
            })
        finally:
            try:
                session.detach()
            except Exception:
                pass
        return base64.b64decode(res["data"])

    def _looks_abnormal(self) -> bool:
        """异常卷特征（实测于五岳）：没有可见答卷图，且分数框被禁用。"""
        try:
            if self._visible_image().count() > 0:
                return False
            box = self._input()
            return box.count() > 0 and box.is_disabled()
        except Exception:
            return False

    @property
    def captured_fingerprint(self) -> Optional[str]:
        return self._captured_fp

    def current_fingerprint(self) -> Optional[str]:
        self._require_page()
        src = self._loaded_image_src()
        return fingerprint_from_src(src) if src else None

    def is_still_same_paper(self) -> bool:
        """写分前核验：页面上可见的还是刚才评分的那一份。"""
        self._require_page()
        loc = self._visible_image()
        try:
            if loc.count() == 0:
                return False
            return fingerprint_from_src(loc.get_attribute("src") or "") == self._captured_fp
        except Exception:
            return False

    # ------------------------------------------------------------------ 填分/提交
    def enter_score(self, score_text: str) -> None:
        """填入分数并读回校验；读回不一致则抛错，绝不提交。"""
        self._require_page()
        box = self._input()
        try:
            if box.count() == 0:
                raise DriverError("找不到分数输入框，页面可能已改版或已退出登录。", KIND_ELEMENT)
            box.fill("")
            box.fill(score_text)
            read_back = box.input_value()
        except DriverError:
            raise
        except Exception as e:
            raise DriverError(f"填写分数失败：{e}", KIND_ELEMENT)
        if read_back.strip() != score_text:
            raise DriverError(
                f"分数读回校验失败：要填 {score_text}，输入框里是 {read_back!r}。已停止，未提交。",
                KIND_VERIFY)

    def confirm(self) -> None:
        self._require_page()
        try:
            if self.profile.submit_selectors:
                btn = self._first_match(self.profile.submit_selectors)
                if btn.count() == 0:
                    raise DriverError("找不到提交按钮，页面可能已改版。", KIND_ELEMENT)
                btn.click()
            else:
                self._input().press("Enter")
        except DriverError:
            raise
        except Exception as e:
            raise DriverError(f"提交失败：{e}", KIND_ELEMENT)

    def wait_next_paper(self, timeout: float = 20.0) -> str:
        """等待换卷：可见答卷图的指纹与刚评分的不同且已加载。返回新指纹。

        下一份若是异常卷（无图、分数框被禁用）也算已换卷，返回 "abnormal"。"""
        self._require_page()
        deadline = time.time() + timeout
        abnormal_since = None
        while time.time() < deadline:
            src = self._loaded_image_src()
            if src:
                fp = fingerprint_from_src(src)
                if fp != self._captured_fp:
                    return fp
                abnormal_since = None
            elif self._looks_abnormal():
                abnormal_since = abnormal_since or time.time()
                if time.time() - abnormal_since >= ABNORMAL_CONFIRM_SECONDS:
                    self._captured_fp = None
                    return "abnormal"
            time.sleep(0.2)
        raise DriverError(
            "提交后页面没有刷新出下一份试卷（超时）。可能提交失败、网速慢或已无试卷。", KIND_TIMEOUT)

    # ------------------------------------------------------------------ 异常卷
    def mark_abnormal(self) -> None:
        """点击"异常卷"按钮。只有用户在设置里开启自动处理时才应调用。"""
        self._require_page()
        if not self.profile.abnormal_selectors:
            raise DriverError("当前网站未配置“异常卷”按钮。", KIND_ELEMENT)
        btn = self._first_match(self.profile.abnormal_selectors)
        try:
            if btn.count() == 0:
                raise DriverError("找不到“异常卷”按钮。", KIND_ELEMENT)
            btn.click()
        except DriverError:
            raise
        except Exception as e:
            raise DriverError(f"点击“异常卷”失败：{e}", KIND_ELEMENT)
