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
    bulk_replace_project, bulk_replace_job_list,
    bulk_replace_job_detail, bulk_replace_job_detail_work_order,
    bulk_replace_equipment_taex,
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
    if upload_type not in ("taex","prisma","pr","po","project","joblist","jobdetail","jobdetailworkorder","equipment"):
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
            elif upload_type == "project":
                cnt = bulk_replace_project(df)
            elif upload_type == "joblist":
                cnt = bulk_replace_job_list(df)
            elif upload_type == "jobdetail":
                cnt = bulk_replace_job_detail(df)
            elif upload_type == "jobdetailworkorder":
                cnt = bulk_replace_job_detail_work_order(df)
            elif upload_type == "equipment":
                cnt = bulk_replace_equipment_taex(df)
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
# TRACKING VIEW — berbasis taex_reservasi sebagai sumber utama
# Detail gabungan: taex + sap_pr + sap_po + kumpulan_summary
# Qty PR, Stock, PO semuanya diambil dari taex (bukan dari sap_pr langsung)
# karena taex sudah merupakan gabungan dari beberapa material/reservasi
# ═══════════════════════════════════════════════════════════════
@app.get("/api/tracking")
def get_tracking(
    request: Request,
    page: int = 1, limit: int = 100,
    q: str = "",
    order_val: str = "",
    material: str = "",
    pr: str = "",
    po: str = "",
    status: str = "",   # "with_pr", "without_pr", "with_po", "without_po"
    order_by: str = "t.id", order_dir: str = "ASC",
):
    """
    Tracking view berbasis taex_reservasi.

    Sumber kebenaran data:
    - Qty_Reqmts, Qty_Stock, Qty_PR, Qty_Deliv  → dari taex_reservasi (sudah terupdate via sync-pr)
    - PR, PO, PO_Date, Delivery_Date            → dari taex_reservasi
    - Tracking, TrackingNo, Valn_price          → join sap_pr (match by taex.pr = sap_pr.pr AND taex.material = sap_pr.material)
    - PO detail (Doc_Date, Net_Price, Crcy)     → join sap_po (match by taex.po = sap_po.po AND taex.material = sap_po.material)
    - CodeTracking (kumpulan)                    → join kumpulan_summary (match by taex.material + taex.order)

    Dengan demikian Qty_PR di tracking = Qty_PR di taex (bukan SUM dari sap_pr),
    karena taex sudah merupakan breakdown per reservasi/material.
    """
    check_api_key(request)
    limit  = min(5000, max(1, limit))
    offset = (page - 1) * limit

    conds, params = [], []

    if q:
        conds.append("""(
            t.material          ILIKE %s OR
            t.material_description ILIKE %s OR
            t."order"           ILIKE %s OR
            t.equipment         ILIKE %s OR
            t.pr                ILIKE %s OR
            t.po                ILIKE %s OR
            t.reservno          ILIKE %s OR
            COALESCE(sp.tracking,'')    ILIKE %s OR
            COALESCE(sp.tracking_no,'') ILIKE %s OR
            COALESCE(k.code_tracking,'') ILIKE %s
        )""")
        params.extend([f"%{q}%"] * 10)

    if order_val:
        conds.append('t."order" ILIKE %s'); params.append(f"%{order_val}%")
    if material:
        conds.append("t.material ILIKE %s"); params.append(f"%{material}%")
    if pr:
        conds.append("t.pr ILIKE %s"); params.append(f"%{pr}%")
    if po:
        conds.append("t.po ILIKE %s"); params.append(f"%{po}%")

    # Filter status PR/PO
    if status == "with_pr":
        conds.append("t.pr IS NOT NULL AND t.pr <> ''")
    elif status == "without_pr":
        conds.append("(t.pr IS NULL OR t.pr = '')")
    elif status == "with_po":
        conds.append("t.po IS NOT NULL AND t.po <> ''")
    elif status == "without_po":
        conds.append("(t.po IS NULL OR t.po = '')")

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    # Kolom sortable yang aman
    SORTABLE = {
        "t.id", "t.plant", "t.equipment", 't."order"', "t.material",
        "t.itm", "t.qty_reqmts", "t.qty_stock", "t.pr", "t.qty_pr",
        "t.po", "t.qty_deliv", "t.delivery_date", "t.reqmts_date",
        "t.res_price", "sp.tracking", "sp.tracking_no",
    }
    safe_ob  = order_by if order_by in SORTABLE else "t.id"
    safe_dir = "DESC" if order_dir.upper() == "DESC" else "ASC"

    # ── JOIN utama: taex sebagai driving table ──
    base_sql = """
        FROM taex_reservasi t
        -- sap_pr: ambil semua kolom, match by pr + material
        LEFT JOIN LATERAL (
            SELECT sp.plant     AS pr_plant,
                   sp.pr        AS pr_pr,
                   sp.item      AS pr_item,
                   sp.material  AS pr_material,
                   sp.material_description AS pr_material_description,
                   sp.d         AS pr_d,
                   sp.r         AS pr_r,
                   sp.pgr       AS pr_pgr,
                   sp.s         AS pr_s,
                   sp.tracking_no,
                   sp.qty_pr    AS pr_qty_pr,
                   sp.un        AS pr_un,
                   sp.req_date,
                   sp.valn_price,
                   sp.pr_curr,
                   sp.pr_per,
                   sp.release_date,
                   sp.tracking
            FROM sap_pr sp
            WHERE sp.pr = t.pr
              AND sp.material = t.material
            ORDER BY sp.id
            LIMIT 1
        ) sp ON TRUE
        -- sap_po: ambil semua kolom, match by po + material
        LEFT JOIN LATERAL (
            SELECT po.plnt          AS po_plnt,
                   po.purchreq      AS po_purchreq,
                   po.item          AS po_item,
                   po.material      AS po_material,
                   po.short_text    AS po_short_text,
                   po.po            AS po_po,
                   po.po_item       AS po_po_item,
                   po.d             AS po_d,
                   po.dci           AS po_dci,
                   po.pgr           AS po_pgr,
                   po.doc_date      AS po_doc_date,
                   po.po_quantity   AS po_quantity,
                   po.qty_delivered AS po_qty_delivered,
                   po.deliv_date    AS po_deliv_date,
                   po.oun           AS po_oun,
                   po.net_price     AS po_net_price,
                   po.crcy          AS po_crcy,
                   po.per           AS po_per
            FROM sap_po po
            WHERE po.po = t.po
              AND po.material = t.material
            ORDER BY po.id
            LIMIT 1
        ) po ON TRUE
        -- work_order: kolom yang relevan untuk tracking progress reservasi
        LEFT JOIN LATERAL (
            SELECT wo.description,
                   wo.system_status,
                   wo.user_status,
                   wo.basic_start_date,
                   wo.basic_finish_date,
                   wo.actual_release,
                   wo.notification,
                   wo.funct_location,
                   wo.planner_group,
                   wo.main_work_ctr
            FROM work_order wo
            WHERE wo."order" = t."order"
            ORDER BY wo.id
            LIMIT 1
        ) wo ON TRUE
    """

    count_res = query(f"SELECT COUNT(*) AS c {base_sql} {where}", params)
    data_res  = query(
        f"""SELECT
            -- ── Semua kolom taex_reservasi ──
            t.id,
            t.plant, t.equipment, t."order", t.revision, t.reservno,
            t.material, t.itm, t.material_description,
            t.qty_reqmts, t.qty_stock, t.qty_pr, t.qty_deliv,
            t.qty_f_avail_check, t.qty_withdrawn,
            t.pr, t.item, t.cost_ctrs,
            t.po, t.po_date, t.delivery_date,
            t.sloc, t.del, t.fis, t.ict, t.pg,
            t.recipient, t.unloading_point, t.reqmts_date,
            t.uom, t.gl_acct, t.res_price, t.res_per, t.res_curr,
            -- ── Semua kolom sap_pr ──
            sp.pr_plant, sp.pr_pr, sp.pr_item, sp.pr_material,
            sp.pr_material_description, sp.pr_d, sp.pr_r, sp.pr_pgr, sp.pr_s,
            sp.tracking_no, sp.pr_qty_pr, sp.pr_un,
            sp.req_date, sp.valn_price, sp.pr_curr, sp.pr_per,
            sp.release_date, sp.tracking,
            -- ── Semua kolom sap_po ──
            po.po_plnt, po.po_purchreq, po.po_item, po.po_material,
            po.po_short_text, po.po_po, po.po_po_item,
            po.po_d, po.po_dci, po.po_pgr,
            po.po_doc_date, po.po_quantity, po.po_qty_delivered,
            po.po_deliv_date, po.po_oun,
            po.po_net_price, po.po_crcy, po.po_per,
            -- ── Kolom work_order yang relevan untuk tracking progress ──
            wo.description      AS wo_description,
            wo.system_status    AS wo_system_status,
            wo.user_status      AS wo_user_status,
            wo.basic_start_date AS wo_basic_start_date,
            wo.basic_finish_date AS wo_basic_finish_date,
            wo.actual_release   AS wo_actual_release,
            wo.notification     AS wo_notification,
            wo.funct_location   AS wo_funct_location,
            wo.planner_group    AS wo_planner_group,
            wo.main_work_ctr    AS wo_main_work_ctr
        {base_sql} {where}
        ORDER BY {safe_ob} {safe_dir}
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )

    def map_tracking(r):
        return {
            # ── taex_reservasi — semua kolom ──
            "ID":                   r["id"],
            "Plant":                r["plant"],
            "Equipment":            r["equipment"],
            "Order":                r["order"],
            "Revision":             r["revision"],
            "Reservno":             r["reservno"],
            "Material":             r["material"],
            "Itm":                  r["itm"],
            "Material_Description": r["material_description"],
            "Qty_Reqmts":           _n(r["qty_reqmts"]),
            "Qty_Stock":            _n(r["qty_stock"]),
            "Qty_PR":               _n(r["qty_pr"]),
            "Qty_Deliv":            _n(r["qty_deliv"]),
            "Qty_f_avail_check":    _n(r["qty_f_avail_check"]),
            "Qty_Withdrawn":        _n(r["qty_withdrawn"]),
            "PR":                   r["pr"],
            "Item":                 r["item"],
            "Cost_Ctrs":            r["cost_ctrs"],
            "PO":                   r["po"],
            "PO_Date":              r["po_date"],
            "Delivery_Date":        r["delivery_date"],
            "SLoc":                 r["sloc"],
            "Del":                  r["del"],
            "FIs":                  r["fis"],
            "Ict":                  r["ict"],
            "PG":                   r["pg"],
            "Recipient":            r["recipient"],
            "Unloading_point":      r["unloading_point"],
            "Reqmts_Date":          r["reqmts_date"],
            "UoM":                  r["uom"],
            "GL_Acct":              r["gl_acct"],
            "Res_Price":            _n(r["res_price"]),
            "Res_per":              _n(r["res_per"]),
            "Res_Curr":             r["res_curr"],
            # ── sap_pr — semua kolom ──
            "PR_Plant":             r["pr_plant"],
            "PR_PR":                r["pr_pr"],
            "PR_Item":              r["pr_item"],
            "PR_Material":          r["pr_material"],
            "PR_Material_Desc":     r["pr_material_description"],
            "PR_D":                 r["pr_d"],
            "PR_R":                 r["pr_r"],
            "PR_PGr":               r["pr_pgr"],
            "PR_S":                 r["pr_s"],
            "TrackingNo":           r["tracking_no"],
            "PR_Qty_PR":            _n(r["pr_qty_pr"]),
            "PR_Un":                r["pr_un"],
            "Req_Date":             r["req_date"],
            "Valn_price":           _n(r["valn_price"]),
            "PR_Curr":              r["pr_curr"],
            "PR_Per":               _n(r["pr_per"]),
            "Release_Date":         r["release_date"],
            "Tracking":             r["tracking"],
            # ── sap_po — semua kolom ──
            "PO_Plnt":              r["po_plnt"],
            "PO_Purchreq":          r["po_purchreq"],
            "PO_Item":              r["po_item"],
            "PO_Material":          r["po_material"],
            "PO_Short_Text":        r["po_short_text"],
            "PO_PO":                r["po_po"],
            "PO_PO_Item":           r["po_po_item"],
            "PO_D":                 r["po_d"],
            "PO_DCI":               r["po_dci"],
            "PO_PGr":               r["po_pgr"],
            "PO_Doc_Date":          r["po_doc_date"],
            "PO_Quantity":          _n(r["po_quantity"]),
            "PO_Qty_Delivered":     _n(r["po_qty_delivered"]),
            "PO_Deliv_Date":        r["po_deliv_date"],
            "PO_OUn":               r["po_oun"],
            "PO_Net_Price":         _n(r["po_net_price"]),
            "PO_Crcy":              r["po_crcy"],
            "PO_Per":               _n(r["po_per"]),
            # ── work_order — kolom relevan untuk progress tracking ──
            "WO_Description":       r["wo_description"],
            "WO_System_Status":     r["wo_system_status"],
            "WO_User_Status":       r["wo_user_status"],
            "WO_Basic_Start":       r["wo_basic_start_date"],
            "WO_Basic_Finish":      r["wo_basic_finish_date"],
            "WO_Actual_Release":    r["wo_actual_release"],
            "WO_Notification":      r["wo_notification"],
            "WO_Funct_Location":    r["wo_funct_location"],
            "WO_Planner_Group":     r["wo_planner_group"],
            "WO_Main_Work_Ctr":     r["wo_main_work_ctr"],
        }

    total = int(count_res[0]["c"])
    return jsonify({
        "data": [map_tracking(r) for r in data_res],
        "pagination": {
            "page": page, "limit": limit, "total": total,
            "totalPages": max(1, -(-total // limit)),
            "hasMore": offset + limit < total,
        },
    })


# ═══════════════════════════════════════════════════════════════
# TRACKING SUMMARY — ringkasan per PR/Tracking dari taex
# (digunakan untuk card summary di halaman tracking)
# ═══════════════════════════════════════════════════════════════
@app.get("/api/tracking/summary")
def get_tracking_summary(request: Request):
    """
    Ringkasan tracking berbasis taex_reservasi:
    - Total material, total Qty_Reqmts, total Qty_Stock, total Qty_PR, total Qty_Deliv
    - Digroup per PR (bukan per sap_pr row)
    - Karena taex sudah 1 baris per reservasi/material, SUM di sini adalah benar
    """
    check_api_key(request)

    summary = query("""
        SELECT
            COALESCE(t.pr, '(Tanpa PR)')  AS pr,
            COUNT(*)                        AS jumlah_material,
            SUM(COALESCE(t.qty_reqmts, 0)) AS total_reqmts,
            SUM(COALESCE(t.qty_stock,  0)) AS total_stock,
            SUM(COALESCE(t.qty_pr,     0)) AS total_qty_pr,
            SUM(COALESCE(t.qty_deliv,  0)) AS total_deliv,
            -- Cek apakah ada PO
            COUNT(CASE WHEN t.po IS NOT NULL AND t.po <> '' THEN 1 END) AS with_po,
            COUNT(CASE WHEN t.po IS NULL OR t.po = ''       THEN 1 END) AS without_po,
            -- Tracking info dari sap_pr (ambil salah satu yang match)
            (SELECT sp.tracking
             FROM sap_pr sp
             WHERE sp.pr = t.pr
             LIMIT 1) AS tracking,
            (SELECT sp.tracking_no
             FROM sap_pr sp
             WHERE sp.pr = t.pr
             LIMIT 1) AS tracking_no
        FROM taex_reservasi t
        GROUP BY t.pr
        ORDER BY t.pr NULLS LAST
    """)

    return jsonify([{
        "PR":             r["pr"],
        "JumlahMaterial": int(r["jumlah_material"]),
        "Total_Reqmts":   _n(r["total_reqmts"]),
        "Total_Stock":    _n(r["total_stock"]),
        "Total_Qty_PR":   _n(r["total_qty_pr"]),
        "Total_Deliv":    _n(r["total_deliv"]),
        "With_PO":        int(r["with_po"]),
        "Without_PO":     int(r["without_po"]),
        "Tracking":       r["tracking"] or "",
        "TrackingNo":     r["tracking_no"] or "",
    } for r in summary])


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
# CHATBOT API — Endpoint khusus untuk chatbot external
# API Key terpisah: CHATBOT_API_KEY
# Base URL: /chatbot/tracking
# ═══════════════════════════════════════════════════════════════

CHATBOT_API_KEY = os.getenv("CHATBOT_API_KEY", "5cRtu21X6O1VHJbE2JVfcKinfSknxgTX56EPS5NIGuY")

def check_chatbot_key(request: Request):
    key = request.headers.get("x-chatbot-key") or request.query_params.get("chatbot_key")
    if key != CHATBOT_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: chatbot API key tidak valid")


@app.get("/chatbot/tracking")
def chatbot_tracking_simple(
    request: Request,
    status:       str  = "",
    plant:        str  = "",
    order:        str  = "",
    equipment:    str  = "",
    material:     str  = "",
    q:            str  = "",
    summary_only: bool = False,
    page:         int  = 1,
    limit:        int  = 50,
):
    """
    Jalur SEDERHANA — chatbot kirim filter, PRISMA yang query.
    Tidak perlu LLM generate SQL.

    Filter tersedia:
    - status: no-pr | pr-created | po-created | partial | complete
    - plant, order, equipment, material, q (search bebas)
    - summary_only=true → return ringkasan bukan detail baris
    - page, limit (max 200)

    Auth: header x-chatbot-key atau query param chatbot_key
    """
    check_chatbot_key(request)

    limit  = min(200, max(1, limit))
    offset = (page - 1) * limit

    conds, params = [], []

    if q:
        conds.append("""(
            t.material ILIKE %s OR t.material_description ILIKE %s
            OR t."order" ILIKE %s OR t.equipment ILIKE %s
            OR t.pr ILIKE %s OR t.reservno ILIKE %s
        )""")
        p = f"%{q}%"; params.extend([p]*6)

    if plant:
        conds.append("t.plant = %s"); params.append(plant)
    if order:
        conds.append('t."order" = %s'); params.append(order)
    if material:
        conds.append("t.material ILIKE %s"); params.append(f"%{material}%")
    if equipment:
        conds.append("t.equipment ILIKE %s"); params.append(f"%{equipment}%")

    status_cond = ""
    if status == "no-pr":
        status_cond = "AND (t.pr IS NULL OR t.pr = '')"
    elif status == "pr-created":
        status_cond = "AND (t.pr IS NOT NULL AND t.pr != '') AND (po_agg.po IS NULL OR po_agg.po = '')"
    elif status == "po-created":
        status_cond = "AND (po_agg.po IS NOT NULL AND po_agg.po != '') AND COALESCE(po_agg.qty_delivered,0) = 0"
    elif status == "partial":
        status_cond = "AND COALESCE(po_agg.qty_delivered,0) > 0 AND COALESCE(po_agg.qty_delivered,0) < COALESCE(po_agg.po_quantity,0)"
    elif status == "complete":
        status_cond = "AND COALESCE(po_agg.qty_delivered,0) >= COALESCE(po_agg.po_quantity,0) AND COALESCE(po_agg.po_quantity,0) > 0"

    sql_base = f"""
        FROM taex_reservasi t
        LEFT JOIN LATERAL (
            SELECT * FROM work_order wo WHERE wo."order" = t."order" LIMIT 1
        ) wo ON true
        LEFT JOIN LATERAL (
            SELECT sp.req_date FROM sap_pr sp
            WHERE sp.pr = t.pr AND (sp.d IS NULL OR sp.d = '') LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT
                po.po, po.doc_date, po.deliv_date, po.crcy,
                SUM(po.po_quantity)   AS po_quantity,
                SUM(po.qty_delivered) AS qty_delivered,
                SUM(po.net_price)     AS net_price
            FROM sap_po po
            WHERE po.purchreq = t.pr AND (po.d IS NULL OR po.d = '')
            GROUP BY po.po, po.doc_date, po.deliv_date, po.crcy
            ORDER BY po.po LIMIT 1
        ) po_agg ON true
        WHERE t.material IS NOT NULL AND t.material != ''
        {("AND " + " AND ".join(conds)) if conds else ""}
        {status_cond}
    """

    def calc_status(r):
        has_pr  = bool(r.get("pr"))
        has_po  = bool(r.get("po_num"))
        qty_po  = float(r.get("po_quantity") or 0)
        qty_del = float(r.get("qty_delivered") or 0)
        if not has_pr:         return "no-pr"
        if not has_po:         return "pr-created"
        if qty_del <= 0:       return "po-created"
        if qty_del < qty_po:   return "partial"
        return "complete"

    if summary_only:
        summary_sql = f"""
            SELECT
                COUNT(*)                                                              AS total_material,
                COUNT(DISTINCT t."order")                                             AS total_order,
                COUNT(DISTINCT t.equipment)                                           AS total_equipment,
                SUM(CASE WHEN t.pr IS NULL OR t.pr='' THEN 1 ELSE 0 END)           AS no_pr,
                SUM(CASE WHEN t.pr IS NOT NULL AND t.pr!=''
                          AND (po_agg.po IS NULL OR po_agg.po='') THEN 1 ELSE 0 END) AS pr_created,
                SUM(CASE WHEN po_agg.po IS NOT NULL AND po_agg.po!=''
                          AND COALESCE(po_agg.qty_delivered,0)=0 THEN 1 ELSE 0 END)  AS po_created,
                SUM(CASE WHEN COALESCE(po_agg.qty_delivered,0)>0
                          AND COALESCE(po_agg.qty_delivered,0)<COALESCE(po_agg.po_quantity,0)
                         THEN 1 ELSE 0 END)                                           AS partial,
                SUM(CASE WHEN COALESCE(po_agg.qty_delivered,0)>=COALESCE(po_agg.po_quantity,0)
                          AND COALESCE(po_agg.po_quantity,0)>0 THEN 1 ELSE 0 END)    AS complete,
                COALESCE(SUM(t.qty_reqmts),0)                                         AS total_qty_reqmts,
                COALESCE(SUM(t.qty_pr),0)                                             AS total_qty_pr,
                COALESCE(SUM(po_agg.net_price),0)                                     AS total_nilai_po
            {sql_base}
        """
        s = query(summary_sql, params)[0]
        return jsonify({
            "mode": "summary",
            "filter": {"status":status,"plant":plant,"order":order,
                       "equipment":equipment,"material":material,"q":q},
            "summary": {
                "total_material":    int(s["total_material"] or 0),
                "total_order":       int(s["total_order"] or 0),
                "total_equipment":   int(s["total_equipment"] or 0),
                "no_pr":             int(s["no_pr"] or 0),
                "pr_created":        int(s["pr_created"] or 0),
                "po_created":        int(s["po_created"] or 0),
                "partial_delivery":  int(s["partial"] or 0),
                "complete":          int(s["complete"] or 0),
                "total_qty_reqmts":  float(s["total_qty_reqmts"] or 0),
                "total_qty_pr":      float(s["total_qty_pr"] or 0),
                "total_nilai_po_idr":float(s["total_nilai_po"] or 0),
            }
        })

    # Detail mode
    count_res = query(f"SELECT COUNT(*) AS c {sql_base}", params)
    total = int(count_res[0]["c"])

    data_sql = f"""
        SELECT
            t.plant, t.equipment, t."order" AS order_val,
            t.reservno, t.material, t.itm, t.material_description,
            t.qty_reqmts, t.qty_stock, t.pr, t.item AS pr_item, t.qty_pr,
            t.del, t.fis, t.ict, t.pg, t.reqmts_date, t.uom,
            wo.description AS order_desc, wo.system_status, wo.planner_group,
            wo.basic_start_date, wo.basic_finish_date,
            sp.req_date,
            po_agg.po AS po_num, po_agg.po_quantity, po_agg.qty_delivered,
            po_agg.deliv_date, po_agg.net_price, po_agg.crcy
        {sql_base}
        ORDER BY t.id
        LIMIT %s OFFSET %s
    """
    rows = query(data_sql, params + [limit, offset])

    data = []
    for r in rows:
        data.append({
            "plant":               r["plant"],
            "equipment":           r["equipment"],
            "order":               r["order_val"],
            "reservno":            r["reservno"],
            "material":            r["material"],
            "itm":                 r["itm"],
            "material_description":r["material_description"],
            "qty_reqmts":          _n(r["qty_reqmts"]),
            "qty_stock":           _n(r["qty_stock"]),
            "pr":                  r["pr"],
            "pr_item":             r["pr_item"],
            "qty_pr":              _n(r["qty_pr"]),
            "uom":                 r["uom"],
            "reqmts_date":         r["reqmts_date"],
            "order_desc":          r["order_desc"],
            "system_status":       r["system_status"],
            "planner_group":       r["planner_group"],
            "basic_start_date":    r["basic_start_date"],
            "basic_finish_date":   r["basic_finish_date"],
            "req_date":            r["req_date"],
            "po_num":              r["po_num"],
            "po_quantity":         _n(r["po_quantity"]),
            "qty_delivered":       _n(r["qty_delivered"]),
            "deliv_date":          r["deliv_date"],
            "net_price":           _n(r["net_price"]),
            "crcy":                r["crcy"],
            "status":              calc_status(r),
        })

    return jsonify({
        "mode": "detail",
        "filter": {"status":status,"plant":plant,"order":order,
                   "equipment":equipment,"material":material,"q":q},
        "pagination": {
            "page":page, "limit":limit,
            "total":total, "total_pages": max(1,-(-total//limit)),
        },
        "data": data,
    })


@app.post("/chatbot/query")
async def chatbot_query(request: Request):
    """
    Endpoint untuk chatbot mengirim query SQL dan mendapat hasilnya.

    Request body:
    {
        "sql": "SELECT material, qty_reqmts FROM taex_reservasi WHERE pr IS NULL LIMIT 10"
    }

    Auth: header x-chatbot-key atau query param chatbot_key

    Aturan keamanan:
    - Hanya SELECT yang diizinkan
    - Tabel yang boleh di-query: taex_reservasi, prisma_reservasi,
      kumpulan_summary, sap_pr, sap_po, work_order
    - LIMIT wajib ada, max 500 baris
    - Query berbahaya (DROP, DELETE, UPDATE, INSERT, TRUNCATE) ditolak
    """
    check_chatbot_key(request)

    body = await request.json()
    sql  = (body.get("sql") or "").strip()

    if not sql:
        raise HTTPException(400, "Body harus berisi field 'sql'")

    sql_upper = sql.upper()
    FORBIDDEN = ["DROP","DELETE","UPDATE","INSERT","TRUNCATE","ALTER","CREATE",
                 "GRANT","REVOKE","EXEC","EXECUTE","COPY","pg_","information_schema"]
    for word in FORBIDDEN:
        if word.upper() in sql_upper:
            raise HTTPException(403, f"Query tidak diizinkan: mengandung '{word}'")

    if not sql_upper.lstrip().startswith("SELECT"):
        raise HTTPException(403, "Hanya query SELECT yang diizinkan")

    ALLOWED_TABLES = {
        "taex_reservasi", "prisma_reservasi", "kumpulan_summary",
        "sap_pr", "sap_po", "work_order"
    }
    import re
    tables_in_query = set(re.findall(r'(?:FROM|JOIN)\s+([\w\"]+)', sql, re.IGNORECASE))
    tables_clean    = {t.strip('"').lower() for t in tables_in_query}
    disallowed      = tables_clean - ALLOWED_TABLES
    if disallowed:
        raise HTTPException(403, f"Tabel tidak diizinkan: {', '.join(disallowed)}")

    limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
    if not limit_match:
        raise HTTPException(400, "Query harus mengandung LIMIT (maksimal 500)")
    if int(limit_match.group(1)) > 500:
        raise HTTPException(400, "LIMIT maksimal 500 baris")

    try:
        rows = query(sql)
    except Exception as e:
        raise HTTPException(400, f"Query error: {str(e)}")

    import decimal, datetime as dt
    def clean(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if isinstance(v, (dt.datetime, dt.date)): return str(v)
        return v

    data = [{k: clean(v) for k, v in dict(r).items()} for r in rows]

    return jsonify({
        "ok":      True,
        "sql":     sql,
        "rows":    len(data),
        "columns": list(data[0].keys()) if data else [],
        "data":    data,
    })


@app.get("/chatbot/schema")
def chatbot_schema(request: Request):
    """
    Fetch schema langsung dari PostgreSQL information_schema.
    Return nama kolom, tipe data, dan nullable untuk semua tabel yang diizinkan.
    Dipanggil chatbot sekali saat startup untuk build prompt otomatis.
    """
    check_chatbot_key(request)

    ALLOWED_TABLES = [
        "taex_reservasi", "prisma_reservasi", "kumpulan_summary",
        "sap_pr", "sap_po", "work_order"
    ]

    rows = query("""
        SELECT
            table_name, column_name, data_type,
            is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
    """, (ALLOWED_TABLES,))

    tables = {}
    for r in rows:
        tbl = r["table_name"]
        if tbl not in tables:
            tables[tbl] = {"columns": [], "column_names": []}
        tables[tbl]["columns"].append({
            "name":     r["column_name"],
            "type":     r["data_type"],
            "nullable": r["is_nullable"] == "YES",
            "default":  r["column_default"],
        })
        tables[tbl]["column_names"].append(r["column_name"])

    TABLE_DESC = {
        "taex_reservasi":   "Data reservasi material TA-ex (sumber utama tracking procurement)",
        "prisma_reservasi": "Subset taex aktif (ict=L), berisi status kertas kerja dan PR",
        "kumpulan_summary": "Ringkasan kebutuhan material per kertas kerja (code_tracking)",
        "sap_pr":           "Purchase Request dari SAP (join ke taex via pr=pr)",
        "sap_po":           "Purchase Order dari SAP (join ke taex via purchreq=pr)",
        "work_order":       "Work Order SAP (join ke taex via order=order)",
    }

    result = {}
    for tbl in ALLOWED_TABLES:
        if tbl in tables:
            result[tbl] = {
                "description":  TABLE_DESC.get(tbl, ""),
                "columns":      tables[tbl]["columns"],
                "column_names": tables[tbl]["column_names"],
            }

    return jsonify({
        "allowed_tables": list(result.keys()),
        "tables":         result,
        "join_hints": {
            "taex_ke_workorder":  'taex_reservasi t JOIN work_order wo ON wo."order" = t."order"',
            "taex_ke_sap_pr":     "taex_reservasi t JOIN sap_pr sp ON sp.pr = t.pr",
            "taex_ke_sap_po":     "taex_reservasi t JOIN sap_po po ON po.purchreq = t.pr",
            "prisma_ke_kumpulan": "prisma_reservasi p JOIN kumpulan_summary k ON k.code_tracking = p.code_kertas_kerja AND k.material = p.material",
        },
        "status_logic": {
            "no-pr":      "pr IS NULL OR pr = ''",
            "pr-created": "pr IS NOT NULL AND pr != '' AND po belum ada",
            "po-created": "po ada AND qty_delivered = 0",
            "partial":    "qty_delivered > 0 AND qty_delivered < po_quantity",
            "complete":   "qty_delivered >= po_quantity AND po_quantity > 0",
        },
        "important_notes": [
            "Kolom 'order' adalah reserved word PostgreSQL — WAJIB ditulis dengan tanda kutip: \"order\"",
            "Selalu gunakan LIMIT maksimal 500",
            "Join PO ke taex: sap_po.purchreq = taex_reservasi.pr",
            "Join PR ke taex: sap_pr.pr = taex_reservasi.pr",
            "Join WO ke taex: work_order.\"order\" = taex_reservasi.\"order\"",
        ],
        "security": {
            "allowed_statements": ["SELECT only"],
            "max_limit":          500,
            "forbidden_keywords": ["DROP","DELETE","UPDATE","INSERT","TRUNCATE","ALTER","CREATE"],
        }
    })


# ═══════════════════════════════════════════════════════════════
# SPA FALLBACK
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# PROJECT
# ═══════════════════════════════════════════════════════════════
def map_project(r):
    return {
        "ID": r["id"], "ProjectNumber": r["project_number"],
        "ProjectTypeId": r["project_type_id"],
        "StartDate": r["start_date"], "FinishDate": r["finish_date"],
        "Revision": r["revision"], "Description": r["description"],
        "ProjectStatus": r["project_status"], "Plant": r["plant"],
        "Created": r["created"], "CreatedBy": r["created_by"],
        "IsDeleted": r["is_deleted"],
        "Modified": r["modified"], "ModifiedBy": r["modified_by"],
        "DurationTaBrickId": r["duration_ta_brick_id"],
    }

@app.get("/api/project")
def get_project(request: Request, plant: str = None):
    check_api_key(request)
    if plant:
        rows = query("SELECT * FROM project WHERE is_deleted=0 AND plant=%s ORDER BY project_number", (plant,))
    else:
        rows = query("SELECT * FROM project WHERE is_deleted=0 ORDER BY project_number")
    return jsonify([map_project(r) for r in rows])

@app.get("/api/project/{project_id}")
def get_project_by_id(project_id: str, request: Request):
    check_api_key(request)
    row = query("SELECT * FROM project WHERE id=%s", (project_id,))
    if not row:
        raise HTTPException(404, "Project tidak ditemukan")
    return jsonify(map_project(row[0]))

@app.post("/api/project/replace")
async def replace_project(request: Request, file: UploadFile = File(...)):
    check_api_key(request)
    content = await file.read()
    df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    cnt = bulk_replace_project(df)
    return {"inserted": cnt}

@app.delete("/api/project/{project_id}")
def delete_project(project_id: str, request: Request):
    check_api_key(request)
    execute("UPDATE project SET is_deleted=1 WHERE id=%s", (project_id,))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# JOB LIST
# ═══════════════════════════════════════════════════════════════
def map_job_list(r):
    return {
        "ID": r["id"], "ProjectId": r["project_id"],
        "EquipmentId": r["equipment_id"], "Plant": r["plant"],
        "Created": r["created"], "CreatedBy": r["created_by"],
        "IsDeleted": r["is_deleted"],
        "Modified": r["modified"], "ModifiedBy": r["modified_by"],
        "JoblistDescription": r["joblist_description"],
        "NoJoblist": r["no_joblist"],
    }

@app.get("/api/joblist")
def get_job_list(request: Request, project_id: str = None, plant: str = None):
    check_api_key(request)
    if project_id:
        rows = query(
            "SELECT * FROM job_list WHERE is_deleted=0 AND project_id=%s ORDER BY no_joblist",
            (project_id,)
        )
    elif plant:
        rows = query(
            "SELECT * FROM job_list WHERE is_deleted=0 AND plant=%s ORDER BY no_joblist",
            (plant,)
        )
    else:
        rows = query("SELECT * FROM job_list WHERE is_deleted=0 ORDER BY no_joblist")
    return jsonify([map_job_list(r) for r in rows])

@app.get("/api/joblist/{joblist_id}")
def get_job_list_by_id(joblist_id: str, request: Request):
    check_api_key(request)
    row = query("SELECT * FROM job_list WHERE id=%s", (joblist_id,))
    if not row:
        raise HTTPException(404, "Joblist tidak ditemukan")
    return jsonify(map_job_list(row[0]))

@app.post("/api/joblist/replace")
async def replace_job_list(request: Request, file: UploadFile = File(...)):
    check_api_key(request)
    content = await file.read()
    df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    cnt = bulk_replace_job_list(df)
    return {"inserted": cnt}

@app.delete("/api/joblist/{joblist_id}")
def delete_job_list(joblist_id: str, request: Request):
    check_api_key(request)
    execute("UPDATE job_list SET is_deleted=1 WHERE id=%s", (joblist_id,))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# JOB DETAIL
# ═══════════════════════════════════════════════════════════════
def map_job_detail(r):
    return {
        "ID": r["id"], "JoblistId": r["joblist_id"],
        "JoblistDetailReasonId": r["joblist_detail_reason_id"],
        "JoblistDetailDescription": r["joblist_detail_description"],
        "IsMechanicalIntegrity": r["is_mechanical_integrity"],
        "IsOptimization": r["is_optimization"],
        "JobDisciplineId": r["job_discipline_id"],
        "Plant": r["plant"],
        "Created": r["created"], "CreatedBy": r["created_by"],
        "IsDeleted": r["is_deleted"],
        "Modified": r["modified"], "ModifiedBy": r["modified_by"],
        "NoDocument": r["no_document"],
        "CreatorJobTitle": r["creator_job_title"], "CreatorName": r["creator_name"],
        "AssignTo": r["assign_to"], "AuthparamArea": r["authparam_area"],
        "StatusId": r["status_id"],
        "IsOffStream": r["is_off_stream"],
        "NomorPM": r["nomor_pm"], "Collective": r["collective"],
        "Notes": r["notes"],
        "PICPlanner": r["pic_planner"], "PICPlannerName": r["pic_planner_name"],
        "IsAllIn": r["is_all_in"],
        "IsJasa": r["is_jasa"], "IsLLDII": r["is_lldii"], "IsMaterial": r["is_material"],
        "NoJoblistDetail": r["no_joblist_detail"],
        "IsRequestFreezing": r["is_request_freezing"],
        "PlanningStatusId": r["planning_status_id"],
        "PlanningMaterialStatusId": r["planning_material_status_id"],
        "PlanningJasaStatusId": r["planning_jasa_status_id"],
    }

@app.get("/api/jobdetail")
def get_job_detail(request: Request, joblist_id: str = None, plant: str = None,
                   page: int = 1, limit: int = 500):
    check_api_key(request)
    offset = (page - 1) * limit
    if joblist_id:
        rows = query(
            "SELECT * FROM job_detail WHERE is_deleted=0 AND joblist_id=%s ORDER BY no_joblist_detail LIMIT %s OFFSET %s",
            (joblist_id, limit, offset)
        )
    elif plant:
        rows = query(
            "SELECT * FROM job_detail WHERE is_deleted=0 AND plant=%s ORDER BY no_joblist_detail LIMIT %s OFFSET %s",
            (plant, limit, offset)
        )
    else:
        rows = query(
            "SELECT * FROM job_detail WHERE is_deleted=0 ORDER BY no_joblist_detail LIMIT %s OFFSET %s",
            (limit, offset)
        )
    return jsonify([map_job_detail(r) for r in rows])

@app.get("/api/jobdetail/{detail_id}")
def get_job_detail_by_id(detail_id: str, request: Request):
    check_api_key(request)
    row = query("SELECT * FROM job_detail WHERE id=%s", (detail_id,))
    if not row:
        raise HTTPException(404, "Job detail tidak ditemukan")
    return jsonify(map_job_detail(row[0]))

@app.post("/api/jobdetail/replace")
async def replace_job_detail(request: Request, file: UploadFile = File(...)):
    check_api_key(request)
    content = await file.read()
    job_id = f"jobdetail_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    set_job(job_id, 0, "Membaca file Excel...")

    def _bg():
        try:
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
            if df.empty:
                set_job(job_id, 100, "File kosong", True, "File Excel kosong")
                return
            set_job(job_id, 20, f"Parsed {len(df):,} baris. Menyimpan...")
            cnt = bulk_replace_job_detail(df)
            set_job(job_id, 100, f"✅ Selesai! {cnt:,} baris tersimpan", True)
        except Exception as e:
            set_job(job_id, 100, f"❌ {e}", True, str(e))

    threading.Thread(target=_bg, daemon=True).start()
    return {"jobId": job_id}

@app.delete("/api/jobdetail/{detail_id}")
def delete_job_detail(detail_id: str, request: Request):
    check_api_key(request)
    execute("UPDATE job_detail SET is_deleted=1 WHERE id=%s", (detail_id,))
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# JOB DETAIL WORK ORDER
# ═══════════════════════════════════════════════════════════════
def map_job_detail_work_order(r):
    return {
        "ID": r["id"], "JoblistDetailId": r["joblist_detail_id"],
        "Notification": r["notification"], "CreatedOn": r["created_on"],
        "SuperiorOrder": r["superior_order"], "Order": r["order"],
        "Description": r["description"], "Equipment": r["equipment"],
        "FunctionalLoc": r["functional_loc"], "Location": r["location"],
        "Revision": r["revision"],
        "SystemStatus": r["system_status"], "UserStatus": r["user_status"],
        "WBSordheader": r["wbs_ord_header"],
        "TotalPlnndCosts": _n(r["total_plnnd_costs"]),
        "Totalactcosts": _n(r["totalact_costs"]),
        "PlannerGroup": r["planner_group"], "MainWorkCtr": r["main_work_ctr"],
        "ChangeBy": r["change_by"],
        "Basstartdate": r["bas_start_date"], "Basicfindate": r["basic_fin_date"],
        "ActualRelease": r["actual_release"],
        "CostCenter": r["cost_center"], "EnteredBy": r["entered_by"],
        "Created": r["created"], "CreatedBy": r["created_by"],
        "IsDeleted": r["is_deleted"],
        "Modified": r["modified"], "ModifiedBy": r["modified_by"],
    }

@app.get("/api/jobdetailworkorder")
def get_job_detail_work_order(request: Request,
                               joblist_detail_id: str = None,
                               order: str = None):
    check_api_key(request)
    if joblist_detail_id:
        rows = query(
            'SELECT * FROM job_detail_work_order WHERE is_deleted=0 AND joblist_detail_id=%s ORDER BY id',
            (joblist_detail_id,)
        )
    elif order:
        rows = query(
            'SELECT * FROM job_detail_work_order WHERE is_deleted=0 AND "order"=%s ORDER BY id',
            (order,)
        )
    else:
        rows = query('SELECT * FROM job_detail_work_order WHERE is_deleted=0 ORDER BY id')
    return jsonify([map_job_detail_work_order(r) for r in rows])

@app.post("/api/jobdetailworkorder/replace")
async def replace_job_detail_work_order(request: Request, file: UploadFile = File(...)):
    check_api_key(request)
    content = await file.read()
    df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    cnt = bulk_replace_job_detail_work_order(df)
    return {"inserted": cnt}

@app.delete("/api/jobdetailworkorder/{row_id}")
def delete_job_detail_work_order(row_id: str, request: Request):
    check_api_key(request)
    execute("UPDATE job_detail_work_order SET is_deleted=1 WHERE id=%s", (row_id,))
    return {"ok": True}

# ─── JOIN: Project → Joblist → JobDetail → WorkOrder ────────
@app.get("/api/project/{project_id}/full")
def get_project_full(project_id: str, request: Request):
    """
    Mengembalikan satu project beserta semua joblist, jobdetail,
    dan work order yang terkait — dalam satu response JSON terstruktur.
    """
    check_api_key(request)
    proj = query("SELECT * FROM project WHERE id=%s", (project_id,))
    if not proj:
        raise HTTPException(404, "Project tidak ditemukan")

    joblists = query(
        "SELECT * FROM job_list WHERE project_id=%s AND is_deleted=0 ORDER BY no_joblist",
        (project_id,)
    )
    result = map_project(proj[0])
    result["Joblists"] = []

    for jl in joblists:
        jl_data = map_job_list(jl)
        details = query(
            "SELECT * FROM job_detail WHERE joblist_id=%s AND is_deleted=0 ORDER BY no_joblist_detail",
            (jl["id"],)
        )
        jl_data["JobDetails"] = []
        for jd in details:
            jd_data = map_job_detail(jd)
            wos = query(
                "SELECT * FROM job_detail_work_order WHERE joblist_detail_id=%s AND is_deleted=0",
                (jd["id"],)
            )
            jd_data["WorkOrders"] = [map_job_detail_work_order(w) for w in wos]
            jl_data["JobDetails"].append(jd_data)
        result["Joblists"].append(jl_data)

    return jsonify(result)


@app.get("/api/jobdetail/summary")
def get_jobdetail_summary(request: Request, plant: str = None):
    """Summary jobdetail: count per collective, per status material/jasa"""
    check_api_key(request)
    plant_clause = "AND plant=%s" if plant else ""
    params = (plant,) if plant else ()
    rows = query(f"""
        SELECT
            collective,
            COUNT(*) AS total,
            SUM(CASE WHEN is_material=1 THEN 1 ELSE 0 END) AS total_material,
            SUM(CASE WHEN is_jasa=1 THEN 1 ELSE 0 END) AS total_jasa,
            SUM(CASE WHEN is_lldii=1 THEN 1 ELSE 0 END) AS total_lldii,
            SUM(CASE WHEN is_off_stream=1 THEN 1 ELSE 0 END) AS total_off_stream,
            SUM(CASE WHEN is_mechanical_integrity=1 THEN 1 ELSE 0 END) AS total_mi
        FROM job_detail
        WHERE is_deleted=0 {plant_clause}
        GROUP BY collective
        ORDER BY collective NULLS LAST
    """, params)
    return jsonify([{
        "Collective": r["collective"],
        "Total": int(r["total"]),
        "TotalMaterial": int(r["total_material"] or 0),
        "TotalJasa": int(r["total_jasa"] or 0),
        "TotalLLDII": int(r["total_lldii"] or 0),
        "TotalOffStream": int(r["total_off_stream"] or 0),
        "TotalMI": int(r["total_mi"] or 0),
    } for r in rows])


@app.get("/api/data/project")
def get_data_project(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM project WHERE is_deleted=0 ORDER BY project_number")
    return jsonify([map_project(r) for r in rows])

@app.get("/api/data/joblist")
def get_data_joblist(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM job_list WHERE is_deleted=0 ORDER BY no_joblist")
    return jsonify([map_job_list(r) for r in rows])

@app.get("/api/data/jobdetail")
def get_data_jobdetail(request: Request, page: int = 1, limit: int = 500):
    check_api_key(request)
    offset = (page - 1) * limit
    rows = query(
        "SELECT * FROM job_detail WHERE is_deleted=0 ORDER BY no_joblist_detail LIMIT %s OFFSET %s",
        (limit, offset)
    )
    return jsonify([map_job_detail(r) for r in rows])

@app.get("/api/data/jobdetailworkorder")
def get_data_jdwo(request: Request):
    check_api_key(request)
    rows = query("SELECT * FROM job_detail_work_order WHERE is_deleted=0 ORDER BY id")
    return jsonify([map_job_detail_work_order(r) for r in rows])


# ═══════════════════════════════════════════════════════════════
# EQUIPMENT TAEX
# ═══════════════════════════════════════════════════════════════
def map_equipment(r):
    return {
        "ID": r["id"], "Plant": r["plant"],
        "UnitId": r["unit_id"],
        "EquipmentNo": r["equipment_no"],
        "DescriptionofTechnicalObject": r["description_of_technical_object"],
        "FunctionalLocation": r["functional_location"],
        "Location": r["location"],
        "Disiplin": r["disiplin"],
        "EquipmentCategory": r["equipment_category"],
        "GroupAsset": r["group_asset"],
        "Criticallity": r["criticallity"],
        "CriticallityText": r["criticallity_text"],
        "CatalogProfile": r["catalog_profile"],
        "CatalogProfileText": r["catalog_profile_text"],
        "MainWorkCenter": r["main_work_center"],
        "MaintenancePlant": r["maintenance_plant"],
        "PlanningPlant": r["planning_plant"],
        "ModelType": r["model_type"],
        "ManufacturerOfAsset": r["manufacturer_of_asset"],
        "Created": r["created"], "CreatedBy": r["created_by"],
        "IsDeleted": r["is_deleted"],
        "Modified": r["modified"], "ModifiedBy": r["modified_by"],
    }

@app.get("/api/equipment")
def get_equipment(request: Request, plant: str = None, disiplin: str = None,
                  q: str = None, page: int = 1, limit: int = 500):
    check_api_key(request)
    clauses = ["is_deleted=0"]
    params = []
    if plant:
        clauses.append("plant=%s"); params.append(plant)
    if disiplin:
        clauses.append("disiplin=%s"); params.append(disiplin)
    if q:
        clauses.append("(equipment_no ILIKE %s OR description_of_technical_object ILIKE %s OR functional_location ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    where = " AND ".join(clauses)
    offset = (page - 1) * limit
    total = query(f"SELECT COUNT(*) AS n FROM equipment_taex WHERE {where}", params)[0]["n"]
    rows = query(f"SELECT * FROM equipment_taex WHERE {where} ORDER BY equipment_no LIMIT %s OFFSET %s",
                 params + [limit, offset])
    return jsonify({"total": total, "page": page, "limit": limit,
                    "data": [map_equipment(r) for r in rows]})

@app.get("/api/equipment/{eq_id}")
def get_equipment_by_id(eq_id: str, request: Request):
    check_api_key(request)
    row = query("SELECT * FROM equipment_taex WHERE id=%s", (eq_id,))
    if not row:
        raise HTTPException(404, "Equipment tidak ditemukan")
    return jsonify(map_equipment(row[0]))

@app.post("/api/equipment/replace")
async def replace_equipment(request: Request, file: UploadFile = File(...)):
    check_api_key(request)
    content = await file.read()
    job_id = f"equipment_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    set_job(job_id, 0, "Membaca file Excel...")

    def _bg():
        try:
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
            if df.empty:
                set_job(job_id, 100, "File kosong", True, "File Excel kosong"); return
            set_job(job_id, 20, f"Parsed {len(df):,} baris. Menyimpan...")
            cnt = bulk_replace_equipment_taex(df)
            set_job(job_id, 100, f"✅ Selesai! {cnt:,} baris tersimpan", True)
        except Exception as e:
            set_job(job_id, 100, f"❌ {e}", True, str(e))

    threading.Thread(target=_bg, daemon=True).start()
    return {"jobId": job_id}

@app.get("/api/equipment/meta/filters")
def equipment_meta(request: Request):
    check_api_key(request)
    plants    = query("SELECT DISTINCT plant FROM equipment_taex WHERE is_deleted=0 AND plant IS NOT NULL ORDER BY plant")
    disiplins = query("SELECT DISTINCT disiplin FROM equipment_taex WHERE is_deleted=0 AND disiplin IS NOT NULL ORDER BY disiplin")
    groups    = query("SELECT DISTINCT group_asset FROM equipment_taex WHERE is_deleted=0 AND group_asset IS NOT NULL ORDER BY group_asset")
    crits     = query("SELECT DISTINCT criticallity_text FROM equipment_taex WHERE is_deleted=0 AND criticallity_text IS NOT NULL ORDER BY criticallity_text")
    return jsonify({
        "plants":    [r["plant"] for r in plants],
        "disiplins": [r["disiplin"] for r in disiplins],
        "groups":    [r["group_asset"] for r in groups],
        "criticallities": [r["criticallity_text"] for r in crits],
    })

@app.delete("/api/equipment/{eq_id}")
def delete_equipment(eq_id: str, request: Request):
    check_api_key(request)
    execute("UPDATE equipment_taex SET is_deleted=1 WHERE id=%s", (eq_id,))
    return {"ok": True}

@app.get("/api/data/equipment")
def get_data_equipment(request: Request, page: int = 1, limit: int = 500):
    check_api_key(request)
    offset = (page - 1) * limit
    total = query("SELECT COUNT(*) AS n FROM equipment_taex WHERE is_deleted=0")[0]["n"]
    rows = query("SELECT * FROM equipment_taex WHERE is_deleted=0 ORDER BY equipment_no LIMIT %s OFFSET %s",
                 (limit, offset))
    return jsonify({"total": total, "page": page, "limit": limit,
                    "data": [map_equipment(r) for r in rows]})


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    return FileResponse("public/index.html")