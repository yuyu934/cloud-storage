"""通用工具：哈希、gcid、提取码、剪贴板等。"""

from __future__ import annotations

import hashlib
import io
import os
import random
import string
import subprocess

BLOCK_CHARS = string.ascii_lowercase + string.digits


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def hash_file(path: str, algos=("md5", "sha1")) -> dict:
    """一次性算出整个文件的多种哈希。"""
    result = {a: getattr(hashlib, a)() for a in algos}
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for h in result.values():
                h.update(chunk)
    return {a: h.hexdigest() for a, h in result.items()}


def gcid_block_size(size: int) -> int:
    """迅雷 gcid 的分块大小：256KB 起，文件越大块越大，上限 2MB。
    与 OpenList drivers/thunder GetGcid 的 calcBlockSize 一致。"""
    psize = 0x40000
    while size / psize > 0x200 and psize < 0x200000:
        psize <<= 1
    return psize


def gcid(path: str) -> str:
    """计算迅雷 gcid（分块 sha1 再总 sha1），用于秒传判定。"""
    size = os.path.getsize(path)
    read_size = gcid_block_size(size)
    outer = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(read_size)
            if not chunk:
                break
            outer.update(hashlib.sha1(chunk).digest())
    return outer.hexdigest()


def random_passcode(length: int = 4) -> str:
    """生成提取码：小写字母+数字（百度要求 4 位）。"""
    return "".join(random.choice(BLOCK_CHARS) for _ in range(length))


def copy_to_clipboard(text: str) -> bool:
    """Windows 下尽力把文本放进剪贴板，失败静默返回 False。"""
    try:
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
        p.communicate(text.encode("gbk", errors="replace"))
        return True
    except Exception:
        try:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-16-le"))
            return True
        except Exception:
            return False


class ProgressWriter(io.IOBase):
    """包装只读文件对象，用于读取进度回调。"""

    def __init__(self, fp, total: int, on_progress=None):
        self.fp = fp
        self.total = total
        self.read_bytes = 0
        self.on_progress = on_progress

    def read(self, n=-1):
        data = self.fp.read(n)
        self.read_bytes += len(data)
        if self.on_progress:
            self.on_progress(self.read_bytes, self.total)
        return data
