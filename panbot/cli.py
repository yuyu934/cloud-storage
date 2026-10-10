"""panbot 命令行入口。

用法：
    python -m panbot check                 # 检查三家凭证是否可用
    python -m panbot run [--source DIR]    # 每日任务：上传+分享+输出链接
    python -m panbot baidu-auth            # 百度开放平台 OAuth 授权
    python -m panbot run --dry-run         # 只打印将要做的事，不发请求
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from . import __version__
from .config import ConfigError, StateStore, load_config
from .drivers import AuthError, DriverError, build_drivers
from .output import append_csv, clipboard_payload, console_summary, default_day, write_markdown
from .util import copy_to_clipboard


def find_config(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = [
        os.path.join(os.getcwd(), "config.yaml"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def cmd_check(args) -> int:
    config_path = find_config(args.config)
    cfg = load_config(config_path)
    state = StateStore(config_path)
    drivers = build_drivers(cfg, state, log=print)
    if not drivers:
        print("没有任何启用的网盘（config.yaml 里把 baidu/quark/xunlei 的 enable 设为 true）")
        return 1
    failed = False
    for d in drivers:
        try:
            msg = d.check_auth()
            print(f"[OK]   {d.name}: {msg}")
        except (AuthError, DriverError) as e:
            failed = True
            print(f"[FAIL] {d.name}: {e}")
    return 1 if failed else 0


def cmd_run(args) -> int:
    config_path = find_config(args.config)
    cfg = load_config(config_path)
    state = StateStore(config_path)
    day = args.date or default_day()
    source = args.source or cfg.get("source_dir") or ""
    source = os.path.abspath(source)
    if not args.dry_run:
        if not source or not os.path.isdir(source):
            print(f"源目录不存在：{source}（用 --source 指定，或填 config.yaml 的 source_dir）")
            return 1
    drivers = build_drivers(cfg, state, dry_run=args.dry_run, log=print)
    if args.drive:
        want = {x.strip() for x in args.drive.split(",") if x.strip()}
        drivers = [d for d in drivers if d.name in want]
    if not drivers:
        print("没有任何启用的网盘（config.yaml 里把 baidu/quark/xunlei 的 enable 设为 true）")
        return 1

    share_cfg = cfg.get("share", {})
    days = int(share_cfg.get("days", 30))
    title_tpl = share_cfg.get("title", "{date} 图片分享")
    results = []
    errors = []

    name = (getattr(args, "name", "") or "").strip()
    if name:
        bad = set(name) & set('\\/:*?"<>|')
        if bad:
            print(f"--name 含文件夹非法字符：{''.join(sorted(bad))}")
            return 1

    for d in drivers:
        # --remote 优先；否则用各盘配置里的 folder 模板（date_dir 作为兜底）
        folder_tpl = args.remote or d.cfg.get("folder", cfg.get("date_dir", "{date}"))
        if "{name}" in folder_tpl and not name:
            errors.append((d.name, "folder 模板包含 {name}，请用 --name 指定本次的文件夹名"))
            continue
        folder = folder_tpl.replace("{date}", day).replace("{name}", name)
        remote = folder.strip("/")
        if d.name == "baidu":
            root = d.root()
            remote = f"{root}/{remote}" if root else f"/{remote}"
        try:
            d.upload_dir(source, remote, cfg.get("extensions", []))
            if args.no_share:
                print(f"[{d.name}] 跳过分享（--no-share）")
                continue
            if args.dry_run:
                print(f"[{d.name}][dry-run] 将创建分享：目录 {remote}，有效期 {days} 天")
                continue
            results.append(
                d.create_share(
                    remote,
                    title_tpl.replace("{date}", day).replace("{name}", name).strip(),
                    share_cfg.get("password", ""),
                    days,
                )
            )
        except (AuthError, DriverError) as e:
            errors.append((d.name, str(e)))

    if results:
        print(console_summary(results))
        out = cfg.get("output", {})
        md = out.get("markdown")
        csvp = out.get("csv")
        if md and not md.startswith("/") and ":" not in md:
            md = os.path.join(os.path.dirname(config_path), md)
        if csvp and not csvp.startswith("/") and ":" not in csvp:
            csvp = os.path.join(os.path.dirname(config_path), csvp)
        write_markdown(md, day, results)
        append_csv(csvp, day, results)
        if not args.no_clipboard and copy_to_clipboard(clipboard_payload(results)):
            print("（已复制到剪贴板：优先为推广文案）")

    if errors:
        print("\n以下网盘处理失败：")
        for name, msg in errors:
            print(f"  - {name}: {msg}")
        return 1
    return 0


def cmd_baidu_auth(args) -> int:
    config_path = find_config(args.config)
    cfg = load_config(config_path)
    state = StateStore(config_path)
    b = cfg.get("baidu", {})
    if not b.get("app_key") or not b.get("secret_key"):
        print("请先在 config.yaml 填好 baidu.app_key / baidu.secret_key（pan.baidu.com/union 创建应用）")
        return 1
    from .drivers.baidu import BaiduDriver

    drv = BaiduDriver(b, state, log=print)
    url = drv.authorize_url()
    print("1) 浏览器打开下面的链接并登录授权：\n")
    print(f"   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    code = input("2) 授权后页面会显示授权码，粘贴到这里：").strip()
    data = drv.exchange_code(code)
    print(f"3) 完成，access_token 已保存（有效期约 {int(data.get('expires_in', 0) / 86400)} 天，脚本会自动续期）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="panbot", description="批量上传图片到多家网盘并生成分享链接")
    parser.add_argument("--version", action="version", version=f"panbot {__version__}")
    parser.add_argument("--config", help="配置文件路径，默认 ./config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查各网盘凭证")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="每日任务：上传+分享+输出链接")
    p_run.add_argument("--source", help="图片目录（默认取 config.yaml 的 source_dir）")
    p_run.add_argument("--remote", help="网盘内目标文件夹（相对各盘根目录，支持 {date}/{name}；默认取各盘的 folder 配置）")
    p_run.add_argument("--name", help="本次的自定义文件夹名，填入 folder 模板的 {name} 占位符")
    p_run.add_argument("--drive", help="只处理指定网盘，逗号分隔，如 baidu,xunlei（默认全部启用项）")
    p_run.add_argument("--date", help="远端目录日期模板的日期，默认今天")
    p_run.add_argument("--dry-run", action="store_true", help="只打印将要做的事，不发网络请求")
    p_run.add_argument("--no-share", action="store_true", help="只上传不创建分享")
    p_run.add_argument("--no-clipboard", action="store_true", help="不自动复制链接到剪贴板")
    p_run.set_defaults(func=cmd_run)

    p_auth = sub.add_parser("baidu-auth", help="百度开放平台 OAuth 授权")
    p_auth.set_defaults(func=cmd_baidu_auth)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
