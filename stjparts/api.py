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

from . import __version__, barcodes, db, labels, services
from .services import NotFound, ServiceError

WEB_DIR = Path(__file__).parent / "web"


class PartIn(BaseModel):
    """Request body: Fields the Add part screen sends. Only name is required; who is the person's initials and barcodes lists extra codes."""
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
    """Request body: Fields that can be changed on an existing part. Anything else (like qty) is refused."""
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
    """Request body: A check out or check in: how many, who, and the work order or PO number."""
    qty: int = 1
    who: str = ""
    ref: str = ""


class AdjustIn(BaseModel):
    """Request body: A count correction: the real count, who counted, and why."""
    qty: int
    who: str = ""
    note: str = ""


class BarcodeIn(BaseModel):
    """Request body: An extra barcode for a part, and what kind it is."""
    code: str
    kind: str = "other"


class MergeIn(BaseModel):
    """Request body: Which part to keep and which duplicate to fold into it."""
    keep_id: int
    remove_id: int
    who: str = ""


class VendorIn(BaseModel):
    """Request body: A vendor's details. Only the name is required."""
    name: str
    rep: str = ""
    phone: str = ""
    email: str = ""
    after_hours: str = ""
    website: str = ""
    account_no: str = ""
    lead_time: str = ""
    notes: str = ""


class PresetIn(BaseModel):
    """Request body: A label size: name, width and height in inches, and whether name/location print by default."""
    name: str
    description: str = ""
    width_in: float
    height_in: float
    show_name: bool = True
    show_location: bool = True
    sort: int = 0


class PrintIn(BaseModel):
    """Request body: A print request: label size, copies, whether to show name and location, and who printed."""
    preset_id: int
    copies: int = 1
    show_name: bool = True
    show_location: bool = True
    who: str = ""


class PrinterIn(BaseModel):
    """Request body: The Windows printer name to use for labels."""
    name: str


class PersonIn(BaseModel):
    """Request body: A person for the check-out list: initials (1-4 letters), name and role."""
    initials: str
    name: str = ""
    role: str = ""


