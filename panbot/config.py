"""配置与状态持久化。

config.yaml 存凭证与选项（不入库）；.panbot_state/ 存自动轮换的 token/cookie，
比如夸克的 __puus、迅雷轮换后的 refresh_token、百度 access_token。
"""

from __future__ import annotations

import json
import os

import yaml

DEFAULT_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "heic"]


class ConfigError(Exception):
    pass


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise ConfigError(
            f"配置文件不存在：{path}\n请复制 config.example.yaml 为 config.yaml 并填入凭证。"
        )
    with open(path, "r", encoding="utf-8") as f:
        try:
            cfg = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            loc = f"（第 {mark.line + 1} 行第 {mark.column + 1} 列附近）" if mark else ""
            raise ConfigError(
                f"config.yaml 语法错误{loc}：{getattr(e, 'problem', e)}\n"
                "常见原因：Windows 路径用了双引号（\"D:\\1\\图片\"），反斜杠会被当成转义符。\n"
                '改成单引号（\'D:\\1\\图片\'）或正斜杠（"D:/1/图片"）即可。'
            )
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml 顶层应为键值映射，请检查格式。")
    cfg.setdefault("source_dir", "")
    cfg.setdefault("extensions", DEFAULT_EXTENSIONS)
    cfg.setdefault("date_dir", "{date}")
    cfg.setdefault("output", {"markdown": "links.md", "csv": "links_log.csv"})
    cfg.setdefault("share", {})
    for key in ("baidu", "quark", "xunlei"):
        cfg.setdefault(key, {"enable": False})
        cfg[key].setdefault("enable", False)
        cfg[key].setdefault("folder", "{date}")
        cfg[key].setdefault("share", {})
    cfg["baidu"].setdefault("app_name", "")
    cfg["baidu"].setdefault("app_key", "")
    cfg["baidu"].setdefault("secret_key", "")
    cfg["baidu"].setdefault("bduss", "")
    cfg["baidu"].setdefault("stoken", "")
    cfg["quark"].setdefault("cookie", "")
    # 口碑/推广用的"口令"：需要夸克客户端配合（走它的原生签名）
    cfg["quark"].setdefault("cmd_token", True)
    cfg["quark"].setdefault("client_exe", "")  # 夸克客户端路径，需自行配置
    cfg["quark"].setdefault("client_debug_port", 9222)
    cfg["quark"].setdefault("client_autostart", False)
    x = cfg["xunlei"]
    x.setdefault("refresh_token", "")
    x.setdefault("client_id", "Xp6vsxz_7IYVw2BB")
    x.setdefault("client_secret", "Xp6vsy4tN9toTVdMSpomVdXpRmES")
    x.setdefault("client_version", "8.31.0.9726")
    x.setdefault("package_name", "com.xunlei.downloadprovider")
    x.setdefault(
        "user_agent",
        "ANDROID-com.xunlei.downloadprovider/8.31.0.9726 netWorkType/5G appid/40 "
        "deviceName/Xiaomi_M2004j7ac deviceModel/M2004J7AC OSVersion/12 "
        "protocolVersion/301 platformVersion/10 sdkVersion/512000 "
        "Oauth2Client/0.9 (Linux 4_14_186-perf-gddfs8vbb238b) (JAVA 0)",
    )
    x.setdefault(
        "algorithms",
        [
            "9uJNVj/wLmdwKrJaVj/omlQ",
            "Oz64Lp0GigmChHMf/6TNfxx7O9PyopcczMsnf",
            "Eb+L7Ce+Ej48u",
            "jKY0",
            "ASr0zCl6v8W4aidjPK5KHd1Lq3t+vBFf41dqv5+fnOd",
            "wQlozdg6r1qxh0eRmt3QgNXOvSZO6q/GXK",
            "gmirk+ciAvIgA/cxUUCema47jr/YToixTT+Q6O",
            "5IiCoM9B1/788ntB",
            "P07JH0h6qoM6TSUAK2aL9T5s2QBVeY9JWvalf",
            "+oK0AN",
        ],
    )
    # 网页版登录模式下使用固定签名三元组（与 device_id 配套），优先于 algorithms
    x.setdefault("captcha_sign", "")
    x.setdefault("captcha_timestamp", "")
    x.setdefault("device_id", "")
    return cfg


class StateStore:
    """在配置文件同目录的 .panbot_state/ 下读写轮换凭证。"""

    def __init__(self, config_path: str):
        base = os.path.dirname(os.path.abspath(config_path))
        self.dir = os.path.join(base, ".panbot_state")
        os.makedirs(self.dir, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def read_json(self, name: str) -> dict:
        p = self.path(name)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def write_json(self, name: str, data: dict) -> None:
        with open(self.path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_text(self, name: str) -> str:
        p = self.path(name)
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()

    def write_text(self, name: str, text: str) -> None:
        with open(self.path(name), "w", encoding="utf-8") as f:
            f.write(text)
