"""panbot 离线自测：不连任何网盘。

运行：python tests/test_offline.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panbot.config import StateStore, load_config  # noqa: E402
from panbot.drivers.base import BaseDriver  # noqa: E402
from panbot.drivers.quark import expired_type_for  # noqa: E402
from panbot.util import gcid, gcid_block_size, hash_file, random_passcode  # noqa: E402


def test_gcid_block_size():
    # 与 OpenList thunder GetGcid 的 calcBlockSize 对齐
    assert gcid_block_size(1) == 0x40000
    assert gcid_block_size(1 << 20) == 0x40000          # 1MB → 256KB 块
    assert gcid_block_size(0x200 * 0x40000) == 0x40000  # 恰好 512 块不升级
    assert gcid_block_size(0x200 * 0x40000 + 1) == 0x80000
    assert gcid_block_size(0x200 * 0x80000 + 1) == 0x100000
    assert gcid_block_size(0x200 * 0x100000 + 1) == 0x200000
    assert gcid_block_size(10 * (1 << 30)) == 0x200000  # 上限 2MB
    print("ok  gcid_block_size")


def test_gcid_content():
    import hashlib

    data = b"panbot-gcid-test" * 1000
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        path = f.name
    try:
        # 手工按算法重算一遍作为期望值
        psize = gcid_block_size(len(data))
        outer = hashlib.sha1()
        for i in range(0, len(data), psize):
            outer.update(hashlib.sha1(data[i : i + psize]).digest())
        assert gcid(path) == outer.hexdigest()
        assert len(gcid(path)) == 40
    finally:
        os.unlink(path)
    print("ok  gcid 内容一致性")


def test_expired_type():
    assert expired_type_for(0) == "1"    # 永久
    assert expired_type_for(1) == "2"    # 1天
    assert expired_type_for(7) == "3"    # 7天
    assert expired_type_for(30) == "4"   # 30天
    assert expired_type_for(15) == "4"   # 未知天数就近映射
    print("ok  夸克有效期映射")


def test_passcode():
    for _ in range(50):
        p = random_passcode()
        assert len(p) == 4
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789" for c in p)
    print("ok  提取码格式")


def test_collect_files():
    with tempfile.TemporaryDirectory() as d:
        for name in ("a.jpg", "b.PNG", "c.txt", "d.webp", "sub/e.gif"):
            fp = os.path.join(d, name)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "wb") as f:
                f.write(b"x")
        drv = BaseDriver({}, None)
        files = drv.collect_files(d, ["jpg", "png", "webp", "gif"])
        names = sorted(os.path.basename(f) for f in files)
        assert names == ["a.jpg", "b.PNG", "d.webp", "e.gif"], names
        hashes = hash_file(os.path.join(d, "a.jpg"))
        assert set(hashes) == {"md5", "sha1"}
    print("ok  图片收集与哈希")


def test_config_template():
    tpl = os.path.join(tempfile.mkdtemp(), "config.yaml")
    with open(tpl, "w", encoding="utf-8") as f:
        f.write("source_dir: 'D:/x'\nbaidu:\n  enable: true\n")
    cfg = load_config(tpl)
    assert cfg["baidu"]["app_key"] == ""
    assert cfg["xunlei"]["client_id"]
    assert cfg["quark"]["cookie"] == ""
    assert len(cfg["xunlei"]["algorithms"]) == 10
    state = StateStore(tpl)
    state.write_text("x.txt", "hello")
    assert state.read_text("x.txt") == "hello"
    state.write_json("y.json", {"a": 1})
    assert state.read_json("y.json")["a"] == 1
    print("ok  配置模板与状态存储")


def test_dry_run_cli():
    """CLI dry-run 全链路：不应发起任何网络请求。"""
    import io
    from contextlib import redirect_stdout

    from panbot import cli

    work = tempfile.mkdtemp()
    src = os.path.join(work, "images")
    os.makedirs(src, exist_ok=True)
    for name in ("1.png", "2.png"):
        with open(os.path.join(src, name), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + os.urandom(64))

    def write_cfg(path, baidu_extra="", folder_tpl=None):
        q = f'  folder: "{folder_tpl}"\n' if folder_tpl else ""
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "source_dir: '%s'\nbaidu:\n  enable: true\n  app_name: demo\n%s%s"
                "quark:\n  enable: true\n%sxunlei:\n  enable: true\n%s"
                % (src.replace("\\", "/"), baidu_extra, q, q, q)
            )

    def run_dry(path):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--config", path, "run", "--dry-run", "--no-clipboard"])
        return code, buf.getvalue()

    # 默认 root="/"：百度直接用网盘根目录
    cfg_path = os.path.join(work, "config.yaml")
    write_cfg(cfg_path)
    code, out = run_dry(cfg_path)
    assert code == 0, out
    assert "[baidu][dry-run] 将上传 2 个文件到 /2026-10-06" in out, out
    assert "[quark][dry-run]" in out and "[xunlei][dry-run]" in out, out
    assert "将创建分享" in out, out

    # 沙箱模式：root 指向 /apps/<应用名>
    sandbox = os.path.join(work, "config_sandbox.yaml")
    write_cfg(sandbox, '  root: "/apps/demo"\n')
    code, out = run_dry(sandbox)
    assert code == 0, out
    assert "[baidu][dry-run] 将上传 2 个文件到 /apps/demo/2026-10-06" in out, out

    # --remote 覆盖 + {name} 占位符
    cfg_name = os.path.join(work, "config_name.yaml")
    write_cfg(cfg_name, folder_tpl="上传图/{name}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(["--config", cfg_name, "run", "--dry-run", "--no-clipboard", "--name", "镜流"])
    out = buf.getvalue()
    assert code == 0, out
    assert "[baidu][dry-run] 将上传 2 个文件到 /上传图/镜流" in out, out
    assert "[quark][dry-run] 将上传 2 个文件到 上传图/镜流" in out, out
    assert "[xunlei][dry-run] 将上传 2 个文件到 上传图/镜流" in out, out
    print("ok  CLI dry-run 全链路（默认根目录 / 沙箱 / {name} 占位符）")


if __name__ == "__main__":
    test_gcid_block_size()
    test_gcid_content()
    test_expired_type()
    test_passcode()
    test_collect_files()
    test_config_template()
    test_dry_run_cli()
    print("\n全部离线测试通过 ✔")
