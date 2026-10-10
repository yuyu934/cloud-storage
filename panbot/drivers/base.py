"""driver 基类：统一接口 + dry-run 支持。"""

from __future__ import annotations

import os
from dataclasses import dataclass


class DriverError(Exception):
    """某个网盘操作失败。"""


class AuthError(DriverError):
    """凭证缺失或失效。"""


@dataclass
class ShareResult:
    drive: str
    folder: str
    url: str
    password: str = ""
    expire: str = ""
    promo_text: str = ""  # 可直接粘贴推广的文案（部分网盘可生成）
    cmd_text: str = ""    # 单独的口令字符串（如夸克的中文口令）


class BaseDriver:
    name = "base"

    def __init__(self, cfg: dict, state, dry_run: bool = False, log=print):
        self.cfg = cfg
        self.state = state
        self.dry = dry_run
        self.log = log

    # ---- 子类必须实现 ----
    def check_auth(self) -> str:
        """验证凭证可用，返回一句描述；失败抛 AuthError。"""
        raise NotImplementedError

    def ensure_dir(self, remote_path: str):
        """确保远端目录存在，返回可分享的对象标识（fid/id/path）。"""
        raise NotImplementedError

    def upload_file(self, local_path: str, remote_dir: str, dir_id=None) -> None:
        """上传单个文件到远端目录。dir_id 为 ensure_dir 的返回值。"""
        raise NotImplementedError

    def create_share(self, remote_path: str, title: str, password: str, days: int) -> ShareResult:
        raise NotImplementedError

    # ---- 公共逻辑 ----
    def collect_files(self, source_dir: str, extensions) -> list:
        """收集源目录下（递归）指定扩展名的图片，返回绝对路径列表。"""
        exts = {e.lower().lstrip(".") for e in extensions}
        found = []
        for root, _dirs, files in os.walk(source_dir):
            for fn in sorted(files):
                if os.path.splitext(fn)[1].lower().lstrip(".") in exts:
                    found.append(os.path.join(root, fn))
        return found

    def upload_dir(self, source_dir: str, remote_path: str, extensions) -> int:
        files = self.collect_files(source_dir, extensions)
        if not files:
            raise DriverError(f"源目录没有可上传的图片（按扩展名过滤后为空）：{source_dir}")
        if self.dry:
            self.log(f"[{self.name}][dry-run] 将上传 {len(files)} 个文件到 {remote_path}")
            return len(files)
        dir_id = self.ensure_dir(remote_path)
        done = 0
        for fp in files:
            self.log(f"[{self.name}] 上传 {os.path.basename(fp)} ({done + 1}/{len(files)})")
            self.upload_file(fp, remote_path, dir_id)
            done += 1
        return done
