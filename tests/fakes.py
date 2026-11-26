"""requests / socket / subprocess / time 的最小替身，供离线测试使用。"""
import json as _json

import requests


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None, json_error=False):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text if text else (_json.dumps(json_data) if json_data is not None else "")
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("响应不是合法 JSON")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


class FakeRequests:
    """按 URL 片段路由的 requests 替身；未配置的 URL 直接抛 AssertionError。"""

    def __init__(self):
        self.routes = {}
        self.calls = []
        self.exceptions = requests.exceptions

    def route(self, fragment, response):
        self.routes[fragment] = response
        return self

    def _handle(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        for fragment, response in self.routes.items():
            if fragment in url:
                result = response(self.calls[-1]) if callable(response) else response
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"未配置的请求：{method} {url}")

    def get(self, url, **kwargs):
        return self._handle("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("post", url, **kwargs)

    def head(self, url, **kwargs):
        return self._handle("head", url, **kwargs)

    def calls_to(self, fragment):
        return [call for call in self.calls if fragment in call["url"]]


class FakeSocket:
    """socket.socket 替身：按 (ip, port) 决定连接成功还是失败。"""

    def __init__(self, family, kind, plan, record, default=None):
        self.family = family
        self.kind = kind
        self.plan = plan
        self.record = record
        self.default = default
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        self.record.append(address)
        action = self.plan.get(address, self.default)
        if isinstance(action, Exception):
            raise action
        if action == "timeout":
            raise TimeoutError("模拟连接超时")
        if action is None:
            raise ConnectionRefusedError("模拟连接被拒绝")


def socket_factory(plan, record, default=None):
    def _factory(family, kind):
        return FakeSocket(family, kind, plan, record, default)
    return _factory


class FakeTime:
    """可控时钟：values 用完后按 step 递增；记录 sleep 调用。"""

    def __init__(self, values=None, step=0.001):
        self.values = list(values or [])
        self.step = step
        self.now = 0.0
        self.sleeps = []

    def time(self):
        if self.values:
            self.now = self.values.pop(0)
            return self.now
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class RecordingWorker:
    """可调用的替身：记录每次调用的参数，返回预设结果。"""

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    def with_side_effect(self, fn):
        self.result = fn
        return self
