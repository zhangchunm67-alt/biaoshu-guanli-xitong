"""远程入口的登录限速、会话与操作日志。

- LoginGuard：按来源和全局统计口令错误，错误过多时逐步延长等待并临时锁定。
- Sessions：登录成功后签发随机会话号（Cookie 不再携带口令本身），带有效期。
- AuditLog：记录登录、材料变更、审核、导出等操作的时间与来源入口，不记录正文。
- DeviceTrust：登录成功后给浏览器签发长期“已信任设备”标记。花生壳穿透后所有来源
  都是 127.0.0.1，按来源锁定会让一个人输错口令锁住所有同事；带有效标记的浏览器
  使用自己的错误计数，不受陌生来源触发的锁定影响。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# 同一来源连续错误 FREE_ATTEMPTS 次后开始锁定，锁定时长逐次翻倍，最长 MAX_LOCK 秒。
FREE_ATTEMPTS = 5
BASE_LOCK = 60
MAX_LOCK = 30 * 60
# 全局：WINDOW 秒内累计 GLOBAL_LIMIT 次错误即锁定整个入口 GLOBAL_LOCK 秒。
# 花生壳穿透后所有请求都来自 127.0.0.1，按来源计数不够，全局计数兜底。
GLOBAL_WINDOW = 10 * 60
GLOBAL_LIMIT = 30
GLOBAL_LOCK = 10 * 60
SESSION_TTL = 12 * 60 * 60
MAX_SESSIONS = 1000
AUDIT_ROTATE_BYTES = 5 * 1024 * 1024
DEVICE_TTL = 180 * 24 * 60 * 60


class LoginLocked(PermissionError):
    def __init__(self, seconds):
        self.seconds = max(1, int(seconds))
        minutes = (self.seconds + 59) // 60
        super().__init__(f'口令错误次数过多，请 {minutes} 分钟后再试；如忘记口令，请联系本机管理员。')


class LoginGuard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.sources = {}  # source -> {'failures': n, 'until': t}
        self.recent = []   # 全局错误时间
        self.global_until = 0.0

    def check(self, source, trusted=False):
        """登录前调用；锁定中则抛出 LoginLocked。已信任设备不受全局锁定影响。"""
        now = self.clock()
        with self.lock:
            if not trusted and self.global_until > now:
                raise LoginLocked(self.global_until - now)
            entry = self.sources.get(source)
            if entry and entry['until'] > now:
                raise LoginLocked(entry['until'] - now)

    def failure(self, source, trusted=False):
        """记录一次错误，返回本轮锁定前剩余尝试次数（0 表示已锁定）。

        已信任设备只累计自己的次数，不计入全局错误窗口。"""
        now = self.clock()
        with self.lock:
            entry = self.sources.setdefault(source, {'failures': 0, 'until': 0.0})
            entry['failures'] += 1
            over = entry['failures'] - FREE_ATTEMPTS
            if over >= 0:
                entry['until'] = now + min(MAX_LOCK, BASE_LOCK * (2 ** over))
            if not trusted:
                self.recent = [t for t in self.recent if now - t < GLOBAL_WINDOW] + [now]
            if not trusted and len(self.recent) >= GLOBAL_LIMIT:
                self.global_until = now + GLOBAL_LOCK
                self.recent = []
            if len(self.sources) > 10000:
                self.sources = {k: v for k, v in self.sources.items() if v['until'] > now}
            return max(0, FREE_ATTEMPTS - entry['failures'])

    def success(self, source):
        with self.lock:
            self.sources.pop(source, None)


class Sessions:
    def __init__(self, ttl=SESSION_TTL, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}

    def create(self):
        sid = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if v > now}
            while len(self.items) >= MAX_SESSIONS:
                self.items.pop(min(self.items, key=self.items.get))
            self.items[sid] = now + self.ttl
        return sid

    def valid(self, sid):
        if not sid or not isinstance(sid, str):
            return False
        with self.lock:
            expiry = self.items.get(sid)
            if expiry is None:
                return False
            if expiry <= self.clock():
                self.items.pop(sid, None)
                return False
            return True

    def clear(self):
        with self.lock:
            self.items.clear()


class DeviceTrust:
    """签发并校验“已信任设备”标记：随机编号 + 本机密钥 HMAC，服务重启后仍有效。

    标记不含口令；只有输入过正确口令的浏览器才能拿到。删除数据目录下的
    .device_secret 即可让全部标记失效（例如更换口令后）。"""

    def __init__(self, directory, ttl=DEVICE_TTL):
        self.ttl = ttl
        path = Path(directory) / '.device_secret'
        try:
            secret = path.read_bytes()
        except FileNotFoundError:
            secret = b''
        if len(secret) < 32:
            secret = secrets.token_bytes(32)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(secret)
        self.secret = secret

    def _sign(self, device_id, token_digest):
        return hmac.new(self.secret, f'{device_id}.{token_digest}'.encode(), hashlib.sha256).hexdigest()[:32]

    @staticmethod
    def _digest(token):
        # 更换访问口令后，旧口令下签发的设备标记自动失效。
        return hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:16]

    def issue(self, token):
        device_id = secrets.token_urlsafe(16)
        return f'{device_id}.{self._sign(device_id, self._digest(token))}'

    def device(self, value, token):
        """有效时返回设备编号，否则返回 None。"""
        if not isinstance(value, str) or value.count('.') != 1:
            return None
        device_id, signature = value.split('.')
        if not device_id or not hmac.compare_digest(signature, self._sign(device_id, self._digest(token))):
            return None
        return device_id


class AuditLog:
    """一行一条 JSON；只记录时间、入口、来源地址、操作、对象标识与结果。"""

    def __init__(self, directory):
        self.path = Path(directory) / 'logs' / 'access.log'
        self.lock = threading.Lock()

    def write(self, **fields):
        record = {'time': datetime.now(timezone.utc).isoformat(timespec='seconds'), **fields}
        line = json.dumps(record, ensure_ascii=False) + '\n'
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > AUDIT_ROTATE_BYTES:
                    self.path.replace(self.path.with_suffix('.log.1'))
                with self.path.open('a', encoding='utf-8') as handle:
                    handle.write(line)
        except OSError:
            # 日志失败不能影响业务请求。
            pass
