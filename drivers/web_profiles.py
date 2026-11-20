"""阅卷网站的页面元素配置：从 profiles/*.json 加载（按网址匹配）。

JSON 字段（"image"/"input"/"submit"/"abnormal" 可写字符串或候选列表，按顺序尝试，取第一个命中的）：
  name          显示名称
  url_contains  网址片段（字符串或列表），页面网址包含其中任一项即匹配
  extends       可选，继承另一份规则的文件名（不含 .json），只需写不同的字段
  image         答卷图（可能匹配多张，驱动只取"可见"的那张）
  input         分数输入框
  submit        提交按钮；为空则在输入框内按 Enter 提交
  abnormal      "异常卷"按钮；仅在用户开启自动处理时才会点击

规则文件夹：程序自带的 profiles/，以及 exe 同目录下的 profiles/（后者同名文件优先，便于用户自行添加）。
"""
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class WebProfile:
    name: str
    url_contains: Tuple[str, ...]
    image_selectors: Tuple[str, ...]
    input_selectors: Tuple[str, ...]
    submit_selectors: Tuple[str, ...] = ()
    abnormal_selectors: Tuple[str, ...] = ()


class ProfileError(Exception):
    pass


def profile_dirs() -> List[str]:
    """按优先级从低到高返回规则文件夹（后面的同名文件覆盖前面的）。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = [os.path.join(base, "profiles")]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "profiles"))
    return dirs


def _as_tuple(value) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ProfileError("选择器必须是字符串或字符串列表")
    return tuple(v.strip() for v in value)


def _resolve(pid: str, raw: Dict[str, dict], stack: Tuple[str, ...] = ()) -> dict:
    if pid in stack:
        raise ProfileError(f"规则继承出现循环：{' -> '.join(stack + (pid,))}")
    if pid not in raw:
        raise ProfileError(f"找不到被继承的规则：{pid}")
    data = dict(raw[pid])
    parent = data.pop("extends", None)
    if parent:
        merged = _resolve(str(parent), raw, stack + (pid,))
        merged.update(data)
        return merged
    return data


def build_profile(data: dict) -> WebProfile:
    url_contains = _as_tuple(data.get("url_contains"))
    image = _as_tuple(data.get("image"))
    inp = _as_tuple(data.get("input"))
    if not url_contains:
        raise ProfileError("缺少 url_contains")
    if not image or not inp:
        raise ProfileError("缺少答卷图(image)或分数框(input)")
    return WebProfile(
        name=str(data.get("name") or url_contains[0]),
        url_contains=url_contains,
        image_selectors=image,
        input_selectors=inp,
        submit_selectors=_as_tuple(data.get("submit")),
        abnormal_selectors=_as_tuple(data.get("abnormal")),
    )


def load_profiles(dirs: Optional[List[str]] = None) -> Dict[str, WebProfile]:
    """加载全部规则。单个文件出错只跳过该文件，不影响其他平台。"""
    raw: Dict[str, dict] = {}
    for d in (dirs if dirs is not None else profile_dirs()):
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                raw[fname[:-5]] = data
    result: Dict[str, WebProfile] = {}
    for pid in raw:
        try:
            result[pid] = build_profile(_resolve(pid, raw))
        except ProfileError:
            continue
    return result


def find_profile_for_url(url: str, profiles: Optional[Dict[str, WebProfile]] = None) -> Optional[WebProfile]:
    profiles = load_profiles() if profiles is None else profiles
    for profile in profiles.values():
        if any(part in (url or "") for part in profile.url_contains):
            return profile
    return None
