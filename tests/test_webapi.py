"""本地网页接口：用真实的 HTTP 请求打到 127.0.0.1 上的服务。"""

import http.client
import json

import numpy as np
import pytest

from givingear import webapi
from givingear.accounts import AccountStore

PW = "correct horse"
FAST = {"n": 2**4, "r": 8, "p": 1}


@pytest.fixture
def server(tmp_path):
    store = AccountStore(tmp_path, kdf_cost=FAST)
    httpd = webapi.serve(0, store)
    yield httpd, store
    httpd.shutdown()
    httpd.server_close()


def call(httpd, method, path, body=None, token=None, headers=None):
    port = httpd.server_address[1]
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body), "application/json"
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request(method, path, data, h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, (json.loads(raw) if r.getheader("Content-Type", "").startswith("application/json") else raw)


def signed_up(httpd, name="wang"):
    status, body = call(httpd, "POST", "/api/register", {"username": name, "password": PW})
    assert status == 200, body
    return body["token"]


def test_the_page_itself_is_served(server):
    status, body = call(server[0], "GET", "/")
    assert status == 200 and b"GivingEar" in body


def test_register_login_list_rename_delete_logout(server):
    httpd, store = server
    token = signed_up(httpd)
    vault = store.open("wang", PW)
    vault.add("小王", [np.ones(192, np.float32)])
    vault.add("小李", [np.ones(192, np.float32)])
    vault.save()
    status, body = call(httpd, "GET", "/api/speakers", token=token)
    assert status == 200 and [s["name"] for s in body["speakers"]] == ["小王", "小李"]
    assert call(httpd, "POST", "/api/speakers/rename", {"old": "小李", "new": "李明"}, token)[0] == 200
    assert call(httpd, "DELETE", "/api/speakers/%E5%B0%8F%E7%8E%8B", token=token)[0] == 200  # 小王
    assert [v.name for v in store.open("wang", PW).voiceprints] == ["李明"]
    assert call(httpd, "DELETE", "/api/speakers/%E6%B2%A1%E8%BF%99%E4%B8%AA%E4%BA%BA", token=token)[0] == 404
    assert call(httpd, "POST", "/api/logout", {}, token)[0] == 200
    assert call(httpd, "GET", "/api/speakers", token=token)[0] == 401


def test_wrong_password_is_401_and_does_not_reveal_which_part_was_wrong(server):
    httpd, _ = server
    signed_up(httpd)
    s1, b1 = call(httpd, "POST", "/api/login", {"username": "wang", "password": "wrong wrong"})
    s2, b2 = call(httpd, "POST", "/api/login", {"username": "nobody", "password": "wrong wrong"})
    assert (s1, s2) == (401, 401) and b1 == b2


def test_repeated_failures_lock_the_account_for_a_while(server):
    httpd, _ = server
    signed_up(httpd)
    for _ in range(webapi.MAX_FAILURES):
        call(httpd, "POST", "/api/login", {"username": "wang", "password": "wrong wrong"})
    status, _ = call(httpd, "POST", "/api/login", {"username": "wang", "password": PW})  # 连正确密码也暂时不行
    assert status == 429


def test_nothing_works_without_logging_in(server):
    httpd, _ = server
    for method, path in [("GET", "/api/speakers"), ("GET", "/api/me"), ("DELETE", "/api/speakers/x")]:
        assert call(httpd, method, path)[0] == 401
    assert call(httpd, "GET", "/api/speakers", token="made-up-token")[0] == 401


def test_tokens_live_only_in_memory(server, tmp_path):
    httpd, _ = server
    token = signed_up(httpd)
    assert not any(token.encode() in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    httpd2 = webapi.serve(0, AccountStore(tmp_path, kdf_cost=FAST))  # 相当于服务重启
    try:
        assert call(httpd2, "GET", "/api/me", token=token)[0] == 401
    finally:
        httpd2.shutdown()
        httpd2.server_close()


def test_the_server_only_listens_on_localhost(server):
    assert server[0].server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("host", ["evil.example.com", "127.0.0.1:1"])
def test_requests_with_a_foreign_host_are_refused(server, host):
    httpd, _ = server
    c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
    c.request("GET", "/api/me", headers={"Host": host})
    assert c.getresponse().status == 403


def test_requests_from_another_website_are_refused(server):
    httpd, _ = server
    status, _ = call(httpd, "POST", "/api/login", {"username": "wang", "password": PW}, headers={"Origin": "https://evil.example.com"})
    assert status == 403


def test_posts_must_be_json(server):
    httpd, _ = server
    port = httpd.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("POST", "/api/login", "username=wang", {"Host": f"127.0.0.1:{port}", "Content-Type": "text/plain"})
    assert c.getresponse().status == 415


def test_registering_a_taken_name_or_a_weak_password_is_a_clear_400(server):
    httpd, _ = server
    signed_up(httpd)
    s, b = call(httpd, "POST", "/api/register", {"username": "wang", "password": PW})
    assert s == 400 and "已经被用" in b["error"]
    s, b = call(httpd, "POST", "/api/register", {"username": "li", "password": "short"})
    assert s == 400 and "至少" in b["error"]


def test_a_voiceprint_registered_by_a_running_session_is_not_overwritten_by_the_web(server):
    """网页和会话各持一份声纹库；网页改名之后，会话再登记的人不能丢。"""
    httpd, store = server
    token = signed_up(httpd)
    session_vault = store.open("wang", PW)
    session_vault.add("小王", [np.ones(192, np.float32)])
    session_vault.save()
    call(httpd, "POST", "/api/speakers/rename", {"old": "小王", "new": "王小明"}, token)
    session_vault.reload()
    session_vault.add("小李", [np.ones(192, np.float32)])
    session_vault.save()
    status, body = call(httpd, "GET", "/api/speakers", token=token)
    assert [s["name"] for s in body["speakers"]] == ["王小明", "小李"]
