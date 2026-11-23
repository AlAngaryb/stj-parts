import pytest

from stjparts import db, services as s
from stjparts.services import ServiceError


def make(conn, name="Bearing, ball, 6204-2RS", **kw):
    data = {"name": name, "qty": 2, "min_qty": 1, **kw}
    codes = data.pop("codes", ())
    return s.create_part(conn, data, "ab", codes)


def test_migrations_are_idempotent(tmp_path):
    c = db.connect(tmp_path / "x.db")
    c.close()
    c = db.connect(tmp_path / "x.db")
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_part_numbers_count_up(conn):
    a = make(conn)
    b = make(conn, "Bearing, ball, 6205-2RS")
    assert (a["part_no"], b["part_no"]) == ("STJ-000001", "STJ-000002")
    assert a["created_by"] == "AB"


def test_custom_part_number_is_kept_and_next_skips_past_it(conn):
    make(conn, part_no="STJ-000010")
    assert make(conn, "Other")["part_no"] == "STJ-000011"


def test_scan_finds_part_by_any_code_and_strips_scanner_suffix(conn):
    p = make(conn, codes=["076067006101"])
    assert s.lookup_code(conn, "076067006101\r\n")["id"] == p["id"]
    assert s.lookup_code(conn, " stj-000001\t")["id"] == p["id"]
    assert s.lookup_code(conn, "nope") is None


def test_duplicate_barcode_rejected(conn):
    make(conn, codes=["123"])
    with pytest.raises(ServiceError, match="already belongs"):
        make(conn, "Other", codes=["123"])


def test_name_and_initials_required(conn):
    with pytest.raises(ServiceError, match="name"):
        s.create_part(conn, {"name": " "}, "AB")
    with pytest.raises(ServiceError, match="initials"):
        s.create_part(conn, {"name": "x"}, "")


def test_description_setting(conn):
    s.set_setting(conn, "description_required", "1")
    with pytest.raises(ServiceError, match="description"):
        make(conn)
    assert make(conn, description="for the HPP")["description"] == "for the HPP"


def test_checkout_and_checkin_log_activity(conn):
    p = make(conn, qty=4)
    p = s.check_out(conn, p["id"], 3, "cd", "WO 24-1192")
    assert p["qty"] == 1 and p["status"] == "low"
    p = s.check_out(conn, p["id"], 1, "cd")
    assert p["status"] == "out"
    p = s.check_in(conn, p["id"], 5, "jk", "PO 88240")
    assert p["qty"] == 5
    acts = s.recent_activity(conn)
    assert [a["action"] for a in acts] == ["in", "out", "out", "added"]
    assert acts[2]["ref"] == "WO 24-1192" and acts[2]["who"] == "CD"


def test_cannot_take_more_than_on_hand(conn):
    p = make(conn, qty=1)
    with pytest.raises(ServiceError, match="Only 1 on hand"):
        s.check_out(conn, p["id"], 2, "AB")
    assert s.get_part(conn, p["id"])["qty"] == 1


def test_checkout_needs_who_and_positive_qty(conn):
    p = make(conn)
    with pytest.raises(ServiceError):
        s.check_out(conn, p["id"], 1, "")
    with pytest.raises(ServiceError):
        s.check_out(conn, p["id"], 0, "AB")
    with pytest.raises(ServiceError):
        s.check_out(conn, p["id"], -1, "AB")


def test_to_order_filter(conn):
    a = make(conn, qty=3, min_qty=1)
    make(conn, "Seal kit", qty=5, min_qty=1)
    s.check_out(conn, a["id"], 2, "AB")
    rows = s.recent_activity(conn, to_order_only=True)
    assert [r["part_id"] for r in rows] == [a["id"]]


def test_search_any_field_prefix_and_typos(conn):
    v = s.create_vendor(conn, {"name": "Festo", "phone": "555-0100"})
    make(conn, "Solenoid valve, 5/2 single", brand="Festo", mfr_part_no="VUVG-L14", vendor_id=v["id"],
         usage="Palletizer slipsheet dispenser", room="Maintenance shop")
    make(conn, "Quick connect air fitting", brand="SMC", size_spec="1/2 inch m10")
    assert [r["name"] for r in s.search_parts(conn, "solen")["results"]] == ["Solenoid valve, 5/2 single"]
    assert len(s.search_parts(conn, "festo palletizer")["results"]) == 1
    assert len(s.search_parts(conn, "festo smc")["results"]) == 0
    res = s.search_parts(conn, "quik conect")
    assert res["did_you_mean"] == "quick connect" and len(res["results"]) == 1
    assert s.search_parts(conn, "STJ-000002")["exact"]["name"] == "Quick connect air fitting"


def test_search_filters_and_sorts(conn):
    a = make(conn, "A part", qty=0, room="Shop")
    make(conn, "B part", qty=1, min_qty=2, room="Shop")
    make(conn, "C part", qty=9, min_qty=2, room="Dryer")
    names = lambda **kw: [r["name"] for r in s.search_parts(conn, **kw)["results"]]
    assert names(stock="out") == ["A part"]
    assert names(stock="low") == ["B part"]
    assert names(stock="ok") == ["C part"]
    assert names(room="shop") == ["A part", "B part"]
    assert names(sort="stock") == ["A part", "B part", "C part"]
    assert names(sort="added")[0] == "C part"
    assert a["status"] == "out"


def test_edit_reindexes_search(conn):
    p = make(conn, "Old name")
    s.update_part(conn, p["id"], {"name": "Gearbox SEW R47"}, "LM")
    assert s.search_parts(conn, "gearbox")["results"][0]["id"] == p["id"]
    assert not s.search_parts(conn, "old")["results"]


def test_merge_keeps_both_labels_working(conn):
    keep = make(conn, qty=2, codes=["111"])
    dup = make(conn, "Bearing 6204 dup", qty=3, codes=["222"])
    merged = s.merge_parts(conn, keep["id"], dup["id"], "LM")
    assert merged["qty"] == 5
    assert s.lookup_code(conn, "222")["id"] == keep["id"]
    assert s.lookup_code(conn, dup["part_no"])["id"] == keep["id"]
    assert len(s.search_parts(conn, "bearing")["results"]) == 1
    with pytest.raises(ServiceError):
        s.check_out(conn, dup["id"], 1, "AB")


def test_vendors_and_people(conn):
    v = s.create_vendor(conn, {"name": "Festo", "rep": "Pat"})
    make(conn, vendor_id=v["id"])
    with pytest.raises(ServiceError):
        s.create_vendor(conn, {"name": "festo"})
    assert s.list_vendors(conn, "pat")[0]["part_count"] == 1
    assert s.get_vendor(conn, v["id"])["parts"][0]["name"].startswith("Bearing")
    s.add_person(conn, "ab", "Alex", "Day shift")
    assert s.list_people(conn)[0]["initials"] == "AB"
    with pytest.raises(ServiceError):
        s.add_person(conn, "a1")


def test_summary(conn):
    make(conn, qty=0)
    make(conn, "x", qty=1, min_qty=1)
    make(conn, "y", qty=5, min_qty=1)
    assert s.summary(conn) == {"parts": 3, "out": 1, "low": 1, "vendors": 0}
