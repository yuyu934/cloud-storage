"""结果输出：控制台汇总、markdown 汇总文件、CSV 追加日志。"""

from __future__ import annotations

import csv
import os
import time
from datetime import date


def console_summary(results: list) -> str:
    lines = ["", "========== 今日分享链接 =========="]
    for r in results:
        pwd = f"  提取码: {r.password}" if r.password else ""
        lines.append(f"[{r.drive}] {r.folder}{pwd}")
        lines.append(f"    {r.url}")
        if r.cmd_text:
            lines.append(f"    口令: {r.cmd_text}")
    lines.append("==================================")
    promo = [r for r in results if r.promo_text]
    if promo:
        lines.append("")
        lines.append("---------- 可直接粘贴的推广文案 ----------")
        for r in promo:
            lines.append(r.promo_text)
            lines.append("")
    return "\n".join(lines)


def clipboard_payload(results: list) -> str:
    """优先复制推广文案，没有文案的盘用链接。"""
    return "\n\n".join(r.promo_text or r.url for r in results)


def write_markdown(path: str, day: str, results: list) -> None:
    if not path or not results:
        return
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if exists:
            f.write("\n")
        f.write(f"## {day}\n\n")
        for r in results:
            pwd = f"（提取码 `{r.password}`）" if r.password else ""
            cmd = f"（口令 `{r.cmd_text}`）" if r.cmd_text else ""
            f.write(f"- **{r.drive}** {r.folder}{pwd}{cmd}：{r.url}\n")
        promo = [r for r in results if r.promo_text]
        if promo:
            f.write("\n### 推广文案（可直接复制）\n\n")
            for r in promo:
                f.write(f"**{r.drive}**\n\n```text\n{r.promo_text}\n```\n\n")


def append_csv(path: str, day: str, results: list) -> None:
    if not path or not results:
        return
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["日期", "网盘", "远端目录", "链接", "提取码", "有效期", "生成时间"])
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        for r in results:
            w.writerow([day, r.drive, r.folder, r.url, r.password, r.expire, ts])


def default_day() -> str:
    return date.today().isoformat()
