# STJ Parts Tracker: Design Spec (v0.1)

Draft 2026-11-20. Built from Controller's and the maintenance manager's decisions. Items marked *(proposed)* are design choices Claude filled in; change any of them.

**Two goals come before everything else:**
1. **Using it must be effortless for maintenance staff, or they won't use it.** Pick up the scanner, scan, and you're done.
2. **Entering parts the first time must be as fast and uniform as possible.**

---

## 1. Platform
- Runs on one shop PC as a local web app: a small Python program with a SQLite database, used through a browser window in full-screen kiosk mode. It installs as a single .exe.
- It's built server-ready from day one *(proposed)*:
  - The screens talk to the program through an API. A future phone app or server copy can use the same API.
  - Every record has a permanent unique ID.
  - "Export everything" produces one .zip with the data, photos and PDFs, which a future server instance can import.
  - SQLite can move to PostgreSQL on a server without redesign.
- The PC has internet, so phone and AI features can be added later without changing the design.

## 2. Scanning flow (the core of the app)
- A generic USB scanner acts like a keyboard. The app recognizes a scan (a fast burst of characters ending in Enter) on any screen and jumps straight to the result. No clicking into a search box first.
  - *(proposed)* Keep the app full screen on the shop PC so scans always land in it. Most scanners can also add a prefix character so the app knows for certain that input came from the scanner.
- **Unknown barcode:** shows "Unregistered barcode scanned. What would you like to do?" with two choices:
  1. Create new part (the scanned barcode is attached to the new part)
  2. Rescan barcode
- **Known barcode:** opens the part page with all its information and photos, plus three large buttons:
  1. Check in part
  2. Check out part
  3. Print new label
- Scanning an existing part's label anywhere in the app finds that part, which is the same as searching for it.

## 3. Check in / check out
- Steps: scan, tap Check Out, choose who, enter the quantity (defaults to 1), enter the work order / PO number, and confirm. That's three or four touches.
- **Who:** type your initials, or tap your name on a list of common people that the supervisor maintains. Initials stay filled in for a short time *(proposed: 10 minutes, adjustable)* so back-to-back scans don't ask again.
- Check-in uses the same screen, with the work order field optional.
- Every transaction records the time, the person, the quantity, the work order / PO, and the quantity left afterward.

