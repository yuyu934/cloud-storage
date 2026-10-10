"""百度网盘 driver。

- 上传/建目录：官方开放平台 xpan API（需在 pan.baidu.com/union 注册应用拿 AppKey/SecretKey，
  再用 `panbot baidu-auth` 完成 OAuth 授权拿 access_token）。未通过审核的个人应用
  只能访问 /apps/<app_name>/ 目录，所以远端路径固定挂在这下面。
- 分享：开放平台的分享接口是付费接口，这里改用网页版 share/pset 接口，
  只需要浏览器里的 BDUSS cookie（与 BaiduPCS-Go 的做法一致）。
"""

from __future__ import annotations

import json
import time

import requests

from ..util import md5_hex, random_passcode
from .base import AuthError, BaseDriver, DriverError, ShareResult

OPENAPI = "https://openapi.baidu.com"
PAN_API = "https://pan.baidu.com"
PCS_UPLOAD = "https://d.pcs.baidu.com"
BLOCK = 4 * 1024 * 1024

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SHARE_ERRNO = {
    0: "成功",
    2: "参数错误",
    -6: "身份验证失败（BDUSS 已失效，请重新复制）",
    -9: "文件不存在（检查 app_name 与路径）",
    12: "操作过于频繁或分享数已达上限",
    105: "提取码格式错误（须为 4 位小写字母/数字）",
    130: "分享内容受限",
}


