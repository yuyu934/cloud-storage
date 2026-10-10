"""夸克网盘 driver（无官方公开 API，走网页版 cookie 接口，参考 OpenList quark_uc
driver 与 QuarkPanTool 的实现）。

- 凭证：浏览器登录 pan.quark.cn 后复制整条 Cookie 填进 config.yaml。
  每次响应服务器可能下发新的 __puus/__pus，脚本会自动滚动并保存到
  .panbot_state/quark_cookie.txt，之后优先使用滚动后的新 cookie。
- 上传：pre → hash(秒传判定) → OSS 分片 → commit → finish。
- 分享：POST /share → 轮询 /task → POST /share/password 换取链接。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
import urllib.parse

import requests

from ..util import hash_file, random_passcode
from .base import AuthError, BaseDriver, DriverError, ShareResult

API = "https://drive-pc.quark.cn/1/clouddrive"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4-b478491100 "
    "Safari/537.36 Channel/pckk_other_ch"
)
REFERER = "https://pan.quark.cn"
BROWSER_REFERER = "https://pan.quark.cn/"

# 分享有效期枚举：1=永久 2=1天 3=7天 4=30天（QuarkPanTool 同款映射）
EXPIRED_TYPE = {0: "1", 1: "2", 3: "2", 7: "3", 15: "4", 30: "4", 365: "1"}


def expired_type_for(days: int) -> str:
    if days in EXPIRED_TYPE:
        return EXPIRED_TYPE[days]
    return "1" if days == 0 else "4"


class QuarkDriver(BaseDriver):
    name = "quark"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cookie = None
        self._dir_cache = {}
        self.session = requests.Session()

    # ---------- cookie ----------
    @property
    def cookie(self) -> str:
        if self._cookie is None:
            rolled = self.state.read_text("quark_cookie.txt")
            self._cookie = rolled or self.cfg.get("cookie", "")
        if not self._cookie:
            raise AuthError("夸克 cookie 为空，请浏览器登录 pan.quark.cn 后把 Cookie 复制到 config.yaml")
        return self._cookie

    def _persist_cookie(self):
        if self._cookie:
            self.state.write_text("quark_cookie.txt", self._cookie)

    def _absorb_cookies(self, resp):
        """把服务器下发的新 __puus/__pus 滚回 cookie（保持会话长期有效）。"""
        new = dict(self._parse_cookie(self.cookie))
        changed = False
        for c in resp.cookies:
            if c.name in ("__puus", "__pus") and c.value:
                new[c.name] = c.value
                changed = True
        if changed:
            self._cookie = "; ".join(f"{k}={v}" for k, v in new.items())
            self._persist_cookie()

    @staticmethod
    def _parse_cookie(text: str) -> dict:
        out = {}
        for part in text.split(";"):
            if "=" in part:
                k, _, v = part.strip().partition("=")
                out[k] = v
        return out

    # ---------- 请求 ----------
    def request(self, path: str, method: str = "GET", json_body=None, params=None) -> dict:
        url = f"{API}{path}"
        p = {"pr": "ucpro", "fr": "pc"}
        p.update(params or {})
        r = self.session.request(
            method,
            url,
            params=p,
            json=json_body,
            headers={"Cookie": self.cookie, "Referer": REFERER, "User-Agent": UA},
            timeout=60,
        )
        self._absorb_cookies(r)
        try:
            out = r.json()
        except ValueError:
            raise DriverError(f"夸克返回非 JSON：HTTP {r.status_code} {r.text[:200]}")
        if r.status_code >= 400 or out.get("code") not in (0, None):
            # 任务类接口轮询时 code 可能短暂非 0，由调用方处理，这里统一抛
            raise DriverError(
                f"夸克接口错误 code={out.get('code')} status={out.get('status')}：{out.get('message', out)}"
            )
        return out.get("data", {})

    def check_auth(self) -> str:
        self.request("/member", params={"fetch_subscribe": "false", "_ch": "home", "fetch_identity": "false"})
        return "夸克 cookie 有效"

    # ---------- 目录 ----------
    def _list_dir(self, pdir_fid: str) -> list:
        items, page = [], 1
        while True:
            out = self.request(
                "/file/sort",
                params={
                    "pdir_fid": pdir_fid,
                    "_page": str(page),
                    "_size": "100",
                    "_fetch_total": "1",
                    "fetch_all_file": "1",
                    "fetch_risk_file_name": "1",
                },
            )
            lst = (out or {}).get("list", [])
            items.extend(lst)
            total = ((out or {}).get("metadata") or {}).get("total", 0)
            if page * 100 >= total or not lst:
                break
            page += 1
        return items

    def _find_child(self, pdir_fid: str, name: str):
        for it in self._list_dir(pdir_fid):
            if it.get("file_name") == name or it.get("filename") == name:
                return it
        return None

    def ensure_dir(self, remote_path: str) -> str:
        if remote_path in self._dir_cache:
            return self._dir_cache[remote_path]
        fid = "0"
        for seg in [p for p in remote_path.strip("/").split("/") if p]:
            hit = self._find_child(fid, seg)
            if hit and (hit.get("dir") or hit.get("file_type") == 0):
                fid = hit["fid"]
            elif hit:
                raise DriverError(f"夸克路径冲突：{seg} 已是文件")
            else:
                out = self.request(
                    "/file",
                    method="POST",
                    json_body={"dir_init_lock": False, "dir_path": "", "file_name": seg, "pdir_fid": fid},
                )
                fid = (out or {}).get("fid", "")
                if not fid:
                    # 可能同名已存在，重新查一次
                    hit = self._find_child(fid if fid else "0", seg)
                    if not hit:
                        raise DriverError(f"夸克创建目录 {seg} 失败")
                    fid = hit["fid"]
        self._dir_cache[remote_path] = fid
        return fid

    # ---------- 上传 ----------
    def upload_file(self, local_path: str, remote_dir: str, dir_id=None) -> None:
        import os

        name = os.path.basename(local_path)
        size = os.path.getsize(local_path)
        mime = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        hashes = hash_file(local_path, ("md5", "sha1"))
        now = int(time.time() * 1000)
        pre = self.request(
            "/file/upload/pre",
            method="POST",
            json_body={
                "ccp_hash_update": True,
                "dir_name": "",
                "file_name": name,
                "format_type": mime,
                "l_created_at": now,
                "l_updated_at": now,
                "pdir_fid": dir_id or "0",
                "size": size,
            },
        )
        task_id = pre.get("task_id", "")
        meta = pre.get("metadata", {}) or {}
        part_size = int(meta.get("part_size") or 16 * 1024 * 1024)
        hash_out = self.request(
            "/file/update/hash",
            method="POST",
            json_body={"md5": hashes["md5"], "sha1": hashes["sha1"], "task_id": task_id},
        )
        if hash_out.get("finish"):
            self.log(f"[quark] {name} 命中秒传")
            return

        etags = []
        with open(local_path, "rb") as f:
            idx = 0
            while True:
                chunk = f.read(part_size)
                if not chunk:
                    break
                idx += 1
                etags.append(self._upload_part(pre, mime, idx, chunk))
        self._commit(pre, etags)
        self.request("/file/upload/finish", method="POST", json_body={"obj_key": pre.get("obj_key", ""), "task_id": task_id})
        time.sleep(1)

    def _oss_url(self, pre) -> str:
        bucket = pre["bucket"]
        host = pre["upload_url"]
        if host.startswith("http"):
            host = host.split("://", 1)[1]
        return f"https://{bucket}.{host}"

    def _upload_part(self, pre, mime: str, part_number: int, chunk: bytes) -> str:
        time_str = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
        auth_out = self.request(
            "/file/upload/auth",
            method="POST",
            json_body={
                "auth_info": pre.get("auth_info", ""),
                "auth_meta": (
                    "PUT\n\n"
                    f"{mime}\n{time_str}\n"
                    "x-oss-date:" + time_str + "\n"
                    "x-oss-user-agent:aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit\n"
                    f"/{pre['bucket']}/{pre['obj_key']}?partNumber={part_number}&uploadId={pre['upload_id']}"
                ),
                "task_id": pre.get("task_id", ""),
            },
        )
        url = (
            f"{self._oss_url(pre)}/{pre['obj_key']}?"
            + urllib.parse.urlencode({"partNumber": str(part_number), "uploadId": pre["upload_id"]})
        )
        r = requests.put(
            url,
            data=chunk,
            headers={
                "Authorization": auth_out.get("auth_key", ""),
                "Content-Type": mime,
                "Referer": BROWSER_REFERER,
                "x-oss-date": time_str,
                "x-oss-user-agent": "aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit",
            },
            timeout=300,
        )
        if r.status_code != 200:
            raise DriverError(f"夸克分片上传失败：HTTP {r.status_code} {r.text[:200]}")
        return r.headers.get("Etag", "").strip('"')

    def _commit(self, pre, etags: list) -> None:
        time_str = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<CompleteMultipartUpload>\n'
        for i, etag in enumerate(etags, 1):
            xml += f"<Part>\n<PartNumber>{i}</PartNumber>\n<ETag>{etag}</ETag>\n</Part>\n"
        xml += "</CompleteMultipartUpload>"
        content_md5 = base64.b64encode(hashlib.md5(xml.encode()).digest()).decode()
        callback_b64 = ""
        if pre.get("callback"):
            callback_b64 = base64.b64encode(
                json.dumps(pre["callback"]).encode() if not isinstance(pre["callback"], str) else pre["callback"].encode()
            ).decode()
        auth_out = self.request(
            "/file/upload/auth",
            method="POST",
            json_body={
                "auth_info": pre.get("auth_info", ""),
                "auth_meta": (
                    f"POST\n{content_md5}\napplication/xml\n{time_str}\n"
                    f"x-oss-callback:{callback_b64}\n"
                    "x-oss-date:" + time_str + "\n"
                    "x-oss-user-agent:aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit\n"
                    f"/{pre['bucket']}/{pre['obj_key']}?uploadId={pre['upload_id']}"
                ),
                "task_id": pre.get("task_id", ""),
            },
        )
        r = requests.post(
            f"{self._oss_url(pre)}/{pre['obj_key']}?uploadId={pre['upload_id']}",
            data=xml.encode(),
            headers={
                "Authorization": auth_out.get("auth_key", ""),
                "Content-MD5": content_md5,
                "Content-Type": "application/xml",
                "Referer": BROWSER_REFERER,
                "x-oss-callback": callback_b64,
                "x-oss-date": time_str,
                "x-oss-user-agent": "aliyun-sdk-js/6.6.1 Chrome 98.0.4758.80 on Windows 10 64-bit",
            },
            timeout=300,
        )
        if r.status_code != 200:
            raise DriverError(f"夸克合并分片失败：HTTP {r.status_code} {r.text[:200]}")

    # ---------- 分享 ----------
    def create_share(self, remote_path: str, title: str, password: str, days: int) -> ShareResult:
        fid = self.ensure_dir(remote_path)
        share_cfg = self.cfg.get("share", {})
        private = bool(share_cfg.get("private", False))
        url_type = 2 if private else 1
        body = {
            "fid_list": [fid],
            "title": title,
            "url_type": url_type,
            "expired_type": expired_type_for(days),
        }
        pwd = ""
        if private:
            pwd = password or share_cfg.get("password") or random_passcode()
            body["passcode"] = pwd
        out = self.request("/share", method="POST", json_body=body, params={"uc_param_str": ""})
        task_id = out.get("task_id", "")
        share_id = ""
        for _ in range(20):
            task = self.request("/task", params={"task_id": task_id, "retry_index": "0", "uc_param_str": ""})
            if task.get("share_id"):
                share_id = task["share_id"]
                break
            if task.get("status") == 2 and task.get("share_id"):
                share_id = task["share_id"]
                break
            time.sleep(1)
        if not share_id:
            raise DriverError(f"夸克分享任务未完成：task_id={task_id}")
        final = self.request(
            "/share/password",
            method="POST",
            json_body={"share_id": share_id},
            params={"uc_param_str": ""},
        )
        base_url = final.get("share_url", "")
        pwd = final.get("passcode", "") or final.get("pass_code", "")
        url = base_url
        if pwd and "pwd=" not in url:
            url = f"{url}?pwd={pwd}"
        # 口令要用不带 ?pwd= 的原始分享链接生成（与客户端行为一致）
        utokens = self._utoken(base_url, title)
        chinese, token = utokens.get("chinese", ""), utokens.get("token", "")
        # 与手机 App 一致的文案：中文口令一行 + 英文口令一行
        promo = (
            f"我用夸克网盘给你分享了「{title}」，点击链接或复制整段内容，打开「夸克APP」即可获取。\n"
            + (f"{chinese}\n" if chinese else "")
            + (f"{token}\n" if token else "")
            + f"链接：{url}"
        )
        if pwd:
            promo += f"\n提取码：{pwd}"
        return ShareResult(
            drive=self.name,
            folder=remote_path,
            url=url,
            password=pwd,
            expire=f"{days}天" if days else "永久",
            promo_text=promo,
            cmd_text=chinese,
        )

    def _utoken(self, share_url: str, title: str) -> dict:
        """取分享口令（需要夸克客户端配合）；失败只警告，不影响分享链接。"""
        if not self.cfg.get("cmd_token", True):
            return {}
        from .. import utoken as utoken_mod

        port = int(self.cfg.get("client_debug_port", 9222))
        try:
            if not utoken_mod.debug_port_alive(port) and self.cfg.get("client_autostart"):
                self.log(f"[quark] 调试端口未开，尝试启动客户端（{self.cfg.get('client_exe')}）")
                utoken_mod.launch_client(self.cfg.get("client_exe", ""), port)
            res = utoken_mod.create_utokens(share_url, title=title, port=port)
            if res.get("chinese"):
                self.log(f"[quark] 中文口令 {res['chinese']}")
            if res.get("token"):
                self.log(f"[quark] 英文口令 {res['token']}")
            for code, err in (res.get("errors") or {}).items():
                self.log(f"[quark] 口令({code})获取失败：{err}")
            return res
        except Exception as e:
            self.log(f"[quark] 口令生成失败（分享链接不受影响）：{e}")
            return {}
