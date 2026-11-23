"""HTTP API used by the screens. Everything the UI does goes through here,
so a phone app or another tool can use the same endpoints later."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, barcodes, db, services
from .services import NotFound, ServiceError

WEB_DIR = Path(__file__).parent / "web"


class PartIn(BaseModel):
    name: str = ""
    part_no: str = ""
    brand: str = ""
    mfr_part_no: str = ""
    size_spec: str = ""
    usage: str = ""
    description: str = ""
    room: str = ""
    location_text: str = ""
    qty: int = 0
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    vendor_id: Optional[int] = None
    supplier_part_no: str = ""
    notes: str = ""
    who: str = ""
    barcodes: list[str] = Field(default_factory=list)


class PartPatch(BaseModel):
    model_config = {"extra": "forbid"}
    name: Optional[str] = None
    brand: Optional[str] = None
    mfr_part_no: Optional[str] = None
    size_spec: Optional[str] = None
    usage: Optional[str] = None
    description: Optional[str] = None
    room: Optional[str] = None
    location_text: Optional[str] = None
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    vendor_id: Optional[int] = None
    supplier_part_no: Optional[str] = None
    notes: Optional[str] = None
    who: str = ""


class MoveIn(BaseModel):
    qty: int = 1
    who: str = ""
    ref: str = ""


class AdjustIn(BaseModel):
    qty: int
    who: str = ""
    note: str = ""


class BarcodeIn(BaseModel):
    code: str
    kind: str = "other"


class MergeIn(BaseModel):
    keep_id: int
    remove_id: int
    who: str = ""


class VendorIn(BaseModel):
    name: str
    rep: str = ""
    phone: str = ""
    email: str = ""
    after_hours: str = ""
    website: str = ""
    account_no: str = ""
    lead_time: str = ""
    notes: str = ""


class PersonIn(BaseModel):
    initials: str
    name: str = ""
    role: str = ""


def create_app(db_file: Path | str | None = None) -> FastAPI:
    app = FastAPI(title="STJ Parts", version=__version__)
    conn = db.connect(db_file)
    app.state.conn = conn

    lock = threading.RLock()

    def get_conn():
        # One shared connection; requests take turns so transactions never interleave.
        # The shop PC serves one screen, so this costs nothing noticeable.
        with lock:
            yield conn

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        status = 404 if isinstance(exc, NotFound) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)

    # ------------------------------------------------ scanning and parts
    @app.get("/api/scan/{code:path}")
    def scan(code: str, c=Depends(get_conn)) -> dict[str, Any]:
        part = services.lookup_code(c, code)
        return {"code": services.normalize_code(code), "found": part is not None, "part": part}

    @app.get("/api/parts")
    def search(q: str = "", room: str = "", brand: str = "", stock: str = "", sort: str = "name",
               limit: int = 100, c=Depends(get_conn)):
        return services.search_parts(c, q, room=room, brand=brand, stock=stock, sort=sort, limit=min(limit, 500))

    @app.get("/api/parts/facets")
    def facets(c=Depends(get_conn)):
        return services.facets(c)

    @app.post("/api/parts", status_code=201)
    def create_part(body: PartIn, c=Depends(get_conn)):
        data = body.model_dump(exclude={"who", "barcodes"})
        return services.create_part(c, data, body.who, body.barcodes)

    @app.get("/api/parts/{part_id}")
    def get_part(part_id: int, c=Depends(get_conn)):
        return services.get_part(c, part_id)

    @app.patch("/api/parts/{part_id}")
    def update_part(part_id: int, body: PartPatch, c=Depends(get_conn)):
        data = body.model_dump(exclude_unset=True, exclude={"who"})
        return services.update_part(c, part_id, data, body.who)

    @app.post("/api/parts/{part_id}/checkout")
    def checkout(part_id: int, body: MoveIn, c=Depends(get_conn)):
        return services.check_out(c, part_id, body.qty, body.who, body.ref)

    @app.post("/api/parts/{part_id}/checkin")
    def checkin(part_id: int, body: MoveIn, c=Depends(get_conn)):
        return services.check_in(c, part_id, body.qty, body.who, body.ref)

    @app.post("/api/parts/{part_id}/adjust")
    def adjust(part_id: int, body: AdjustIn, c=Depends(get_conn)):
        return services.adjust_count(c, part_id, body.qty, body.who, body.note)

    @app.post("/api/parts/{part_id}/barcodes", status_code=201)
    def add_code(part_id: int, body: BarcodeIn, c=Depends(get_conn)):
        services.add_barcode(c, part_id, body.code, body.kind)
        return services.get_part(c, part_id)

    @app.post("/api/parts/merge")
    def merge(body: MergeIn, c=Depends(get_conn)):
        return services.merge_parts(c, body.keep_id, body.remove_id, body.who)

    @app.get("/api/barcode/{code:path}.svg")
    def barcode_svg(code: str, text: bool = False):
        code = services.normalize_code(code)
        if not code:
            raise HTTPException(400, "Empty code")
        return Response(barcodes.code128_svg(code, text), media_type="image/svg+xml")

    # ------------------------------------------------ activity, home, people, vendors
    @app.get("/api/activity")
    def activity(limit: int = 50, to_order: bool = False, day: str | None = None, c=Depends(get_conn)):
        return services.recent_activity(c, min(limit, 1000), to_order, day)

    @app.get("/api/summary")
    def summary(c=Depends(get_conn)):
        return services.summary(c)

    @app.get("/api/people")
    def people(c=Depends(get_conn)):
        return services.list_people(c)

    @app.post("/api/people", status_code=201)
    def add_person(body: PersonIn, c=Depends(get_conn)):
        return services.add_person(c, body.initials, body.name, body.role)

    @app.get("/api/vendors")
    def vendors(q: str = "", c=Depends(get_conn)):
        return services.list_vendors(c, q)

    @app.post("/api/vendors", status_code=201)
    def add_vendor(body: VendorIn, c=Depends(get_conn)):
        return services.create_vendor(c, body.model_dump())

    @app.get("/api/vendors/{vendor_id}")
    def vendor(vendor_id: int, c=Depends(get_conn)):
        return services.get_vendor(c, vendor_id)

    @app.get("/api/settings")
    def settings(c=Depends(get_conn)):
        return services.all_settings(c)

    @app.get("/api/version")
    def version():
        return {"version": __version__}

    # ------------------------------------------------ screens
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app
