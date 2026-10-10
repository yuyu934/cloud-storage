"""driver 注册表。"""

from __future__ import annotations

from .base import AuthError, BaseDriver, DriverError, ShareResult
from .baidu import BaiduDriver
from .quark import QuarkDriver
from .xunlei import XunleiDriver

DRIVERS = {
    "baidu": BaiduDriver,
    "quark": QuarkDriver,
    "xunlei": XunleiDriver,
}


def build_drivers(cfg: dict, state, dry_run: bool = False, log=print) -> list:
    """按 config 中 enable 标记构建已启用的 driver 实例。"""
    out = []
    for name, cls in DRIVERS.items():
        section = cfg.get(name, {})
        if section.get("enable"):
            out.append(cls(section, state, dry_run=dry_run, log=log))
    return out


__all__ = [
    "AuthError",
    "BaseDriver",
    "DriverError",
    "ShareResult",
    "BaiduDriver",
    "QuarkDriver",
    "XunleiDriver",
    "DRIVERS",
    "build_drivers",
]
