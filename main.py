"""
main.py — PRISMA · TA-ex System
FastAPI backend menggantikan Node.js/Express
Semua endpoint kompatibel 1:1 dengan frontend index.html asli

Jalankan: uvicorn main:app --reload --port 8080
"""
import io
import json
import os
import time
import uuid
import threading
from decimal import Decimal
from datetime import datetime, date
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import migrate, query, execute, get_state, set_state
from bulk_ops import (
    bulk_replace_taex, bulk_replace_prisma, bulk_replace_pr,
    bulk_replace_po, bulk_replace_kumpulan, bulk_replace_order,
)
from header_maps import normalize_taex, normalize_sap, normalize_order

load_dotenv()

# ─── APP ────────────────────────────────────────────────────────
app = FastAPI(title="PRISMA · TA-ex System", version="2.0.0")

API_KEY = os.getenv("API_KEY", "")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN] if ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "x-api-key"],
)

# ─── DB MIGRATE ON STARTUP ──────────────────────────────────────
@app.on_event("startup")
def startup():
    migrate()
    print("🚀 PRISMA TA-ex FastAPI started")


# ─── JSON ENCODER (handle Decimal, date) ───────────────────────
class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal): return float(obj)
        if isinstance(obj, (datetime, date)): return str(obj)
        return super().default(obj)

def jsonify(data: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(data, cls=_Encoder)))


# ─── AUTH MIDDLEWARE ────────────────────────────────────────────
def check_api_key(request: Request):
    if not API_KEY:
        return
    key = request.headers.get("x-api-key") or request.query_params.get("api_key")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: API key tidak valid")


# ─── UPLOAD JOB PROGRESS ────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()

def set_job(job_id: str, pct: int, msg: str, done: bool = False, error: str = None):
    with _jobs_lock:
        _jobs[job_id] = {"pct": pct, "msg": msg, "done": done, "error": error, "ts": time.time()}

def cleanup_jobs():
    cutoff = time.time() - 600
    with _jobs_lock:
        stale = [k for k, v in _jobs.items() if v["ts"] < cutoff]
        for k in stale:
            del _jobs[k]


# ─── ROW MAPPERS ────────────────────────────────────────────────
def _n(v):
    if v is None: return None
    try: return float(v)
    except: return None

def map_taex(r):
    return {
        "ID": r["id"], "Plant": r["plant"], "Equipment": r["equipment"],
        "Order": r["order"], "Revision": r["revision"], "Reservno": r["reservno"],
        "Material": r["material"], "Itm": r["itm"],
        "Material_Description": r["material_description"],
        "Qty_Reqmts": _n(r["qty_reqmts"]), "Qty_Stock": _n(r["qty_stock"]),
        "PR": r["pr"], "Item": r["item"], "Qty_PR": _n(r["qty_pr"]),
        "Cost_Ctrs": r["cost_ctrs"],
        "PO": r["po"], "PO_Date": r["po_date"], "Qty_Deliv": _n(r["qty_deliv"]),
        "Delivery_Date": r["delivery_date"],
        "SLoc": r["sloc"], "Del": r["del"], "FIs": r["fis"],
        "Ict": r["ict"], "PG": r["pg"],
        "Recipient": r["recipient"], "Unloading_point": r["unloading_point"],
        "Reqmts_Date": r["reqmts_date"],
        "Qty_f_avail_check": _n(r["qty_f_avail_check"]),
        "Qty_Withdrawn": _n(r["qty_withdrawn"]),
        "UoM": r["uom"], "GL_Acct": r["gl_acct"],
        "Res_Price": _n(r["res_price"]), "Res_per": _n(r["res_per"]),
        "Res_Curr": r["res_curr"],
    }

def map_prisma(r):
    return {
        "ID": r["id"], "Plant": r["plant"], "Equipment": r["equipment"],
        "Revision": r["revision"], "Order": r["order"], "Reservno": r["reservno"],
        "Itm": r["itm"], "Material": r["material"],
        "Material_Description": r["material_description"],
        "Del": r["del"], "FIs": r["fis"], "Ict": r["ict"], "PG": r["pg"],
        "Recipient": r["recipient"], "Unloading_point": r["unloading_point"],
        "Reqmts_Date": r["reqmts_date"],
        "Qty_Reqmts": _n(r["qty_reqmts"]), "UoM": r["uom"],
        "PR_Prisma": r["pr_prisma"], "Item_Prisma": r["item_prisma"],
        "Qty_PR_Prisma": _n(r["qty_pr_prisma"]),
        "Qty_StockOnhand": _n(r["qty_stock_onhand"]),
        "CodeKertasKerja": r["code_kertas_kerja"],
    }

def map_kumpulan(r):
    return {
        "ID": r["id"], "Plant": r["plant"], "Equipment": r["equipment"],
        "Revision": r["revision"], "Order": r["order"], "Reservno": r["reservno"],
        "Itm": r["itm"], "Material": r["material"],
        "Material_Description": r["material_description"],
        "Qty_Req": _n(r["qty_req"]), "Qty_Stock": _n(r["qty_stock"]),
        "Qty_PR": _n(r["qty_pr"]), "Qty_To_PR": _n(r["qty_to_pr"]),
        "CodeTracking": r["code_tracking"],
    }

