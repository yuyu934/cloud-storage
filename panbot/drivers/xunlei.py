"""迅雷云盘 driver（参考 OpenList drivers/thunder 与 urldb 的实现）。

- 凭证：登录 pan.xunlei.com 时抓到的 refresh_token（README 有抓取步骤）。
  access_token 约 2 小时有效，脚本自动刷新并保存轮换后的 refresh_token。
- 上传：先算 gcid（迅雷的秒传哈希），命中直接完成；否则按返回的 S3 参数分片上传。
- 分享：POST /drive/v1/share，需要 X-Captcha-Token（脚本自动获取/刷新）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

import requests

from ..util import gcid
from .base import AuthError, BaseDriver, DriverError, ShareResult

API = "https://api-pan.xunlei.com/drive/v1"
AUTH = "https://xluser-ssl.xunlei.com/v1"
CAPTCHA_ACTION = "get:/drive/v1/share"
REDIRECT_URI = "xlaccsdk01://xunlei.com/callback?state=harbor"


class XunleiDriver(BaseDriver):
    name = "xunlei"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self._access_token = ""
        self._access_expire = 0.0
        self._captcha_token = ""
        self._captcha_expire = 0.0
        self._user_id = ""
        self._refresh_token = ""
        tok = self.state.read_json("xunlei_token.json")
        if tok.get("refresh_token"):
            self._refresh_token = tok["refresh_token"]
        elif self.cfg.get("refresh_token"):
            self._refresh_token = self.cfg["refresh_token"]
        dev = self.cfg.get("device_id", "")
        if not dev:
            dev = self.state.read_text("xunlei_device_id.txt")
        if not dev:
            dev = hashlib.md5(uuid.uuid4().bytes).hexdigest()
            self.state.write_text("xunlei_device_id.txt", dev)
        self.device_id = dev
        self._dir_cache = {}

    # ---------- token ----------
    def _persist(self):
        self.state.write_json(
            "xunlei_token.json",
            {
                "refresh_token": self._refresh_token,
                "access_token": self._access_token,
                "expires_at": self._access_expire,
                "user_id": self._user_id,
            },
        )

    def _refresh_access(self) -> None:
        if not self._refresh_token:
            raise AuthError(
                "迅雷 refresh_token 为空。请按 README 抓取一次并填入 config.yaml 的 xunlei.refresh_token"
            )
        body = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self.cfg["client_id"],
        }
        # 网页版客户端不需要 client_secret；安卓客户端需要
        if self.cfg.get("client_secret"):
            body["client_secret"] = self.cfg["client_secret"]
        r = requests.post(
            f"{AUTH}/auth/token",
            json=body,
            headers={"User-Agent": self.cfg["user_agent"]},
            timeout=30,
        )
        data = r.json()
        if "access_token" not in data:
            raise AuthError(f"迅雷刷新 token 失败（refresh_token 可能已失效）：{data}")
        self._access_token = data["access_token"]
        self._access_expire = time.time() + int(data.get("expires_in", 7200)) - 120
        if data.get("refresh_token") and data["refresh_token"] != self._refresh_token:
            self._refresh_token = data["refresh_token"]
        self._user_id = str(data.get("user_id", "") or "")
        self._persist()

    def _captcha_sign(self, timestamp: str) -> str:
        # 网页版模式：使用配置里的固定签名三元组（captcha_sign + captcha_timestamp + device_id）
        if self.cfg.get("captcha_sign") and self.cfg.get("captcha_timestamp"):
            return self.cfg["captcha_sign"]
        s = "".join(
            [
                self.cfg["client_id"],
                self.cfg["client_version"],
                self.cfg["package_name"],
                self.device_id,
                timestamp,
            ]
        )
        for alg in self.cfg["algorithms"]:
            s = hashlib.md5((s + alg).encode()).hexdigest()
        return "1." + s

    def _refresh_captcha(self, action: str = CAPTCHA_ACTION) -> None:
        fixed = bool(self.cfg.get("captcha_sign") and self.cfg.get("captcha_timestamp"))
        ts = self.cfg["captcha_timestamp"] if fixed else str(int(time.time() * 1000))
        r = requests.post(
            f"{AUTH}/shield/captcha/init",
            json={
                "action": action,
                "captcha_token": "",
                "client_id": self.cfg["client_id"],
                "device_id": self.device_id,
                "meta": {
                    "client_version": self.cfg["client_version"],
                    "package_name": self.cfg["package_name"],
                    "user_id": self._user_id or "0",
                    "timestamp": ts,
                    "captcha_sign": self._captcha_sign(ts),
                },
                "redirect_uri": REDIRECT_URI,
            },
            headers={"User-Agent": self.cfg["user_agent"]},
            timeout=30,
        )
        data = r.json()
        token = data.get("captcha_token")
        if not token:
            raise AuthError(f"迅雷获取 captcha_token 失败：{data}")
        self._captcha_token = token
        self._captcha_expire = time.time() + int(data.get("expires_in", 300)) - 10

    def _ensure_tokens(self):
        if time.time() >= self._access_expire:
            self._refresh_access()
        if time.time() >= self._captcha_expire:
            self._refresh_captcha()

    # ---------- 请求 ----------
    def request(self, method: str, path: str, json_body=None, params=None, retry=1) -> dict:
        self._ensure_tokens()
        r = self.session.request(
            method,
            f"{API}{path}",
            params=params,
            json=json_body,
            headers={
                "User-Agent": self.cfg["user_agent"],
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Bearer {self._access_token}",
                "X-Captcha-Token": self._captcha_token,
                "X-Device-Id": self.device_id,
                "X-Client-Id": self.cfg["client_id"],
                "X-Client-Version": self.cfg["client_version"],
            },
            timeout=60,
        )
        try:
            data = r.json()
        except ValueError:
            raise DriverError(f"迅雷返回非 JSON：HTTP {r.status_code} {r.text[:200]}")
        err = data.get("error")
        err_code = data.get("error_code")
        if err or err_code:
            # token 失效 / 验证码 token 过期，刷新后重试一次（与 OpenList 的含义对齐）
            if retry > 0 and (err in ("UNAUTHENTICATED",) or err_code in (4122, 4121, 10, 16)):
                self._access_expire = 0
                return self.request(method, path, json_body, params, retry - 1)
            if retry > 0 and (err_code == 9 or err in ("CAPTCHA_INVALID", "captcha_require")):
                self._captcha_expire = 0
                return self.request(method, path, json_body, params, retry - 1)
            raise DriverError(
                f"迅雷接口错误 {err or err_code}：{data.get('error_description', '')} "
                f"{json.dumps(data, ensure_ascii=False)[:200]}"
            )
        return data

    def check_auth(self) -> str:
        try:
            self._refresh_access()
            self._refresh_captcha()
        except AuthError:
            raise
        about = self.request("GET", "/about")
        quota = (about or {}).get("quota", {})
        return f"迅雷 token 有效（容量 {quota.get('limit', '?')}）"

    # ---------- 目录 ----------
    def _list(self, parent_id: str) -> list:
        files, page_token = [], ""
        while True:
            out = self.request(
                "GET",
                "/files",
                params={
                    "parent_id": parent_id,
                    "page_token": page_token,
                    "limit": "100",
                    "with_audit": "true",
                    "space": "",
                    "filters": json.dumps(
                        {"phase": {"eq": "PHASE_TYPE_COMPLETE"}, "trashed": {"eq": False}}
                    ),
                },
            )
            files.extend(out.get("files", []))
            page_token = out.get("next_page_token", "")
            if not page_token:
                break
        return files

    def ensure_dir(self, remote_path: str) -> str:
        if remote_path in self._dir_cache:
            return self._dir_cache[remote_path]
        parent = ""
        for seg in [p for p in remote_path.strip("/").split("/") if p]:
            hit = None
            for it in self._list(parent):
                if it.get("kind") == "drive#folder" and it.get("name") == seg:
                    hit = it
                    break
            if hit:
                parent = hit["id"]
            else:
                out = self.request(
                    "POST",
                    "/files",
                    json_body={"kind": "drive#folder", "name": seg, "parent_id": parent, "space": ""},
                )
                parent = (out.get("file") or out).get("id", "")
                if not parent:
                    raise DriverError(f"迅雷创建目录 {seg} 失败：{out}")
        self._dir_cache[remote_path] = parent
        return parent

    # ---------- 上传 ----------
    def upload_file(self, local_path: str, remote_dir: str, dir_id=None) -> None:
        name = os.path.basename(local_path)
        size = os.path.getsize(local_path)
        file_hash = gcid(local_path)
        out = self.request(
            "POST",
            "/files",
            json_body={
                "kind": "drive#file",
                "parent_id": dir_id or "",
                "name": name,
                "size": size,
                "hash": file_hash,
                "upload_type": "UPLOAD_TYPE_RESUMABLE",
                "space": "",
            },
        )
        if out.get("upload_type") != "UPLOAD_TYPE_RESUMABLE":
            self.log(f"[xunlei] {name} 命中秒传")
            return
        params = ((out.get("resumable") or {}).get("params")) or {}
        bucket = params.get("bucket", "")
        key = params.get("key", "")
        endpoint = params.get("endpoint", "")
        if endpoint.startswith(bucket + "."):
            endpoint = endpoint[len(bucket) + 1 :]
        if not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        self._s3_upload(
            local_path,
            bucket,
            key,
            endpoint,
            params.get("access_key_id", ""),
            params.get("access_key_secret", ""),
            params.get("security_token", ""),
        )

    def _s3_upload(self, local_path, bucket, key, endpoint, ak, sk, token):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            raise DriverError("迅雷上传需要 boto3：pip install boto3") from e
        # 新版 botocore 默认给 PutObject 加 aws-chunked + 末尾校验（STREAMING-UNSIGNED-PAYLOAD-TRAILER），
        # 迅雷的 S3 兼容端点不支持，必须关掉自动校验和计算
        os.environ["AWS_REQUEST_CHECKSUM_CALCULATION"] = "when_required"
        os.environ["AWS_RESPONSE_CHECKSUM_VALIDATION"] = "when_required"
        kwargs = dict(
            endpoint_url=endpoint,
            aws_access_key_id=ak,
            aws_secret_access_key=sk,
            aws_session_token=token,
            region_name="xunlei",
        )
        try:
            config = Config(
                s3={"addressing_style": "virtual"},
                retries={"max_attempts": 3},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            )
        except (TypeError, ValueError):
            # 旧版 botocore 不认识这些选项，靠上面的环境变量兜底
            config = Config(s3={"addressing_style": "virtual"}, retries={"max_attempts": 3})
        s3 = boto3.client("s3", config=config, **kwargs)
        with open(local_path, "rb") as f:
            s3.upload_fileobj(f, bucket, key)

    # ---------- 分享 ----------
    def create_share(self, remote_path: str, title: str, password: str, days: int) -> ShareResult:
        folder_id = self.ensure_dir(remote_path)
        out = self.request(
            "POST",
            "/share",
            json_body={
                "file_ids": [folder_id],
                "share_to": "copy",
                "params": {"subscribe_push": "false", "WithPassCodeInLink": "true"},
                "title": title,
                "restore_limit": "-1",
                "expiration_days": str(days),
            },
        )
        url = out.get("share_url", "")
        pwd = out.get("pass_code", "") or ""
        if pwd and "pwd=" not in url:
            url = f"{url}?pwd={pwd}"
        name = remote_path.rstrip("/").split("/")[-1]
        promo = f"我用迅雷云盘给你分享了「{name}」，复制链接打开「迅雷」APP即可获取。\n链接：{url}"
        return ShareResult(
            drive=self.name,
            folder=remote_path,
            url=url,
            password=pwd,
            expire=f"{days}天" if days else "永久",
            promo_text=promo,
        )
