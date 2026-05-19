"""
update_pr.py — Update PR/Item/QPR di taex_reservasi
Fitur transisi: update kolom PR, Item, QPR berdasarkan Order + Itm
Endpoint terpisah agar tidak mengganggu logic utama
"""

import io
import json
import time
import uuid
import threading
from decimal import Decimal
from datetime import datetime, date

import pandas as pd
from fastapi import APIRouter, File, UploadFile, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse

router = APIRouter(prefix="/api/update-pr", tags=["update-pr"])


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        if isinstance(o, (datetime, date)): return str(o)
        return super().default(o)

def J(data):
    return JSONResponse(content=json.loads(json.dumps(data, cls=_Enc)))


def _require_admin(request: Request):
    from main import get_current_user, check_api_key
    check_api_key(request)
    user = get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "Admin only")
    return user


_jobs = {}
_lock = threading.Lock()

def _set_job(job_id, pct, msg, done=False, error=None):
    with _lock:
        _jobs[job_id] = {"pct": pct, "msg": msg, "done": done, "error": error}


# ── Progress endpoint ─────────────────────────────────────────
@router.get("/progress/{job_id}")
def progress(job_id: str, request: Request):
    from main import check_api_key
    check_api_key(request)
    job = _jobs.get(job_id, {"pct": 0, "msg": "Menunggu...", "done": False})
    return J(job)


# ── Preview endpoint — baca Excel, tampilkan preview ─────────
@router.post("/preview")
async def preview(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    content = await file.read()

    try:
        df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    except Exception as e:
        raise HTTPException(400, f"Gagal baca file: {e}")

    # Normalisasi kolom
    df.columns = [str(c).strip() for c in df.columns]

    # Cek kolom wajib
    required = {"Order", "Itm", "PR"}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(400, f"Kolom tidak ditemukan: {', '.join(missing)}")

    # Bersihkan
    df = df.fillna("")
    df["Order"] = df["Order"].str.strip()
    df["Itm"]   = df["Itm"].str.strip().str.zfill(4)
    df["PR"]    = df["PR"].str.strip()
    df["Item"]  = df["Item"].str.strip() if "Item" in df.columns else ""
    df["QPR"]   = df["QPR"].str.strip()  if "QPR"  in df.columns else ""

    # Filter baris yang punya Order + Itm + PR
    df = df[df["Order"].str.len() > 0]
    df = df[df["Itm"].str.len() > 0]

    preview_rows = df.head(20).to_dict(orient="records")
    return J({
        "total_rows": len(df),
        "preview":    preview_rows,
        "columns":    df.columns.tolist(),
    })


# ── Upload & Update endpoint ──────────────────────────────────
@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    content = await file.read()
    job_id  = f"updatepr_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    _set_job(job_id, 0, "Membaca file Excel...")

    def _bg():
        try:
            from database import get_conn, release_conn

            _set_job(job_id, 5, "Parsing Excel...")
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.fillna("")

            if "Order" not in df.columns or "Itm" not in df.columns:
                _set_job(job_id, 100, "❌ Kolom Order/Itm tidak ditemukan", True,
                         "Kolom Order/Itm tidak ditemukan")
                return

            df["Order"] = df["Order"].str.strip()
            df["Itm"]   = df["Itm"].str.strip().str.zfill(4)
            df["PR"]    = df["PR"].str.strip()    if "PR"   in df.columns else ""
            df["Item"]  = df["Item"].str.strip()  if "Item" in df.columns else ""
            df["QPR"]   = df["QPR"].str.strip()   if "QPR"  in df.columns else ""

            df = df[df["Order"].str.len() > 0]
            df = df[df["Itm"].str.len() > 0]

            total = len(df)
            _set_job(job_id, 10, f"Parsed {total:,} baris. Mengupdate database...")

            conn = get_conn()
            updated = 0
            not_found = 0

            try:
                with conn.cursor() as cur:
                    for i, row in df.iterrows():
                        pr   = row.get("PR", "")   or None
                        item = row.get("Item", "") or None
                        qpr  = row.get("QPR", "")  or None

                        # Update hanya kolom PR, Item, QPR (qty_pr)
                        cur.execute("""
                            UPDATE taex_reservasi
                            SET    pr       = %s,
                                   item     = %s,
                                   qty_pr   = %s,
                                   updated_at = NOW()
                            WHERE  "order" = %s
                              AND  itm     = %s
                        """, (pr, item, qpr, row["Order"], row["Itm"]))

                        if cur.rowcount > 0:
                            updated += cur.rowcount
                        else:
                            not_found += 1

                        # Progress update setiap 100 baris
                        if (i + 1) % 100 == 0:
                            pct = int(10 + (i + 1) / total * 85)
                            _set_job(job_id, pct,
                                     f"Update {i+1:,}/{total:,} baris...")

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                release_conn(conn)

            _set_job(job_id, 100,
                     f"✅ Selesai! {updated:,} baris ter-update, "
                     f"{not_found:,} baris tidak ditemukan di database",
                     done=True)

        except Exception as e:
            _set_job(job_id, 100, f"❌ Error: {str(e)}", done=True, error=str(e))

    threading.Thread(target=_bg, daemon=True).start()
    return J({"jobId": job_id})


# ── Serve UI ──────────────────────────────────────────────────
@router.get("/ui")
def serve_ui():
    return FileResponse("public/update_pr.html")