## 4. Part page fields
| Field | Required? |
|---|---|
| Part number (typed in, or auto-generated as STJ-000123 when unknown) | Yes |
| Part description (e.g. "Quick connect air fitting for solenoid") | Optional. Can be made required in Settings |
| Brand / manufacturer | Optional |
| Size / specification description (e.g. "1/2 in quick connect, M10 thread") | Optional |
| Usage description (where and how it's used, e.g. "panels with 1/2 in solenoid banks, PNLxxxx") | Optional |
| Quantity on hand, min, max | Qty required; min/max optional |
| Location: Room (from a list) plus location description, with optional shelf / cabinet / drawer number | Room required *(proposed)* |
| Vendor (dropdown with search, plus "+ Add vendor") | Optional |
| Photos (any number) | Optional |
| PDF manual (opens in the PC's default PDF viewer) | Optional |
| Shelf life / expiry date and warranty date | Optional; can be hidden entirely in Settings |
| Custom fields (serial number and supplier part number to start; the supervisor can add more as text, number, date or dropdown fields) | Optional |
| Barcodes (one or more per part) | At least one |

- **Categories:** the earlier Area > System tree is replaced by the free-text Usage field, which feeds search. Area/System values from the existing spreadsheets go into Usage when imported.

## 5. Search
- One search box searches everything on the part page: part number, descriptions, brand, size/spec, usage, location, vendor, custom fields and all barcodes.
- *(proposed)* It tolerates partial words and small typos, so "1/2 quik conect smc" still finds the fitting. Results show photos so the right part is easy to recognize.

## 6. Labels (Brady BMP71)
- Prints through the BMP71's Windows driver over USB.
- Every label shows at least the barcode and the part number. Description and location are optional lines.
- A label settings screen covers width and height, font size, barcode height and which lines appear, with a live preview. Several sizes can be saved as presets, for example "small bin" and "shelf tag". The BMP71 takes media up to 2 in wide.
- Barcode type: Code 128 *(proposed)*. It's compact and every generic scanner reads it.
- **Batch printing:** print a sheet of labels for selected parts, or print a run of blank, unregistered sequential labels (see section 9).

## 7. Users and settings
- **Logged out (default):** anyone can search, view, check in, check out, and add a new part (they must enter initials).
- **Supervisor login:** required to edit or delete parts, merge duplicates, manage people, vendors, rooms and custom fields, and change settings.
- Auto-logout after a set time of inactivity, adjustable in Settings.
- **Settings (supervisor only):**
  - Make description required (on/off)
  - Hide or show each optional field: shelf life, warranty, min/max, custom fields
  - Auto-logout time
  - Initials memory time
  - Daily report on/off, time, folder and format
  - Email on/off and recipients
  - Photo size
  - Label presets
  - AI features on/off

## 8. History and reports
- **Daily check-in/out report:** generated automatically each day as .xlsx or .csv and saved to a folder you choose. Emailing it is optional (off by default) and goes through your company email account.
- **Activity log (on screen):** newest first. Each row shows the part, the action, who did it, the quantity and the quantity left. Rows are color-coded:
  - Red: none left after this checkout
  - Amber: at or below min *(proposed)*
  - Green: stock remains
- The supervisor uses the activity log to see what needs ordering, and to spot parts that were checked out and then put back without a check-in.
- **Usage report:** generated on demand only. Pick a date range and get usage by part, person and work order.
- All reports open inside the app as a sortable, filterable table, and each one includes the vendor and the vendor's contact information.
- *(proposed)* A "To order" view lists everything at or below min, grouped by vendor.

## 9. Making first-time entry fast
- **Rapid entry mode:** scan (or generate) a barcode, take a photo, fill in a few fields, then Save & Next. The cursor and defaults carry over, so a shelf of similar parts goes quickly.
- **Pre-printed labels:** print a few hundred blank sequential labels, stick them on parts and bins, then scan each one. "Unregistered barcode, create new part" registers it on the spot.
- **Part types that keep entries uniform** *(proposed)*: choosing a type such as Bearing, Motor, Fitting, Valve, Seal kit or Filter fills in a naming pattern and the right size fields. For example, a bearing becomes "Bearing, ball, 6204-2RS".
- **Copy from an existing part**, then change only what's different.
- **Existing manufacturer barcodes:** when a box already has a barcode, scan it and it's added to the part as another barcode.
- **Excel import and export** using the simple template. The 288-part starter sheet can be loaded on day one.

## 10. Duplicate parts and merging
- When a part has been entered twice, the supervisor picks two parts, compares them side by side, and chooses which profile to keep.
- Both barcodes keep working, all photos and files are combined onto the kept part, and quantities and history are combined.
- *(proposed)* When someone creates a new part, the app warns them if it looks like an existing one ("Is this the same as…?").

## 11. Vendors
- Each vendor has a profile: company name, main rep, address, phones, emails, website and notes.
- On the part page you pick a vendor from a searchable dropdown, or add a new one without leaving the page.
- Each part can have optional vendor details on their own tab: vendor part number, price, lead time and last ordered date.

## 12. Photos from a phone (no separate server)
- *(proposed)* The shop PC app can already serve web pages, so each part page shows a QR code. Scanning it with a phone on the same Wi-Fi opens an upload page, and the photos land on that part.
- This needs the phone and the PC on a network that can reach each other. Plant IT will need to confirm that.
- Fallback: a "photo inbox" folder (for example a shared OneDrive folder). Photos dropped there show up in the app to attach to a part.
- Photos are shrunk to a fixed size when uploaded (*proposed*: 1600 px on the long side), which keeps each one around 300 KB.

## 13. AI (designed in, off by default)
- The shop PC doesn't need to be powerful, because the AI would run as an online service (Claude) using the PC's internet connection. Turning it on requires company approval for part data to leave the site.
- Planned uses:
  - Read a nameplate or box photo and fill in brand, part number and size
  - Catch duplicates and inconsistent naming
  - Suggest a description and usage text
  - Answer plain-English searches ("seals for the HICON discharge pump")
  - Review the "To order" list
- Without AI, the app still does duplicate checking and typo-tolerant search on its own.

## 14. Later
- Phone app: uses the same API once a server or remote access exists.
- Server copy for multiple sites.
- Formal shelf, cabinet and drawer numbering with location barcodes.
- Linking to a CMMS or work orders.

## Not included (decided)
- Reserving parts.
- Automatic usage reports (these are on demand only).

## Open questions
1. Which BMP71 label cartridge or size do you plan to use for bins and shelves?
2. Will the shop PC and the phones be on the same Wi-Fi, and will IT allow the PC to accept connections from phones?
3. Which email system should optional reports go out through (for example Outlook / Microsoft 365)?
4. What Windows version is on the shop PC, and can it run an installed .exe?
5. Who should get supervisor logins to start?
6. What list of rooms should it start with?
