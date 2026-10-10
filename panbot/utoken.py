"""夸克分享口令（口令/Utoken）生成。

夸克官方有一个"口令"服务 `https://utoken2.uc.cn/utoken/v2/create`，它要求请求带
`sign`，而 sign 由客户端里的原生模块 `quantum.wsg.sign` 计算（密钥藏在原生层，
无法在纯 Python 里复刻）。因此这里通过 Electron 客户端的远程调试端口（CDP）
在客户端内部完成签名和请求，取回口令字符串。

前提：夸克客户端在运行，且带 `--remote-debugging-port=<port>` 启动。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request

DEFAULT_PORT = 9222
UTOKEN_URL = "https://utoken2.uc.cn/utoken/v2/create"
SECURE_NO = 14100

# shareCode 决定口令形态：
#   quark_cloud_disk → "~xxxxxxxxxx~:/" 形式的英文口令（客户端复制出来的那种）
#   auto3_c_code     → 12 个汉字的口令（手机 App 分享文案里的那种）
SHARE_CODE_TOKEN = "quark_cloud_disk"
SHARE_CODE_CHINESE = "auto3_c_code"

# 客户端 u.createDriveShare 用的基础参数（取自客户端 bundle）
BASE_PARAMS = {
    "app": "QUARK",
    "buttonThemeColor": "",
    "buttonTitle": "立即查看",
    "businessCode": "quark_cloud_disk",
    "dialogContent": "来自剪切板",
    "shareContent": "",
    "imageUrl": "",
    "expiredTimestamp": "",
}

_JS_TEMPLATE = r"""
(async () => {
  try {
    if (typeof quantum === 'undefined' || !quantum.wsg) return JSON.stringify({error: 'no-quantum'});
    const base = %(base)s;
    const url = %(url)s;
    const codes = %(codes)s;
    const toSign = (p) => Object.keys(p).filter(k => k !== 'timestamp').sort().map(k => p[k]).join('') + p.timestamp;
    const call = (shareCode) => {
      const p = Object.assign({}, base, {
        shareCode: shareCode,
        iOSUrl: url + '?way=u_token',
        androidUrl: url + '?way=u_token',
        nickname: '', dialogTitle: %(title)s, avatar: '',
        timestamp: String(Date.now())
      });
      const sign = quantum.wsg.sign({number: %(secure_no)d, value_to_sign: toSign(p)}).sign;
      const body = JSON.stringify(Object.assign({}, p, {sign: sign}));
      const req_ = new Promise((resolve, reject) => {
        const req = quantum.net.request({method: 'POST', url: %(endpoint)s});
        req.setHeader('Content-Type', 'application/json');
        let buf = '';
        req.on('response', r => { r.on('data', d => buf += d.toString()); r.on('end', () => resolve(buf)); });
        req.on('error', e => reject(new Error('net: ' + e)));
        req.write(body); req.end();
      });
      const guard = new Promise(res => setTimeout(() => res('{"__timeout":true}'), 20000));
      return Promise.race([req_, guard]);
    };
    const out = {};
    for (const code of codes) {
      try {
        const txt = await call(code);
        const j = JSON.parse(txt);
        out[code] = (j.data && j.data.value) ? j.data.value : ('ERROR:' + (j.message || j.code || txt.slice(0, 80)));
      } catch (e) { out[code] = 'ERROR:' + String(e && e.message || e); }
    }
    return JSON.stringify(out);
  } catch (e) { return JSON.stringify({error: String(e && e.message || e)}); }
})()
"""


class UtokenError(Exception):
    pass


def debug_port_alive(port: int = DEFAULT_PORT, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout):
            return True
    except Exception:
        return False


def launch_client(exe: str, port: int = DEFAULT_PORT, wait: float = 25.0) -> bool:
    """带调试端口拉起夸克客户端（若已在运行则无副作用）。"""
    if not exe or not os.path.exists(exe):
        return False
    subprocess.Popen([exe, f"--remote-debugging-port={port}"], close_fds=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if debug_port_alive(port):
            return True
        time.sleep(1)
    return False


def _pick_target(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        targets = json.load(r)
    pages = [t for t in targets if t.get("type") == "page"]
    for t in pages:
        if "index.html" in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    for t in pages:
        if t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise UtokenError("客户端里没有可用的页面目标")


def create_utokens(url: str, title: str = "分享链接", port: int = DEFAULT_PORT,
                   timeout: float = 60.0) -> dict:
    """一次取两种口令，返回 {"token": "/~xxx~:/", "chinese": "汉字口令"}（取不到的为空串）。"""
    try:
        import websocket  # websocket-client
    except ImportError as e:
        raise UtokenError("需要 websocket-client：pip install websocket-client") from e

    if not debug_port_alive(port):
        raise UtokenError(
            f"夸克客户端未开启调试端口（127.0.0.1:{port}）。"
            "请用 `quark.exe --remote-debugging-port=9222` 启动，或在配置里打开 client_autostart。"
        )

    codes = [SHARE_CODE_CHINESE, SHARE_CODE_TOKEN]
    expr = _JS_TEMPLATE % {
        "base": json.dumps(BASE_PARAMS, ensure_ascii=False),
        "url": json.dumps(url),
        "title": json.dumps(title, ensure_ascii=False),
        "codes": json.dumps(codes),
        "secure_no": SECURE_NO,
        "endpoint": json.dumps(UTOKEN_URL),
    }

    ws = websocket.create_connection(_pick_target(port), timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") != 1:
                continue
            value = (msg.get("result") or {}).get("result", {}).get("value")
            if not value:
                raise UtokenError(f"客户端返回为空：{json.dumps(msg, ensure_ascii=False)[:200]}")
            data = json.loads(value)
            if data.get("error"):
                raise UtokenError(f"客户端内调用失败：{data['error']}")
            result = {"token": "", "chinese": "", "errors": {}}
            for code, val in data.items():
                if not isinstance(val, str) or val.startswith("ERROR:"):
                    result["errors"][code] = val if isinstance(val, str) else str(val)
                    continue
                if code == SHARE_CODE_TOKEN:
                    result["token"] = val if val.startswith("/") else f"/{val}"
                elif code == SHARE_CODE_CHINESE:
                    result["chinese"] = val
            return result
        raise UtokenError("等待客户端响应超时")
    finally:
        ws.close()