def map_sap(r):
    return {
        "ID": r["id"], "Plant": r["plant"],
        "PR": r["pr"], "Item": r["item"],
        "Material": r["material"], "Material_Description": r["material_description"],
        "D": r["d"], "R": r["r"], "PGr": r["pgr"], "S": r["s"],
        "TrackingNo": r["tracking_no"],
        "Qty_PR": _n(r["qty_pr"]), "Un": r["un"], "Req_Date": r["req_date"],
        "Valn_price": _n(r["valn_price"]), "PR_Curr": r["pr_curr"],
        "PR_Per": _n(r["pr_per"]), "Release_Date": r["release_date"],
        "Tracking": r["tracking"],
    }

def map_po(r):
    return {
        "ID": r["id"], "Plnt": r["plnt"],
        "Purchreq": r["purchreq"], "Item": r["item"],
        "Material": r["material"], "Short_Text": r["short_text"],
        "PO": r["po"], "PO_Item": r["po_item"],
        "D": r["d"], "DCI": r["dci"], "PGr": r["pgr"],
        "Doc_Date": r["doc_date"],
        "PO_Quantity": _n(r["po_quantity"]), "Qty_Delivered": _n(r["qty_delivered"]),
        "Deliv_Date": r["deliv_date"], "OUn": r["oun"],
        "Net_Price": _n(r["net_price"]), "Crcy": r["crcy"], "Per": _n(r["per"]),
    }

def map_order(r):
    return {
        "ID": r["id"], "Plant": r["plant"], "Order": r["order"],
        "Superior_Order": r["superior_order"], "Notification": r["notification"],
        "Created_On": r["created_on"], "Description": r["description"],
        "Revision": r["revision"], "Equipment": r["equipment"],
        "System_Status": r["system_status"], "User_Status": r["user_status"],
        "FunctLocation": r["funct_location"], "Location": r["location"],
        "WBS_Ord_header": r["wbs_ord_header"], "CostCenter": r["cost_center"],
        "Total_Plan_Cost": _n(r["total_plan_cost"]),
        "Total_Act_Cost": _n(r["total_act_cost"]),
        "Planner_Group": r["planner_group"], "MainWorkCtr": r["main_work_ctr"],
        "Entry_by": r["entry_by"], "Changed_by": r["changed_by"],
        "Basic_start_date": r["basic_start_date"],
        "Basic_finish_date": r["basic_finish_date"],
        "Actual_Release": r["actual_release"],
    }


# ═══════════════════════════════════════════════════════════════
# STATIC FILES
# ═══════════════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("public/index.html")


# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════
@app.get("/api/health")
def health():
    try:
        query("SELECT 1")
        return {"status": "ok", "db": "postgresql", "time": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# ═══════════════════════════════════════════════════════════════
# META — lightweight init (hanya COUNT + state)
# ═══════════════════════════════════════════════════════════════
@app.get("/api/meta")
def meta(request: Request):
    check_api_key(request)
    counts = query("""
        SELECT
            (SELECT COUNT(*) FROM taex_reservasi)    AS taex,
            (SELECT COUNT(*) FROM prisma_reservasi)  AS prisma,
            (SELECT COUNT(*) FROM kumpulan_summary)  AS kumpulan,
            (SELECT COUNT(*) FROM sap_pr)            AS pr,
            (SELECT COUNT(*) FROM sap_po)            AS po
    """)[0]
    kk      = get_state("kk_current")
    summary = get_state("summary_current")
    kk_ctr  = get_state("kk_counter")
    pr_ctr  = get_state("pr_counter")
    return jsonify({
        "kkData":      kk["data"] if kk else [],
        "kkCode":      kk["code"] if kk else None,
        "summaryData": summary or [],
        "kkCounter":   kk_ctr or 0,
        "prCounter":   pr_ctr or 0,
        "pagination": {
            "totalTaex":     int(counts["taex"]),
            "totalPrisma":   int(counts["prisma"]),
            "totalKumpulan": int(counts["kumpulan"]),
            "totalPR":       int(counts["pr"]),
            "totalPO":       int(counts["po"]),
        },
    })


# ═══════════════════════════════════════════════════════════════
# DATA — paginated per tabel
# ═══════════════════════════════════════════════════════════════
TABLE_CONFIG = {
    "taex": {
        "table": "taex_reservasi", "mapper": map_taex,
        "search_cols": ['material','material_description','"order"','equipment','pr','po','plant','itm','reservno','cost_ctrs'],
        "sortable": {'id','plant','equipment','"order"','revision','material','itm','qty_reqmts','qty_stock','pr','item','qty_pr','reservno','res_price'},
        "filters": {
            "pr": lambda v: ("pr = %s", v) if v else None,
            "po": lambda v: ("po IS NOT NULL AND po <> ''", None) if v=="with" else
                            ("(po IS NULL OR po = '')", None) if v=="without" else None,
        },
    },
    "prisma": {
        "table": "prisma_reservasi", "mapper": map_prisma,
        "search_cols": ['material','material_description','"order"','equipment','plant','reservno','pr_prisma'],
        "sortable": {'id','plant','equipment','"order"','material','qty_reqmts','pr_prisma','code_kertas_kerja'},
        "filters": {
            "order": lambda v: ('"order" = %s', v) if v else None,
        },
    },
    "kumpulan": {
        "table": "kumpulan_summary", "mapper": map_kumpulan,
        "search_cols": ['material','material_description','"order"','equipment','code_tracking'],
        "sortable": {'id','plant','"order"','material','qty_req','qty_stock','code_tracking'},
        "filters": {
            "code_tracking": lambda v: ("code_tracking = %s", v) if v else None,
        },
    },
    "pr": {
        "table": "sap_pr", "mapper": map_sap,
        "search_cols": ['pr','material','material_description','plant','tracking','tracking_no'],
        "sortable": {'id','plant','pr','material','qty_pr','req_date','release_date'},
        "filters": {},
    },
    "po": {
        "table": "sap_po", "mapper": map_po,
        "search_cols": ['po','purchreq','material','short_text','plnt'],
        "sortable": {'id','plnt','po','purchreq','material','po_quantity','deliv_date','doc_date'},
        "filters": {},
    },
}

@app.get("/api/data/{tabel}")
def get_data_table(tabel: str, request: Request,
                   page: int = 1, limit: int = 100,
                   q: str = "", order_by: str = "id", order_dir: str = "ASC"):
    check_api_key(request)
    cfg = TABLE_CONFIG.get(tabel)
    if not cfg:
        raise HTTPException(404, "Tabel tidak ditemukan")

    limit  = min(5000, max(1, limit))
    page   = max(1, page)
    offset = (page - 1) * limit

    conds, params = [], []
    if q:
        conds.append(f"({' OR '.join(f'{c}::text ILIKE %s' for c in cfg['search_cols'])})")
        params.extend([f"%{q}%"] * len(cfg["search_cols"]))

    for key, build in cfg["filters"].items():
        val = request.query_params.get(key)
        if not val: continue
        result = build(val)
        if not result: continue
        col_expr, col_val = result
        if col_val is not None:
            conds.append(f"{col_expr}")
            params.append(col_val)
        else:
            conds.append(col_expr)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    safe_ob  = order_by if order_by in cfg["sortable"] else "id"
    safe_dir = "DESC" if order_dir.upper() == "DESC" else "ASC"

    rows  = query(f"SELECT * FROM {cfg['table']} {where} ORDER BY {safe_ob} {safe_dir} LIMIT %s OFFSET %s",
                  params + [limit, offset])
    total = query(f"SELECT COUNT(*) AS c FROM {cfg['table']} {where}", params)[0]["c"]

    return jsonify({
        "data": [cfg["mapper"](r) for r in rows],
        "pagination": {
            "page": page, "limit": limit, "total": int(total),
            "totalPages": max(1, -(-int(total) // limit)),
            "hasMore": offset + limit < int(total),
        },
    })


# ═══════════════════════════════════════════════════════════════
# UPLOAD — server-side parse Excel, background job
# ═══════════════════════════════════════════════════════════════
@app.get("/api/upload-progress/{job_id}")
def upload_progress(job_id: str, request: Request):
    check_api_key(request)
    cleanup_jobs()
    job = _jobs.get(job_id)
    if not job:
        return {"pct": 0, "msg": "Menunggu...", "done": False}
    return jsonify(job)


@app.post("/api/upload/{upload_type}")
async def upload_excel(upload_type: str, request: Request,
                       file: UploadFile = File(...),
                       mode: Optional[str] = Form(None)):
    check_api_key(request)
    if upload_type not in ("taex","prisma","pr","po"):
        raise HTTPException(400, "Type tidak valid")

    content = await file.read()
    job_id = f"{upload_type}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    set_job(job_id, 0, "Membaca file Excel...")

    def _bg():
        try:
            set_job(job_id, 5, "Parsing Excel...")
            fname = file.filename.lower()
            buf = io.BytesIO(content)
            if fname.endswith(".csv"):
                df = pd.read_csv(buf, dtype=str, keep_default_na=False)
            else:
                df = pd.read_excel(buf, dtype=str, keep_default_na=False)

            if df.empty:
                set_job(job_id, 100, "File kosong", True, "File Excel kosong")
                return

            total = len(df)
            set_job(job_id, 10, f"Parsed {total:,} baris. Menyimpan ke database...")

            if upload_type == "taex":
                _mode = mode if mode in ("append","replace") else "replace"
                cnt = bulk_replace_taex(df, mode=_mode)
            elif upload_type == "prisma":
                cnt = bulk_replace_prisma(df)
            elif upload_type == "pr":
                cnt = bulk_replace_pr(df)
            elif upload_type == "po":
                cnt = bulk_replace_po(df)
            else:
                cnt = 0

            set_job(job_id, 100, f"✅ Selesai! {cnt:,} baris tersimpan", True)

        except Exception as e:
            set_job(job_id, 100, f"❌ {e}", True, str(e))

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    return {"jobId": job_id}


# ═══════════════════════════════════════════════════════════════
# TAEX
# ═══════════════════════════════════════════════════════════════
@app.get("/api/taex")
def get_taex(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM taex_reservasi ORDER BY id")
    return jsonify([map_taex(r) for r in rows])

@app.post("/api/taex")
async def add_taex(request: Request):
    check_api_key(request)
    r = await request.json()
    res = query(
        """INSERT INTO taex_reservasi
           (plant,equipment,"order",revision,material,itm,material_description,
            qty_reqmts,qty_stock,pr,item,qty_pr,cost_ctrs,po,po_date,qty_deliv,
            delivery_date,sloc,del,fis,ict,pg,recipient,unloading_point,reqmts_date,
            qty_f_avail_check,qty_withdrawn,uom,gl_acct,res_price,res_per,res_curr,reservno)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id""",
        (r.get("Plant"),r.get("Equipment"),r.get("Order"),r.get("Revision"),
         r.get("Material"),r.get("Itm"),r.get("Material_Description"),
         r.get("Qty_Reqmts",0),r.get("Qty_Stock",0),
         r.get("PR"),r.get("Item"),r.get("Qty_PR"),r.get("Cost_Ctrs"),
         r.get("PO"),r.get("PO_Date"),r.get("Qty_Deliv"),r.get("Delivery_Date"),
         r.get("SLoc"),r.get("Del"),r.get("FIs"),r.get("Ict"),r.get("PG"),
         r.get("Recipient"),r.get("Unloading_point"),r.get("Reqmts_Date"),
         r.get("Qty_f_avail_check"),r.get("Qty_Withdrawn"),
         r.get("UoM"),r.get("GL_Acct"),r.get("Res_Price"),r.get("Res_per"),
         r.get("Res_Curr"),r.get("Reservno"))
    )
    return {"ok": True, "id": res[0]["id"]}

@app.put("/api/taex")
async def put_taex(request: Request):
    check_api_key(request)
    rows = await request.json()
    if not isinstance(rows, list):
        raise HTTPException(400, "Body harus array")
    df = pd.DataFrame(rows)
    # rename keys back to Excel-style for bulk_replace_taex
    df = df.rename(columns={v:k for k,v in {
        "plant":"Plant","equipment":"Equipment","order":"Order","revision":"Revision",
        "material":"Material","itm":"Itm","material_description":"Material_Description",
        "qty_reqmts":"Qty_Reqmts","qty_stock":"Qty_Stock","pr":"PR","item":"Item",
        "qty_pr":"Qty_PR","cost_ctrs":"Cost_Ctrs",
    }.items()})
    bulk_replace_taex(df, mode="replace")
    return {"ok": True}

@app.post("/api/taex/replace")
async def replace_taex(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_taex(df, mode="replace")
    return {"ok": True, "count": cnt}

@app.post("/api/taex/append")
async def append_taex(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_taex(df, mode="append")
    return {"ok": True, "count": cnt}

@app.delete("/api/taex/{row_id}")
def delete_taex(row_id: int, request: Request):
    check_api_key(request)
    execute("DELETE FROM taex_reservasi WHERE id=%s", (row_id,))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# PRISMA
# ═══════════════════════════════════════════════════════════════
@app.get("/api/prisma")
def get_prisma(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM prisma_reservasi ORDER BY id")
    return jsonify([map_prisma(r) for r in rows])

@app.get("/api/prisma/meta")
def prisma_meta(request: Request):
    check_api_key(request)
    orders = query('SELECT DISTINCT "order" FROM prisma_reservasi WHERE "order" IS NOT NULL ORDER BY "order"')
    pgs    = query('SELECT DISTINCT pg FROM prisma_reservasi WHERE pg IS NOT NULL ORDER BY pg')
    return {"orders": [r["order"] for r in orders], "pgs": [r["pg"] for r in pgs]}

@app.put("/api/prisma")
async def put_prisma(request: Request):
    check_api_key(request)
    rows = await request.json()
    if not isinstance(rows, list):
        raise HTTPException(400, "Body harus array")
    from psycopg2.extras import execute_values
    from database import get_conn, release_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in rows:
                if r.get("ID"):
                    cur.execute("""UPDATE prisma_reservasi SET
                        plant=%s,equipment=%s,revision=%s,"order"=%s,reservno=%s,itm=%s,
                        material=%s,material_description=%s,del=%s,fis=%s,ict=%s,pg=%s,
                        recipient=%s,unloading_point=%s,reqmts_date=%s,qty_reqmts=%s,uom=%s,
                        pr_prisma=%s,item_prisma=%s,qty_pr_prisma=%s,qty_stock_onhand=%s,
                        code_kertas_kerja=%s,updated_at=NOW()
                        WHERE id=%s""",
                        (r.get("Plant"),r.get("Equipment"),r.get("Revision"),r.get("Order"),
                         r.get("Reservno"),r.get("Itm"),r.get("Material"),r.get("Material_Description"),
                         r.get("Del"),r.get("FIs"),r.get("Ict"),r.get("PG"),
                         r.get("Recipient"),r.get("Unloading_point"),r.get("Reqmts_Date"),
                         r.get("Qty_Reqmts",0),r.get("UoM"),
                         r.get("PR_Prisma"),r.get("Item_Prisma"),r.get("Qty_PR_Prisma"),
                         r.get("Qty_StockOnhand"),r.get("CodeKertasKerja"),r["ID"]))
                else:
                    cur.execute("""INSERT INTO prisma_reservasi
                        (plant,equipment,revision,"order",reservno,itm,material,material_description,
                         del,fis,ict,pg,recipient,unloading_point,reqmts_date,qty_reqmts,uom,
                         pr_prisma,item_prisma,qty_pr_prisma,qty_stock_onhand,code_kertas_kerja)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (r.get("Plant"),r.get("Equipment"),r.get("Revision"),r.get("Order"),
                         r.get("Reservno"),r.get("Itm"),r.get("Material"),r.get("Material_Description"),
                         r.get("Del"),r.get("FIs"),r.get("Ict"),r.get("PG"),
                         r.get("Recipient"),r.get("Unloading_point"),r.get("Reqmts_Date"),
                         r.get("Qty_Reqmts",0),r.get("UoM"),
                         r.get("PR_Prisma"),r.get("Item_Prisma"),r.get("Qty_PR_Prisma"),
                         r.get("Qty_StockOnhand"),r.get("CodeKertasKerja")))
        conn.commit()
    except Exception as e:
        conn.rollback(); raise HTTPException(500, str(e))
    finally:
        release_conn(conn)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# SINKRON TA-EX → PRISMA (server-side, lebih cepat dari client-side)
# ═══════════════════════════════════════════════════════════════
@app.post("/api/prisma/sync-from-taex")
def sync_prisma_from_taex(request: Request):
    """
    Sinkron data dari TA-ex ke PRISMA dengan aturan:
    - ICt = 'L'
    - Del bukan 'X'
    - FIs bukan 'X'
    - qty_reqmts > 0  ← baris dengan qty 0 tidak ditarik
    Hanya tambah baris baru (tidak timpa yang sudah ada).
    """
    check_api_key(request)

    # Ambil semua dari taex dengan filter server-side
    all_taex = query("""
        SELECT * FROM taex_reservasi
        WHERE UPPER(COALESCE(ict,'')) = 'L'
          AND UPPER(COALESCE(del,'')) != 'X'
          AND UPPER(COALESCE(fis,'')) != 'X'
          AND COALESCE(qty_reqmts, 0) > 0
    """)

    if not all_taex:
        return {"ok": True, "added": 0, "skipped": 0,
                "msg": "Tidak ada data TA-ex yang memenuhi syarat (ICt=L, Del≠X, FIs≠X, Qty>0)"}

    # Ambil existing prisma untuk cek duplikat
    existing = query('SELECT "order", material, itm FROM prisma_reservasi')
    exist_set = {(r["order"], r["material"], r["itm"]) for r in existing}

    new_rows = [t for t in all_taex
                if (t["order"], t["material"], t["itm"]) not in exist_set]
    skip_count = len(all_taex) - len(new_rows)

    if new_rows:
        from psycopg2.extras import execute_values
        from database import get_conn, release_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO prisma_reservasi
                    (plant, equipment, revision, "order", reservno, itm, material,
                     material_description, del, fis, ict, pg, recipient, unloading_point,
                     reqmts_date, qty_reqmts, uom)
                    VALUES %s
                """, [(
                    t["plant"], t["equipment"], t["revision"], t["order"],
                    t["reservno"], t["itm"], t["material"], t["material_description"],
                    t["del"], t["fis"], t["ict"], t["pg"], t["recipient"],
                    t["unloading_point"], t["reqmts_date"], t["qty_reqmts"], t["uom"]
                ) for t in new_rows])
            conn.commit()
        finally:
            release_conn(conn)

    total_prisma = query("SELECT COUNT(*) AS c FROM prisma_reservasi")[0]["c"]
    return {
        "ok": True,
        "added": len(new_rows),
        "skipped": skip_count,
        "total": int(total_prisma),
        "msg": f"✅ {len(new_rows):,} baris baru ditambahkan, {skip_count:,} sudah ada atau dilewati"
    }


# ═══════════════════════════════════════════════════════════════
# SINKRON PR → KUMPULAN SUMMARY (server-side, tidak return data taex)
# ═══════════════════════════════════════════════════════════════
@app.post("/api/kumpulan/sync-pr")
def sync_kumpulan_pr(request: Request):
    """
    Sinkron nomor PR dari SAP PR ke Kumpulan Summary.
    Match by: material + (tracking_no atau tracking) = code_tracking
    Return HANYA hasil sinkron — tidak return data taex agar tab TA-ex tidak terganggu.
    """
    check_api_key(request)

    kumpulan_rows = query("SELECT * FROM kumpulan_summary")
    pr_rows       = query("SELECT * FROM sap_pr")

    if not kumpulan_rows:
        return {"ok": True, "matched": 0, "msg": "Kumpulan Summary kosong"}

    from database import get_conn, release_conn
    conn = get_conn()
    matched_count = 0
    preview = []

    try:
        with conn.cursor() as cur:
            for k in kumpulan_rows:
                pr_item = next((
                    p for p in pr_rows
                    if p["material"] == k["material"]
                    and (p["tracking_no"] == k["code_tracking"]
                         or p["tracking"]  == k["code_tracking"])
                ), None)

                if not pr_item:
                    continue

                matched_count += 1
                qty_to_pr = max(0,
                    float(k["qty_req"] or 0)
                    - float(k["qty_stock"] or 0)
                    - float(pr_item["qty_pr"] or 0)
                )

                cur.execute("""
                    UPDATE kumpulan_summary
                    SET qty_pr=%s, qty_to_pr=%s, updated_at=NOW()
                    WHERE id=%s
                """, (pr_item["qty_pr"], qty_to_pr, k["id"]))

                # ── Update PRISMA: pr_prisma, item_prisma, qty_pr_prisma ──
                cur.execute("""
                    UPDATE prisma_reservasi
                    SET pr_prisma=%s, item_prisma=%s, qty_pr_prisma=%s, updated_at=NOW()
                    WHERE material=%s AND code_kertas_kerja=%s
                """, (pr_item["pr"], pr_item["item"], pr_item["qty_pr"],
                      k["material"], k["code_tracking"]))

                # ── Update TAEX: PR, Item, Qty_PR + Qty_Stock dari qty_stock_onhand prisma ──
                # qty_stock di taex diisi dari qty_stock_onhand di prisma (match by order+material+itm)
                cur.execute("""
                    UPDATE taex_reservasi t
                    SET pr       = %s,
                        item     = %s,
                        qty_pr   = %s,
                        qty_stock = COALESCE(p.qty_stock_onhand, t.qty_stock),
                        updated_at = NOW()
                    FROM prisma_reservasi p
                    WHERE t.material = p.material
                      AND t."order" = p."order"
                      AND t.itm     = p.itm
                      AND p.material = %s
                      AND p.code_kertas_kerja = %s
                """, (pr_item["pr"], pr_item["item"], pr_item["qty_pr"],
                      k["material"], k["code_tracking"]))

                preview.append({
                    "Material":  k["material"],
                    "Deskripsi": k["material_description"],
                    "PR":        pr_item["pr"],
                    "Item":      pr_item["item"],
                    "Qty_PR":    float(pr_item["qty_pr"] or 0),
                    "Tracking":  k["code_tracking"],
                })

        conn.commit()
    finally:
        release_conn(conn)

    # Return kumpulan yang terupdate — BUKAN semua data taex (tab TA-ex tidak reset)
    updated_kumpulan = query("SELECT * FROM kumpulan_summary ORDER BY id")
    return jsonify({
        "ok": True,
        "matched": matched_count,
        "preview": preview,
        "kumpulanData": [map_kumpulan(r) for r in updated_kumpulan],
        "msg": f"✅ {matched_count} material PR tersinkron — kumpulan + prisma + taex diupdate"
    })


# ═══════════════════════════════════════════════════════════════
# KUMPULAN
# ═══════════════════════════════════════════════════════════════
@app.get("/api/kumpulan")
def get_kumpulan(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM kumpulan_summary ORDER BY id")
    return jsonify([map_kumpulan(r) for r in rows])

@app.put("/api/kumpulan")
async def put_kumpulan(request: Request):
    check_api_key(request)
    rows = await request.json()
    if not isinstance(rows, list):
        raise HTTPException(400, "Body harus array")
    df = pd.DataFrame(rows)
    cnt = bulk_replace_kumpulan(df)
    return {"ok": True, "count": cnt}


# ═══════════════════════════════════════════════════════════════
# SAP PR
# ═══════════════════════════════════════════════════════════════
@app.get("/api/pr")
def get_pr(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM sap_pr ORDER BY id")
    return jsonify([map_sap(r) for r in rows])

@app.put("/api/pr")
async def put_pr(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_pr(df)
    return {"ok": True, "count": cnt}

@app.post("/api/pr/replace")
async def replace_pr(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_pr(df)
    return {"ok": True, "count": cnt}

@app.post("/api/pr/append")
async def append_pr(request: Request):
    check_api_key(request)
    rows = await request.json()
    from psycopg2.extras import execute_values
    from database import get_conn, release_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            from bulk_ops import _s, _n
            vals = []
            for r in rows:
                nr = normalize_sap(r)
                vals.append((_s(nr.get("Plant")),_s(nr.get("PR")),_s(nr.get("Item")),
                              _s(nr.get("Material")),_s(nr.get("Material_Description")),
                              _s(nr.get("D")),_s(nr.get("R")),_s(nr.get("PGr")),
                              _s(nr.get("S")),_s(nr.get("TrackingNo")),
                              _n(nr.get("Qty_PR")),_s(nr.get("Un")),_s(nr.get("Req_Date")),
                              _n(nr.get("Valn_price")),_s(nr.get("PR_Curr")),_n(nr.get("PR_Per")),
                              _s(nr.get("Release_Date")),_s(nr.get("Tracking"))))
            execute_values(cur, """INSERT INTO sap_pr
                (plant,pr,item,material,material_description,d,r,pgr,s,tracking_no,
                 qty_pr,un,req_date,valn_price,pr_curr,pr_per,release_date,tracking)
                VALUES %s""", vals)
        conn.commit()
    finally:
        release_conn(conn)
    rows_all = query("SELECT * FROM sap_pr ORDER BY id")
    return jsonify({"ok": True, "count": len(vals), "data": [map_sap(r) for r in rows_all]})


# ═══════════════════════════════════════════════════════════════
# SAP PO
# ═══════════════════════════════════════════════════════════════
@app.get("/api/po")
def get_po(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM sap_po ORDER BY id")
    return jsonify([map_po(r) for r in rows])

@app.put("/api/po")
async def put_po(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_po(df)
    return {"ok": True, "count": cnt}

@app.post("/api/po/replace")
async def replace_po(request: Request):
    check_api_key(request)
    rows = await request.json()
    df = pd.DataFrame(rows)
    cnt = bulk_replace_po(df)
    return {"ok": True, "count": cnt}

@app.post("/api/po/append")
async def append_po(request: Request):
    check_api_key(request)
    rows = await request.json()
    from psycopg2.extras import execute_values
    from database import get_conn, release_conn
    from bulk_ops import _s, _n
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            vals = [(_s(r.get("Plnt")),_s(r.get("Purchreq")),_s(r.get("Item")),
                     _s(r.get("Material")),_s(r.get("Short_Text")),
                     _s(r.get("PO")),_s(r.get("PO_Item")),
                     _s(r.get("D")),_s(r.get("DCI")),_s(r.get("PGr")),
                     _s(r.get("Doc_Date")),_n(r.get("PO_Quantity")),_n(r.get("Qty_Delivered")),
                     _s(r.get("Deliv_Date")),_s(r.get("OUn")),
                     _n(r.get("Net_Price")),_s(r.get("Crcy")),_n(r.get("Per")))
                    for r in rows]
            execute_values(cur, """INSERT INTO sap_po
                (plnt,purchreq,item,material,short_text,po,po_item,d,dci,pgr,
                 doc_date,po_quantity,qty_delivered,deliv_date,oun,net_price,crcy,per)
                VALUES %s""", vals)
        conn.commit()
    finally:
        release_conn(conn)
    rows_all = query("SELECT * FROM sap_po ORDER BY id")
    return jsonify({"ok": True, "count": len(vals), "data": [map_po(r) for r in rows_all]})


# ═══════════════════════════════════════════════════════════════
# WORK ORDER
# ═══════════════════════════════════════════════════════════════
@app.get("/api/order")
def get_order(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM work_order ORDER BY id")
    return jsonify([map_order(r) for r in rows])

@app.put("/api/order")
async def put_order(request: Request):
    check_api_key(request)
    rows = await request.json()
    if not isinstance(rows, list):
        raise HTTPException(400, "Body harus array")
    df = pd.DataFrame(rows)
    cnt = bulk_replace_order(df)
    return {"ok": True, "count": cnt}

@app.delete("/api/order/{row_id}")
def delete_order(row_id: int, request: Request):
    check_api_key(request)
    execute("DELETE FROM work_order WHERE id=%s", (row_id,))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# AUDIT — server-side JOIN taex vs prisma
# ═══════════════════════════════════════════════════════════════
AUDIT_COLS = [
    ("equipment","Equipment"), ("reservno","Reserv.No."), ("revision","Revision"),
    ("material_description","Material Description"), ("qty_reqmts","Reqmt Qty"),
    ("del","Del"), ("fis","FIs"), ("ict","ICt"), ("pg","PG"),
    ("uom","BUn"), ("recipient","Recipient"), ("unloading_point","Unloading Point"),
    ("reqmts_date","Reqmt Date"),
]

@app.get("/api/audit")
def audit(request: Request, page: int = 1, limit: int = 100,
          q: str = "", col: str = ""):
    check_api_key(request)
    limit = min(500, max(1, limit))
    offset = (page - 1) * limit

    target = [(col, next(v for k,v in AUDIT_COLS if k==col))] if col else AUDIT_COLS

    extra = ""
    if q:
        extra = f" AND (t.\"order\" ILIKE '%{q}%' OR t.material ILIKE '%{q}%' OR t.itm::text ILIKE '%{q}%')"

    unions = []
    for key, label in target:
        pv = f"COALESCE(p.{key}::text,'')"
        tv = f"COALESCE(t.{key}::text,'')"
        unions.append(f"""
            SELECT t."order" AS order_val, t.material, t.itm,
                   '{key}' AS col_key, '{label}' AS col_label,
                   {pv} AS val_prisma, {tv} AS val_taex
            FROM prisma_reservasi p
            JOIN taex_reservasi t
              ON p."order"=t."order" AND p.material=t.material AND p.itm=t.itm
            WHERE p.{key} IS DISTINCT FROM t.{key}{extra}
        """)

    if not unions:
        return jsonify({"data":[], "pagination":{"page":1,"limit":limit,"total":0,"totalPages":1}, "changedRows":0})

    full_sql = " UNION ALL ".join(unions)
    count_res = query(f"SELECT COUNT(*) AS c FROM ({full_sql}) sub")
    data_res  = query(f"SELECT * FROM ({full_sql}) sub ORDER BY order_val,material,itm,col_key LIMIT %s OFFSET %s",
                      (limit, offset))

    all_diff = " OR ".join([f"p.{k} IS DISTINCT FROM t.{k}" for k,_ in AUDIT_COLS])
    changed_res = query(f"""
        SELECT COUNT(DISTINCT (t."order",t.material,t.itm)) AS c
        FROM prisma_reservasi p
        JOIN taex_reservasi t ON p."order"=t."order" AND p.material=t.material AND p.itm=t.itm
        WHERE {all_diff}{extra}
    """)

    total = int(count_res[0]["c"])
    return jsonify({
        "data": [{"Order": r["order_val"], "Material": r["material"], "Itm": r["itm"],
                  "col_key": r["col_key"], "col_label": r["col_label"],
                  "val_prisma": r["val_prisma"] or None, "val_taex": r["val_taex"] or None}
                 for r in data_res],
        "pagination": {"page": page, "limit": limit, "total": total,
                       "totalPages": max(1, -(-total // limit))},
        "changedRows": int(changed_res[0]["c"]),
    })


# ═══════════════════════════════════════════════════════════════
# APP STATE
# ═══════════════════════════════════════════════════════════════
@app.get("/api/state/{key}")
def get_state_api(key: str, request: Request):
    check_api_key(request)
    return {"key": key, "value": get_state(key)}

@app.post("/api/state/{key}")
async def set_state_api(key: str, request: Request):
    check_api_key(request)
    body = await request.json()
    set_state(key, body.get("value"))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# BULK SAVE
# ═══════════════════════════════════════════════════════════════
@app.post("/api/save")
async def bulk_save(request: Request):
    check_api_key(request)
    body = await request.json()

    taex_d   = body.get("taexData")
    prisma_d = body.get("prismaReservasiData")
    kumpulan_d = body.get("kumpulanData")
    pr_d     = body.get("prData")
    po_d     = body.get("poData")
    kk_d     = body.get("kkData")
    kk_code  = body.get("kkCode")
    sum_d    = body.get("summaryData")
    kk_ctr   = body.get("kkCounter")
    pr_ctr   = body.get("prCounter")

    if isinstance(taex_d, list)   and taex_d:   bulk_replace_taex(pd.DataFrame(taex_d), mode="replace")
    if isinstance(prisma_d, list) and prisma_d: bulk_replace_prisma(pd.DataFrame(prisma_d))
    if isinstance(kumpulan_d, list) and kumpulan_d: bulk_replace_kumpulan(pd.DataFrame(kumpulan_d))
    if isinstance(pr_d, list)     and pr_d:     bulk_replace_pr(pd.DataFrame(pr_d))
    if isinstance(po_d, list)     and po_d:     bulk_replace_po(pd.DataFrame(po_d))

    if kk_d is not None or kk_code is not None:
        set_state("kk_current", {"data": kk_d or [], "code": kk_code or None})
    if sum_d is not None:  set_state("summary_current", sum_d or [])
    if kk_ctr is not None: set_state("kk_counter", kk_ctr)
    if pr_ctr is not None: set_state("pr_counter", pr_ctr)

    return {"ok": True, "savedAt": datetime.now().isoformat()}


# ═══════════════════════════════════════════════════════════════
# RESET ALL
# ═══════════════════════════════════════════════════════════════
@app.post("/api/reset")
def reset_all(request: Request):
    check_api_key(request)
    for tbl in ["taex_reservasi","prisma_reservasi","kumpulan_summary",
                "sap_pr","sap_po","work_order","app_state"]:
        execute(f"DELETE FROM {tbl}")
    for seq in ["taex_reservasi_id_seq","prisma_reservasi_id_seq",
                "kumpulan_summary_id_seq","sap_pr_id_seq",
                "sap_po_id_seq","work_order_id_seq"]:
        try: execute(f"ALTER SEQUENCE {seq} RESTART WITH 1")
        except: pass
    migrate()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# SPA FALLBACK
# ═══════════════════════════════════════════════════════════════
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    return FileResponse("public/index.html")