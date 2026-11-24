def test_scan_unknown_then_create_then_scan_known(client):
    r = client.get("/api/scan/076067006101")
    assert r.json() == {"code": "076067006101", "found": False, "part": None}

    r = client.post("/api/parts", json={"name": "Solenoid valve", "qty": 4, "min_qty": 2, "who": "ab",
                                        "barcodes": ["076067006101"]})
    assert r.status_code == 201, r.text
    part = r.json()
    assert part["part_no"] == "STJ-000001"

    r = client.get("/api/scan/076067006101")
    assert r.json()["found"] and r.json()["part"]["id"] == part["id"]


def test_checkout_errors_are_readable(client):
    pid = client.post("/api/parts", json={"name": "Seal kit", "qty": 1, "who": "EF"}).json()["id"]
    r = client.post(f"/api/parts/{pid}/checkout", json={"qty": 2, "who": "EF"})
    assert r.status_code == 400 and "Only 1 on hand" in r.json()["error"]
    r = client.post(f"/api/parts/{pid}/checkout", json={"qty": 1, "who": "EF", "ref": "WO 24-1190"})
    assert r.json()["qty"] == 0 and r.json()["status"] == "out"
    assert client.get("/api/activity").json()[0]["ref"] == "WO 24-1190"
    assert client.get("/api/parts/999").status_code == 404


def test_search_summary_and_barcode_svg(client):
    client.post("/api/parts", json={"name": "Quick connect air fitting", "brand": "SMC", "who": "AB"})
    assert client.get("/api/parts", params={"q": "quick"}).json()["results"][0]["brand"] == "SMC"
    assert client.get("/api/summary").json()["parts"] == 1
    assert client.get("/api/parts/facets").json()["brands"] == ["SMC"]
    r = client.get("/api/barcode/STJ-000001.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg") and b"<svg" in r.content


def test_patch_rejects_unknown_fields(client):
    pid = client.post("/api/parts", json={"name": "x", "who": "AB"}).json()["id"]
    assert client.patch(f"/api/parts/{pid}", json={"qty": 99}).status_code == 422
    assert client.patch(f"/api/parts/{pid}", json={"room": "Shop", "who": "LM"}).json()["room"] == "Shop"


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "STJ Parts" in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_parallel_requests_dont_fail(client):
    # The home screen loads several things at once; this used to return 500s.
    from concurrent.futures import ThreadPoolExecutor

    client.post("/api/parts", json={"name": "x", "who": "AB"})
    urls = ["/api/summary", "/api/activity", "/api/printer", "/api/people", "/api/parts?q=x"] * 6
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda u: client.get(u).status_code, urls))
    assert set(codes) == {200}


def test_second_start_refuses_a_different_running_copy(monkeypatch):
    # A copy from another folder is on the port: main() must warn and stop, not open its window.
    from stjparts import main as m

    opened, warned = [], []
    monkeypatch.setattr(m, "_port_in_use", lambda port: True)
    monkeypatch.setattr(m, "open_window", lambda url: opened.append(url))
    monkeypatch.setattr(m, "_warn", lambda msg: warned.append(msg))
    monkeypatch.setattr(m, "_running_copy", lambda url: {"version": "0.1.0", "code_dir": r"C:\old\stjparts"})
    import pytest
    with pytest.raises(SystemExit):
        m.main([])
    assert not opened and "0.1.0" in warned[0] and r"C:\old" in warned[0]

    # The same copy is running: just open another window.
    monkeypatch.setattr(m, "_running_copy", lambda url: {"version": m.__version__, "code_dir": m.CODE_DIR})
    m.main([])
    assert len(opened) == 1


def test_version_reports_code_folder(client):
    info = client.get("/api/version").json()
    assert info["version"] and info["code_dir"].endswith("stjparts")