class BaiduDriver(BaseDriver):
    name = "baidu"

    # ---------- OAuth ----------
    def authorize_url(self) -> str:
        return (
            f"{OPENAPI}/oauth/2.0/authorize?response_type=code&client_id={self.cfg['app_key']}"
            "&redirect_uri=oob&scope=basic,netdisk&display=page"
        )

    def exchange_code(self, code: str) -> dict:
        r = requests.get(
            f"{OPENAPI}/oauth/2.0/token",
            params={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.cfg["app_key"],
                "client_secret": self.cfg["secret_key"],
                # 必须与授权链接中的 redirect_uri 完全一致，缺了会报 missing a required parameter
                "redirect_uri": "oob",
            },
            timeout=30,
        )
        data = r.json()
        if "access_token" not in data:
            raise AuthError(
                f"换取 access_token 失败：{data}\n"
                "常见原因：授权码已过期或已使用过（一次性），重新运行 baidu-auth 再授权一次即可。"
            )
        self._save_token(data)
        return data

    def _save_token(self, data: dict) -> None:
        self.state.write_json(
            "baidu_token.json",
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": time.time() + int(data.get("expires_in", 2592000)),
            },
        )

    def access_token(self) -> str:
        tok = self.state.read_json("baidu_token.json")
        if not tok.get("access_token"):
            raise AuthError(
                "百度尚未授权。请先注册开放平台应用（pan.baidu.com/union），"
                "再运行：python -m panbot baidu-auth"
            )
        if tok.get("expires_at", 0) - time.time() < 86400 and tok.get("refresh_token"):
            r = requests.get(
                f"{OPENAPI}/oauth/2.0/token",
                params={
                    "grant_type": "refresh_token",
                    "refresh_token": tok["refresh_token"],
                    "client_id": self.cfg["app_key"],
                    "client_secret": self.cfg["secret_key"],
                },
                timeout=30,
            )
            data = r.json()
            if "access_token" in data:
                self._save_token(data)
                return data["access_token"]
        return tok["access_token"]

    # ---------- xpan 基础 ----------
    def _xpan(self, method: str, params=None, data=None, headers=None) -> dict:
        params = dict(params or {})
        params["access_token"] = self.access_token()
        params.setdefault("method", method)
        r = requests.post(
            f"{PAN_API}/rest/2.0/xpan/file",
            params=params,
            data=data,
            headers={"User-Agent": BROWSER_UA, **(headers or {})},
            timeout=60,
        )
        try:
            return r.json()
        except ValueError:
            raise DriverError(f"百度返回非 JSON：HTTP {r.status_code} {r.text[:200]}")

    def _xpan_ok(self, method: str, params=None, data=None, ok_errnos=(0,)) -> dict:
        out = self._xpan(method, params, data)
        if out.get("errno", 0) not in ok_errnos:
            raise DriverError(
                f"百度接口 {method} 失败 errno={out.get('errno')}：{json.dumps(out, ensure_ascii=False)[:300]}"
            )
        return out

    def check_auth(self) -> str:
        tok = self.state.read_json("baidu_token.json")
        if not tok.get("access_token"):
            raise AuthError("百度未授权，先运行 python -m panbot baidu-auth")
        if not self.cfg.get("bduss"):
            raise AuthError("百度缺少 BDUSS（创建分享需要），请在 config.yaml 填写")
        r = requests.get(
            f"{PAN_API}/rest/2.0/xpan/nas",
            params={"method": "uinfo", "access_token": self.access_token()},
            headers={"User-Agent": BROWSER_UA},
            timeout=30,
        )
        try:
            info = r.json()
        except ValueError:
            raise AuthError(f"百度用户信息接口返回异常：HTTP {r.status_code} {r.text[:200]}")
        if info.get("errno") != 0:
            raise AuthError(f"百度 access_token 无效 errno={info.get('errno')}")
        return f"已授权：{info.get('baidu_name', '?')}（分享用 BDUSS 已配置）"

    def root(self) -> str:
        """上传/分享的根目录。默认网盘根目录（""）；沙箱应用请在 config 里
        设 root: "/apps/<应用名>"。"""
        r = (self.cfg.get("root") or "").strip()
        if r in ("", "/"):
            return ""
        return "/" + r.strip("/")

    def app_name(self) -> str:
        return (self.cfg.get("app_name") or "").strip("/ ")

    # ---------- 目录 ----------
    def _xpan_list(self, dir_path: str) -> dict:
        r = requests.get(
            f"{PAN_API}/rest/2.0/xpan/file",
            params={
                "method": "list",
                "access_token": self.access_token(),
                "dir": dir_path,
                "limit": "1",
            },
            headers={"User-Agent": BROWSER_UA},
            timeout=30,
        )
        try:
            return r.json()
        except ValueError:
            raise DriverError(f"百度列目录返回非 JSON：HTTP {r.status_code} {r.text[:200]}")

    def ensure_dir(self, remote_path: str) -> str:
        root = self.root()
        if root and not remote_path.rstrip("/").startswith(root):
            raise DriverError(f"百度路径必须以配置的 root 开头：{remote_path}（root={root}）")
        if root:
            # 沙箱模式：/apps/<应用名> 由百度生成，不主动创建，先确认可访问
            check = self._xpan_list(root)
            if check.get("errno") != 0:
                raise DriverError(
                    f"百度根目录 {root} 不可访问（errno={check.get('errno')}）。"
                    "请确认 baidu.app_name 与开放平台应用名一致，"
                    "或改用 root: \"/\"（非沙箱应用可直接用网盘根目录）。"
                )
        cur = root
        rest = remote_path.rstrip("/")[len(root):].strip("/")
        for part in [p for p in rest.split("/") if p]:
            cur += f"/{part}"
            out = self._xpan("create", data={"path": cur, "isdir": "1"})
            # -8 = 已存在，直接复用
            if out.get("errno", 0) not in (0, -8):
                raise DriverError(f"创建目录 {cur} 失败：{out}")
        return remote_path

    # ---------- 上传 ----------
    def upload_file(self, local_path: str, remote_dir: str, dir_id=None) -> None:
        import os

        name = os.path.basename(local_path)
        path = f"{remote_dir.rstrip('/')}/{name}"
        size = os.path.getsize(local_path)
        with open(local_path, "rb") as f:
            blocks = []
            while True:
                chunk = f.read(BLOCK)
                if not chunk:
                    break
                blocks.append(md5_hex(chunk))
        pre = self._xpan_ok(
            "precreate",
            data={
                "path": path,
                "size": str(size),
                "isdir": "0",
                "autoinit": "1",
                "block_list": json.dumps(blocks),
            },
        )
        if pre.get("return_type") == 2:
            self.log(f"[baidu] {name} 命中秒传")
            return
        uploadid = pre.get("uploadid")
        if not uploadid:
            raise DriverError(f"百度 precreate 未返回 uploadid：{pre}")
        with open(local_path, "rb") as f:
            seq = 0
            while True:
                chunk = f.read(BLOCK)
                if not chunk:
                    break
                r = requests.post(
                    f"{PCS_UPLOAD}/rest/2.0/pcs/superfile2",
                    params={
                        "method": "upload",
                        "access_token": self.access_token(),
                        "type": "object",
                        "uploadid": uploadid,
                        "path": path,
                        "partseq": str(seq),
                    },
                    files={"file": chunk},
                    headers={"User-Agent": BROWSER_UA},
                    timeout=300,
                )
                out = r.json()
                if out.get("errno", 0) != 0:
                    raise DriverError(f"百度分片上传失败（第{seq}片）：{out}")
                seq += 1
        self._xpan_ok(
            "create",
            data={
                "path": path,
                "size": str(size),
                "isdir": "0",
                "uploadid": uploadid,
                "block_list": json.dumps(blocks),
            },
        )

    # ---------- 分享 ----------
    def create_share(self, remote_path: str, title: str, password: str, days: int) -> ShareResult:
        pwd = (password or "").strip().lower()
        if len(pwd) != 4 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789" for c in pwd):
            pwd = random_passcode()
        cookies = {"BDUSS": self.cfg["bduss"]}
        if self.cfg.get("stoken"):
            cookies["STOKEN"] = self.cfg["stoken"]
        r = requests.post(
            f"{PAN_API}/share/pset",
            data={
                "path_list": json.dumps([remote_path]),
                "schannel": "4",
                "channel_list": "[]",
                "period": str(days),
                "pwd": pwd,
                "share_type": "9",
            },
            cookies=cookies,
            headers={"User-Agent": BROWSER_UA, "Referer": "https://pan.baidu.com/"},
            timeout=30,
        )
        try:
            out = r.json()
        except ValueError:
            raise DriverError(f"百度分享接口返回异常：HTTP {r.status_code} {r.text[:200]}")
        errno = out.get("errno")
        if errno != 0:
            reason = SHARE_ERRNO.get(errno, f"errno={errno}")
            raise DriverError(f"百度创建分享失败：{reason}（完整响应 {json.dumps(out, ensure_ascii=False)[:200]}）")
        url = out.get("link") or out.get("shorturl") or ""
        if url and not url.startswith("http"):
            url = f"https://pan.baidu.com/s/{url}"
        name = remote_path.rstrip("/").split("/")[-1]
        promo = f"我用百度网盘给你分享了「{name}」，复制链接打开「百度网盘」APP即可获取。\n链接：{url}"
        if pwd:
            promo += f"\n提取码：{pwd}"
        return ShareResult(
            drive=self.name,
            folder=remote_path,
            url=url,
            password=pwd,
            expire=f"{days}天" if days else "永久",
            promo_text=promo,
        )
