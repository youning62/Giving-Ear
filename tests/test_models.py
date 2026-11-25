"""模型文件下载：镜像前缀、失败时的提示、不留下半截文件。"""

import urllib.error

import pytest

from givingear import models


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("GIVINGEAR_DOWNLOAD_MIRROR", raising=False)


def test_a_mirror_prefix_is_added_to_the_address(monkeypatch):
    url = "https://github.com/k2-fsa/x.onnx"
    assert models.download_url(url) == url
    monkeypatch.setenv("GIVINGEAR_DOWNLOAD_MIRROR", "https://mirror.example.com/")
    assert models.download_url(url) == "https://mirror.example.com/" + url


def test_download_uses_the_mirror(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("GIVINGEAR_DOWNLOAD_MIRROR", "https://m.example.com")
    monkeypatch.setattr(models.urllib.request, "urlretrieve", lambda u, d: (seen.append(u), open(d, "wb").write(b"x")))
    path = models.ensure(("a.onnx", "https://github.com/a.onnx"))
    assert seen == ["https://m.example.com/https://github.com/a.onnx"] and path.read_bytes() == b"x"


def test_a_failed_download_explains_what_to_do_and_leaves_no_partial_file(monkeypatch, tmp_path):
    def broken(url, dest):
        open(dest, "wb").write(b"half")
        raise urllib.error.URLError("连接超时")

    monkeypatch.setattr(models.urllib.request, "urlretrieve", broken)
    with pytest.raises(models.ModelDownloadError) as e:
        models.ensure(("a.onnx", "https://github.com/a.onnx"))
    msg = str(e.value)
    assert "a.onnx" in msg and "https://github.com/a.onnx" in msg and str(tmp_path / "models") in msg
    assert "GIVINGEAR_DOWNLOAD_MIRROR" in msg and "连接超时" in msg
    assert list((tmp_path / "models").iterdir()) == []  # 半截文件被删掉了


def test_a_model_already_on_disk_is_used_without_any_download(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.onnx").write_bytes(b"manual")
    monkeypatch.setattr(models.urllib.request, "urlretrieve", lambda *a: pytest.fail("不该下载"))
    assert models.ensure(("a.onnx", "https://github.com/a.onnx")).read_bytes() == b"manual"
