import barcode
import pytest

from stjparts import labels, services as s
from stjparts.services import ServiceError


def bars_row(img):
    """Read the bar pattern back: bars are the tallest block of identical rows with many black/white changes."""
    w, h = img.size
    px = img.load()
    rows = ["".join("1" if px[x, y] == 0 else "0" for x in range(w)) for y in range(h)]
    best, best_len, y = "", 0, 0
    while y < h:
        n = 1
        while y + n < h and rows[y + n] == rows[y]:
            n += 1
        if n > best_len and sum(a != b for a, b in zip(rows[y], rows[y][1:])) > 20:
            best, best_len = rows[y], n
        y += n
    return best.strip("0")


def decode_modules(row):
    runs, cur, n = [], row[0], 0
    for ch in row:
        if ch == cur:
            n += 1
        else:
            runs.append((cur, n)); cur, n = ch, 1
    runs.append((cur, n))
    unit = min(n for _, n in runs)
    return "".join(c * (n // unit) for c, n in runs)


@pytest.mark.parametrize("w,h", [(2, 1), (1.5, 0.75), (2, 0.5), (2, 2)])
def test_label_size_and_barcode_is_exact(w, h):
    img = labels.render(labels.LabelContent("STJ-000214", "Solenoid valve, 5/2 single, VUVG-L14", "Shop, cabinet 3"),
                        labels.LabelSize(w, h), dpi=300)
    assert img.size == (round(w * 300), round(h * 300))
    expected = barcode.get("code128", "STJ-000214").build()[0]
    assert decode_modules(bars_row(img)) == expected.strip("0")


def test_long_code_on_tiny_label_is_refused():
    with pytest.raises(labels.LabelTooSmall):
        labels.render(labels.LabelContent("X" * 60), labels.LabelSize(1.0, 0.5))


def test_pdf_and_png():
    img = labels.render(labels.LabelContent("STJ-000001", "Bearing"), labels.LabelSize(2, 1))
    assert labels.to_png(img)[:4] == b"\x89PNG"
    pdf = labels.to_pdf([img, img])
    assert pdf[:4] == b"%PDF" and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page ") >= 2 or b"/Count 2" in pdf


def test_presets_seeded_and_editable(conn):
    names = [p["name"] for p in s.list_presets(conn)]
    assert names == ["Part label", "Small part", "Shelf strip", "Large bin"]
    p = s.save_preset(conn, {"name": "Brady 1x0.5", "width_in": 1, "height_in": 0.5, "show_name": False})
    assert p["width_in"] == 1 and p["show_name"] == 0
    with pytest.raises(ServiceError):
        s.save_preset(conn, {"name": "Part label", "width_in": 2, "height_in": 1})
    with pytest.raises(ServiceError):
        s.save_preset(conn, {"name": "Huge", "width_in": 8, "height_in": 1})


def test_print_without_printer_queues_the_job(conn):
    part = s.create_part(conn, {"name": "Seal kit", "room": "Shop", "location_text": "Rack A"}, "AB")
    preset = s.list_presets(conn)[0]["id"]
    r = s.print_label(conn, part["id"], preset, 3, True, True, "ab")
    assert r["queued"] and not r["printed"] and "Windows" in r["error"]
    jobs = s.list_print_jobs(conn)
    assert len(jobs) == 1 and jobs[0]["copies"] == 3 and jobs[0]["part_no"] == part["part_no"]
    assert s.printer_status(conn)["waiting"] == 1
    assert s.retry_print_jobs(conn)["waiting"] == 1
    s.cancel_print_job(conn, jobs[0]["id"])
    assert s.list_print_jobs(conn) == []
    with pytest.raises(ServiceError):
        s.print_label(conn, part["id"], preset, 0, True, True)


def test_label_endpoints(client):
    pid = client.post("/api/parts", json={"name": "Bearing", "who": "AB"}).json()["id"]
    preset = client.get("/api/labels/presets").json()[2]["id"]
    r = client.get(f"/api/parts/{pid}/label.png", params={"preset": preset})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    r = client.get(f"/api/parts/{pid}/label.pdf", params={"preset": preset, "copies": 2})
    assert r.headers["content-type"] == "application/pdf" and "STJ-000001" in r.headers["content-disposition"]
    r = client.post(f"/api/parts/{pid}/print", json={"preset_id": preset, "copies": 1})
    assert r.status_code == 200 and r.json()["queued"]
    assert client.get("/api/printer").json()["waiting"] == 1
    job = client.get("/api/print-jobs").json()[0]["id"]
    assert client.post(f"/api/print-jobs/{job}/cancel").status_code == 200
    assert client.get("/api/print-jobs").json() == []
