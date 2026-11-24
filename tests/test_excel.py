"""Excel import and export: reading the template, the preview, saving, and round trips."""
import io

import pytest
from openpyxl import Workbook, load_workbook

from stjparts import excel, services

TEMPLATE_HEADINGS = ["Part Name *", "Manufacturer", "Part Number *", "Description", "Area *", "System",
                     "Used On (equipment tags)", "Qty On Hand", "Min", "Max", "Bin Location", "Criticality",
                     "Vendor", "Unit Price", "Link", "Notes"]


def make_xlsx(headings, rows, title="Parts"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headings)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def template_file():
    return make_xlsx(TEMPLATE_HEADINGS, [
        ["Mechanical seal kit", "Fristam", "EXAMPLE-1234", "Seal kit", "Evap", "Infusion Heater & Hold", "CP20-0003",
         1, 1, 2, "B-03-12", "A", "Local distributor", 450, "https://www.fristam.com", "Example row, delete me"],
        ["Motor 20 HP 1770 RPM 256T", "FILL IN", "FILL IN", "Spare motor", "Mechanical", "Chillers & Glycol",
         "Glycol Return Pump", 2, 1, None, None, "B", None, None, None, "From motor list."],
        [],
        ["Bearing 6205-2RS", "SKF", "6205-2RS", None, "Dryer", None, None, 4.0, 2, 6, "Bearing rack C", None,
         "Motion", None, None, None],
        ["Bearing 6205-2RS again", "skf", "6205-2rs", None, None, None, None, 1, None, None, None, None, None, None, None, None],
        [None, "Festo", "123", None, None, None, None, 1, None, None, None, None, None, None, None, None],
        ["Bad count", None, None, None, None, None, None, -2, None, None, None, None, None, None, None, None],
    ])


def test_template_rows_are_read_and_folded():
    sheet = excel.read_parts(template_file())
    rows = {r["name"] if "name" in r else f"row{r['row']}": r for r in sheet["rows"]}
    assert sheet["sheet"] == "Parts"
    assert "Mechanical seal kit" not in rows  # the template's example row is skipped
    motor = rows["Motor 20 HP 1770 RPM 256T"]
    assert "brand" not in motor and "mfr_part_no" not in motor  # FILL IN means blank
    assert motor["usage"] == "Mechanical › Chillers & Glycol: Glycol Return Pump"
    assert motor["notes"] == "From motor list. Criticality B"
    assert motor["row"] == 3
    bearing = rows["Bearing 6205-2RS"]
    assert bearing["qty"] == "4" and bearing["usage"] == "Dryer" and bearing["location_text"] == "Bearing rack C"


def test_preview_then_import_then_reimport_skips(conn):
    rows = excel.read_parts(template_file())["rows"]
    plan = services.plan_import(conn, rows)
    by_name = {i["name"]: i for i in plan["rows"]}
    assert plan["counts"] == {"add": 2, "update": 0, "same": 0, "skip": 1, "fix": 2}
    assert by_name["Bearing 6205-2RS again"]["detail"].startswith("Same brand and part number as row 5")
    assert by_name["Bad count"]["action"] == "fix" and "negative" in by_name["Bad count"]["detail"]
    assert plan["new_vendors"] == ["Motion"]

    result = services.run_import(conn, rows, "ba")
    assert result == {"added": 2, "updated": 0, "vendors_added": 1, "left_out": 3}
    hit = services.search_parts(conn, "6205")["results"][0]
    part = services.get_part(conn, hit["id"])
    assert part["qty"] == 4 and part["vendor"]["name"] == "Motion" and part["created_by"] == "BA"
    assert services.recent_activity(conn)[0]["who"] == "BA"

    again = services.plan_import(conn, rows)["counts"]
    assert again["add"] == 0 and again["skip"] == 3  # matched by part number, and the motor by name


def test_import_needs_initials_and_is_all_or_nothing(conn, monkeypatch):
    rows = excel.read_parts(template_file())["rows"]
    with pytest.raises(services.ServiceError, match="initials"):
        services.run_import(conn, rows, "")
    real = services.create_part
    calls = []

    def fail_second(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise services.ServiceError("Disk full.")
        return real(*a, **k)

    monkeypatch.setattr(services, "create_part", fail_second)
    with pytest.raises(services.ServiceError, match=r"Row \d+: Disk full. Nothing was imported."):
        services.run_import(conn, rows, "BA")
    assert services.summary(conn)["parts"] == 0 and services.list_vendors(conn) == []


def test_export_round_trip_updates_details_but_not_counts(conn):
    services.run_import(conn, excel.read_parts(template_file())["rows"], "BA")
    data = excel.write_parts(services.export_parts(conn))
    assert services.plan_import(conn, excel.read_parts(data)["rows"])["counts"]["same"] == 2

    wb = load_workbook(io.BytesIO(data))
    ws = wb["Parts"]
    heads = [c.value for c in ws[1]]
    row = next(r for r in ws.iter_rows(min_row=2) if r[heads.index("Brand")].value == "SKF")
    row[heads.index("Room")].value = "Maintenance shop"
    row[heads.index("On hand")].value = 9
    out = io.BytesIO()
    wb.save(out)

    plan = services.plan_import(conn, excel.read_parts(out.getvalue())["rows"])
    item = next(i for i in plan["rows"] if i["action"] == "update")
    assert "room" in item["detail"] and "doesn't change counts" in item["detail"]
    services.run_import(conn, excel.read_parts(out.getvalue())["rows"], "BA")
    part = services.lookup_code(conn, item["part_no"])
    assert part["room"] == "Maintenance shop" and part["qty"] == 4


def test_not_an_excel_file_or_no_headings():
    with pytest.raises(ValueError, match="isn't an Excel"):
        excel.read_parts(b"name,qty\nbolt,3\n")
    with pytest.raises(ValueError, match="Part name"):
        excel.read_parts(make_xlsx(["Foo", "Bar"], [[1, 2]]))


def test_import_and_export_endpoints(client):
    xlsx = template_file()
    files = {"file": ("parts.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post("/api/import/preview", files=files)
    assert r.status_code == 200 and r.json()["counts"]["add"] == 2 and "data" not in r.json()["rows"][0]
    assert client.get("/api/summary").json()["parts"] == 0  # preview saves nothing
    r = client.post("/api/import", files=files, data={"who": "ba"})
    assert r.status_code == 200 and r.json()["added"] == 2
    r = client.get("/api/export.xlsx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert len(excel.read_parts(r.content)["rows"]) == 2
    r = client.post("/api/import/preview", files={"file": ("x.csv", b"a,b", "text/csv")})
    assert r.status_code == 400 and "isn't an Excel" in r.json()["error"]