def create_app(db_file: Path | str | None = None) -> FastAPI:
    """
    Description: Builds the web app: opens the database, sets up every /api address the screens
        use, turns rule errors into readable messages, and serves the screens from the web folder.
        For example, main() calls create_app() at startup, and each test builds its own with a
        temporary database.
    Parameters:
        1: db_file - [Path, str or None] Database file to use; None means the real one.
    Returns: [FastAPI] The app, ready for the server (uvicorn) to run.
    """
    app = FastAPI(title="STJ Parts", version=__version__)
    conn = db.connect(db_file)
    app.state.conn = conn

    lock = threading.Lock()  # a plain Lock: FastAPI may release it from a different thread

    def get_conn():
        """
        Description: Hands the shared database connection to each request, one request at a time,
            so two changes can never interleave. The shop PC serves one screen, so waiting is
            never noticeable. A plain Lock is used because FastAPI may finish a request on a
            different thread than it started on.
        Parameters: None.
        Returns: [sqlite3.Connection] Yields the connection for the length of one request.
        """
        # One shared connection; requests take turns so transactions never interleave.
        # The shop PC serves one screen, so this costs nothing noticeable.
        with lock:
            yield conn

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        """
        Description: Turns a rule error from services.py into an answer the screens can show:
            HTTP 404 for "doesn't exist", HTTP 400 for everything else, with the plain message
            in "error". For example, "Only 1 on hand..." appears on the check-out screen this way.
        Parameters:
            1: _ - [Request] The request that failed (not used).
            2: exc - [ServiceError] The error raised by the rule.
        Returns: [JSONResponse] {"error": message} with the right status code.
        """
        status = 404 if isinstance(exc, NotFound) else 400
        return JSONResponse({"error": str(exc)}, status_code=status)

    # ======================================================================================
    # SCANNING AND PARTS
    # Scan lookups, the parts library search, adding/editing parts and stock moves.
    # ======================================================================================
    @app.get("/api/scan/{code:path}")
    def scan(code: str, c=Depends(get_conn)) -> dict[str, Any]:
        """
        Description: GET /api/scan/CODE. Looks up a scanned barcode.
            The home screen calls this on every scan to decide between the part page and the
            "Unregistered barcode" screen.
        Parameters:
            1: code - [str] The barcode from the address (may contain slashes).
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "code" (cleaned), "found" (bool) and "part" (the part or None).
        """
        part = services.lookup_code(c, code)
        return {"code": services.normalize_code(code), "found": part is not None, "part": part}

    @app.get("/api/parts")
    def search(q: str = "", room: str = "", brand: str = "", stock: str = "", sort: str = "name",
               limit: int = 100, c=Depends(get_conn)):
        """
        Description: GET /api/parts. The parts library search, with filters and sorting.
        Parameters:
            1: q - [str] Search text or a scanned code.
            2: room - [str] Room filter.  3: brand - [str] Brand filter.
            4: stock - [str] "out", "low", "ok" or "".
            5: sort - [str] "name", "stock", "used" or "added".
            6: limit - [int] Most rows (capped at 500).
            7: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] See services.search_parts().
        """
        return services.search_parts(c, q, room=room, brand=brand, stock=stock, sort=sort, limit=min(limit, 500))

    @app.get("/api/parts/facets")
    def facets(c=Depends(get_conn)):
        """
        Description: GET /api/parts/facets. Rooms and brands in use, for dropdowns and suggestions.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "rooms" and "brands".
        """
        return services.facets(c)

    @app.post("/api/parts", status_code=201)
    def create_part(body: PartIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts. Saves a new part from the Add part screen.
        Parameters:
            1: body - [PartIn] The part's fields, plus "who" (initials) and "barcodes" (extra codes).
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The new part (HTTP 201).
        """
        data = body.model_dump(exclude={"who", "barcodes"})
        return services.create_part(c, data, body.who, body.barcodes)

    @app.get("/api/parts/{part_id}")
    def get_part(part_id: int, c=Depends(get_conn)):
        """
        Description: GET /api/parts/ID. Everything the part page shows.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] See services.get_part(). HTTP 404 if the part doesn't exist.
        """
        return services.get_part(c, part_id)

    @app.patch("/api/parts/{part_id}")
    def update_part(part_id: int, body: PartPatch, c=Depends(get_conn)):
        """
        Description: PATCH /api/parts/ID. Changes some of a part's details. Quantity can't be
            changed here (use check out, check in or adjust), so unknown fields are refused.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [PartPatch] Only the fields to change, plus "who".
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The updated part.
        """
        data = body.model_dump(exclude_unset=True, exclude={"who"})
        return services.update_part(c, part_id, data, body.who)

    @app.post("/api/parts/{part_id}/checkout")
    def checkout(part_id: int, body: MoveIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/ID/checkout. Takes parts out of stock.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [MoveIn] "qty", "who" (initials) and "ref" (work order).
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The part after the change. HTTP 400 with a message if not enough on hand.
        """
        return services.check_out(c, part_id, body.qty, body.who, body.ref)

    @app.post("/api/parts/{part_id}/checkin")
    def checkin(part_id: int, body: MoveIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/ID/checkin. Adds parts back into stock.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [MoveIn] "qty", "who" (initials) and "ref" (PO number).
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The part after the change.
        """
        return services.check_in(c, part_id, body.qty, body.who, body.ref)

    @app.post("/api/parts/{part_id}/adjust")
    def adjust(part_id: int, body: AdjustIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/ID/adjust. Sets the on-hand count after a physical count.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [AdjustIn] "qty" (the real count), "who" and "note".
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The part after the change.
        """
        return services.adjust_count(c, part_id, body.qty, body.who, body.note)

    @app.post("/api/parts/{part_id}/barcodes", status_code=201)
    def add_code(part_id: int, body: BarcodeIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/ID/barcodes. Attaches another barcode to a part.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [BarcodeIn] "code" and "kind" ("mfr", "internal" or "other").
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The part with its updated barcode list (HTTP 201).
        """
        services.add_barcode(c, part_id, body.code, body.kind)
        return services.get_part(c, part_id)

    @app.post("/api/parts/merge")
    def merge(body: MergeIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/merge. Folds a duplicate part into the one to keep.
        Parameters:
            1: body - [MergeIn] "keep_id", "remove_id" and "who".
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The kept part.
        """
        return services.merge_parts(c, body.keep_id, body.remove_id, body.who)

    @app.get("/api/barcode/{code:path}.svg")
    def barcode_svg(code: str, text: bool = False):
        """
        Description: GET /api/barcode/CODE.svg. A barcode picture for the screen.
        Parameters:
            1: code - [str] The text to encode.
            2: text - [bool] Also show the code as text under the bars.
        Returns: [Response] An SVG image.
        """
        code = services.normalize_code(code)
        if not code:
            raise HTTPException(400, "Empty code")
        return Response(barcodes.code128_svg(code, text), media_type="image/svg+xml")

    # ======================================================================================
    # ACTIVITY, HOME, PEOPLE, VENDORS
    # Data for the home screen, the activity log, the check-out people list and vendors.
    # ======================================================================================
    @app.get("/api/activity")
    def activity(limit: int = 50, to_order: bool = False, day: str | None = None, c=Depends(get_conn)):
        """
        Description: GET /api/activity. Activity log lines, newest first.
        Parameters:
            1: limit - [int] Most lines (capped at 1000).
            2: to_order - [bool] Only lines that left a part out or low.
            3: day - [str or None] Only this date, "YYYY-MM-DD".
            4: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [list of dict] See services.recent_activity().
        """
        return services.recent_activity(c, min(limit, 1000), to_order, day)

    @app.get("/api/summary")
    def summary(c=Depends(get_conn)):
        """
        Description: GET /api/summary. Counts for the home screen tiles.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "parts", "out", "low", "vendors".
        """
        return services.summary(c)

    @app.get("/api/people")
    def people(c=Depends(get_conn)):
        """
        Description: GET /api/people. People shown as buttons on the check-out screen.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [list of dict] Active people.
        """
        return services.list_people(c)

    @app.post("/api/people", status_code=201)
    def add_person(body: PersonIn, c=Depends(get_conn)):
        """
        Description: POST /api/people. Adds or updates a person on the check-out list.
        Parameters:
            1: body - [PersonIn] "initials", "name" and "role".
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The saved person (HTTP 201).
        """
        return services.add_person(c, body.initials, body.name, body.role)

    @app.get("/api/vendors")
    def vendors(q: str = "", c=Depends(get_conn)):
        """
        Description: GET /api/vendors. The vendor list, optionally searched.
        Parameters:
            1: q - [str] Text to look for in name, rep or phone.
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [list of dict] Vendors with part counts.
        """
        return services.list_vendors(c, q)

    @app.post("/api/vendors", status_code=201)
    def add_vendor(body: VendorIn, c=Depends(get_conn)):
        """
        Description: POST /api/vendors. Adds a vendor.
        Parameters:
            1: body - [VendorIn] The vendor's details; "name" is required.
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The saved vendor (HTTP 201).
        """
        return services.create_vendor(c, body.model_dump())

    @app.get("/api/vendors/{vendor_id}")
    def vendor(vendor_id: int, c=Depends(get_conn)):
        """
        Description: GET /api/vendors/ID. One vendor and the parts bought from them.
        Parameters:
            1: vendor_id - [int] The vendor's database ID.
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] See services.get_vendor().
        """
        return services.get_vendor(c, vendor_id)

    # ======================================================================================
    # LABELS AND PRINTING
    # Label sizes, previews, PDF downloads, printing and the print queue.
    # ======================================================================================
    @app.get("/api/labels/presets")
    def presets(c=Depends(get_conn)):
        """
        Description: GET /api/labels/presets. The label sizes for the print screen.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [list of dict] Label sizes in display order.
        """
        return services.list_presets(c)

    @app.post("/api/labels/presets", status_code=201)
    def add_preset(body: PresetIn, c=Depends(get_conn)):
        """
        Description: POST /api/labels/presets. Adds a label size.
        Parameters:
            1: body - [PresetIn] Name, width and height in inches, and default name/location.
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The saved size (HTTP 201).
        """
        return services.save_preset(c, body.model_dump())

    @app.put("/api/labels/presets/{preset_id}")
    def edit_preset(preset_id: int, body: PresetIn, c=Depends(get_conn)):
        """
        Description: PUT /api/labels/presets/ID. Changes a label size.
        Parameters:
            1: preset_id - [int] The label size's ID.
            2: body - [PresetIn] The new values.
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The saved size.
        """
        return services.save_preset(c, body.model_dump(), preset_id)

    @app.get("/api/parts/{part_id}/label.png")
    def label_png(part_id: int, preset: int, name: Optional[bool] = None, loc: Optional[bool] = None, c=Depends(get_conn)):
        """
        Description: GET /api/parts/ID/label.png. The label preview image on the print screen.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: preset - [int] Label size ID.
            3: name - [bool or None] Show the part name (None = the size's default).
            4: loc - [bool or None] Show the location (None = the size's default).
            5: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [Response] A PNG image. HTTP 400 with a message if the barcode won't fit.
        """
        img = services.label_image(c, part_id, preset, name, loc)
        return Response(labels.to_png(img), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/parts/{part_id}/label.pdf")
    def label_pdf(part_id: int, preset: int, copies: int = 1, name: Optional[bool] = None, loc: Optional[bool] = None,
                  c=Depends(get_conn)):
        """
        Description: GET /api/parts/ID/label.pdf. Labels as a PDF download, one per page.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: preset - [int] Label size ID.
            3: copies - [int] Number of pages (1 to 200).
            4: name - [bool or None] Show the part name.
            5: loc - [bool or None] Show the location.
            6: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [Response] A PDF file named label-PARTNO.pdf.
        """
        img = services.label_image(c, part_id, preset, name, loc)
        pdf = labels.to_pdf([img] * max(1, min(copies, 200)), int(services.get_setting(c, "label_dpi")))
        part_no = services.get_part(c, part_id)["part_no"]
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="label-{part_no}.pdf"'})

    @app.post("/api/parts/{part_id}/print")
    def print_label(part_id: int, body: PrintIn, c=Depends(get_conn)):
        """
        Description: POST /api/parts/ID/print. Prints labels now, or queues them if the printer
            isn't available.
        Parameters:
            1: part_id - [int] The part's database ID (from the web address).
            2: body - [PrintIn] "preset_id", "copies", "show_name", "show_location", "who".
            3: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "printed", "queued", "error", "job_id".
        """
        return services.print_label(c, part_id, body.preset_id, body.copies, body.show_name, body.show_location, body.who)

    @app.get("/api/printer")
    def printer(c=Depends(get_conn)):
        """
        Description: GET /api/printer. Printer status for the print screen and home screen.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] See services.printer_status().
        """
        return services.printer_status(c)

    @app.put("/api/printer")
    def choose_printer(body: PrinterIn, c=Depends(get_conn)):
        """
        Description: PUT /api/printer. Saves which Windows printer to use for labels.
        Parameters:
            1: body - [PrinterIn] "name" (blank = pick the Brady automatically).
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] The updated printer status.
        """
        services.set_label_printer(c, body.name)
        return services.printer_status(c)

    @app.get("/api/print-jobs")
    def print_jobs(status: str = "waiting", c=Depends(get_conn)):
        """
        Description: GET /api/print-jobs. Print jobs in one state (the "Waiting to print" list).
        Parameters:
            1: status - [str] "waiting", "printed" or "cancelled".
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [list of dict] The jobs.
        """
        return services.list_print_jobs(c, status)

    @app.post("/api/print-jobs/retry")
    def retry_jobs(c=Depends(get_conn)):
        """
        Description: POST /api/print-jobs/retry. Tries to print every waiting job ("Print now").
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "printed", "waiting", "error".
        """
        return services.retry_print_jobs(c)

    @app.post("/api/print-jobs/{job_id}/cancel")
    def cancel_job(job_id: int, c=Depends(get_conn)):
        """
        Description: POST /api/print-jobs/ID/cancel. Removes a waiting job from the queue.
        Parameters:
            1: job_id - [int] The print job's ID.
            2: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] "cancelled" with the job ID.
        """
        services.cancel_print_job(c, job_id)
        return {"cancelled": job_id}

    @app.get("/api/settings")
    def settings(c=Depends(get_conn)):
        """
        Description: GET /api/settings. Every setting with defaults filled in.
        Parameters:
            1: c - [sqlite3.Connection] The database, supplied automatically by get_conn.
        Returns: [dict] Setting name -> value.
        """
        return services.all_settings(c)

    @app.get("/api/version")
    def version():
        """
        Description: GET /api/version. The app's version number (used by the build check).
        Parameters: None.
        Returns: [dict] "version", e.g. "0.2.0".
        """
        return {"version": __version__}

    # ======================================================================================
    # SCREENS
    # Serves index.html and the files in the web folder (styles, scripts).
    # ======================================================================================
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        """
        Description: GET /. Sends the main page (index.html); the screens load from there.
        Parameters: None.
        Returns: [FileResponse] The HTML page.
        """
        return FileResponse(WEB_DIR / "index.html")

    return app
