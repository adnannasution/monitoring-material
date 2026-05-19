"""
dashboard.py — TA Excellence Dashboard
Endpoint khusus dashboard readiness (Page 1 & 2)
Akses: admin only via token
"""

from fastapi import APIRouter, Request, HTTPException
from database import query, query_one
import json
from decimal import Decimal
from datetime import datetime, date

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ── JSON encoder ─────────────────────────────────────────────
class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        if isinstance(o, (datetime, date)): return str(o)
        return super().default(o)

def J(data):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=json.loads(json.dumps(data, cls=_Enc)))

# ── Auth helper ───────────────────────────────────────────────
def _require_admin(request: Request):
    from main import get_current_user, check_api_key
    check_api_key(request)
    user = get_current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "Admin only")
    return user


# ═══════════════════════════════════════════════════════════════
# PAGE 1 — Readiness Equipment per Plant (RU)
# ═══════════════════════════════════════════════════════════════
@router.get("/readiness-equipment")
def readiness_equipment(request: Request, plant: str = ""):
    _require_admin(request)

    plant_filter = "AND jl.plant = %s" if plant else ""
    params = [plant] if plant else []

    rows = query(f"""
        WITH order_ready AS (
            SELECT "order",
                   COUNT(*)  AS total_mat,
                   SUM(CASE WHEN COALESCE(qty_deliv,0) >= qty_reqmts
                                 AND qty_reqmts > 0 THEN 1 ELSE 0 END) AS ready_mat
            FROM taex_reservasi
            GROUP BY "order"
        ),
        eq_ready AS (
            SELECT
                jl.plant,
                jl.equipment_id,
                eq.equipment_no,
                BOOL_AND(
                    CASE WHEN COALESCE(or_.total_mat,0) > 0
                              AND or_.ready_mat = or_.total_mat
                         THEN TRUE ELSE FALSE END
                ) AS equipment_ready,
                COUNT(DISTINCT wo.id) AS total_wo,
                SUM(COALESCE(or_.total_mat,0)) AS total_mat,
                SUM(COALESCE(or_.ready_mat,0)) AS ready_mat
            FROM job_detail_work_order wo
            JOIN job_detail     jd  ON wo.joblist_detail_id = jd.id
            JOIN job_list       jl  ON jd.joblist_id        = jl.id
            LEFT JOIN equipment_taex eq ON jl.equipment_id  = eq.id
            LEFT JOIN order_ready or_   ON or_."order"      = wo."order"
            WHERE wo.is_deleted = 0
              AND jl.equipment_id IS NOT NULL
              {plant_filter}
            GROUP BY jl.plant, jl.equipment_id, eq.equipment_no
        )
        SELECT
            p.plant_code,
            p.plant_name,
            COUNT(*)  AS total_equipment,
            SUM(CASE WHEN er.equipment_ready THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN NOT er.equipment_ready THEN 1 ELSE 0 END) AS not_ready,
            ROUND(
                SUM(CASE WHEN er.equipment_ready THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(*), 0), 2
            ) AS pct_ready
        FROM eq_ready er
        JOIN plants p ON p.plant_code = er.plant
        GROUP BY p.plant_code, p.plant_name
        ORDER BY p.plant_code
    """, params)

    # Total semua RU
    total_eq   = sum(int(r["total_equipment"] or 0) for r in rows)
    total_rdy  = sum(int(r["ready"] or 0) for r in rows)
    total_nrdy = sum(int(r["not_ready"] or 0) for r in rows)

    return J({
        "summary": {
            "total_equipment": total_eq,
            "ready":     total_rdy,
            "not_ready": total_nrdy,
            "pct_ready": round(total_rdy / total_eq * 100, 2) if total_eq else 0,
        },
        "by_plant": [{
            "plant_code":      r["plant_code"],
            "plant_name":      r["plant_name"],
            "total_equipment": int(r["total_equipment"] or 0),
            "ready":           int(r["ready"] or 0),
            "not_ready":       int(r["not_ready"] or 0),
            "pct_ready":       float(r["pct_ready"] or 0),
        } for r in rows],
    })


# ═══════════════════════════════════════════════════════════════
# PAGE 1 — Readiness Material & Jasa per Bulan (line+bar chart)
# Plan = count dari Req_Date, Actual = count dari Delivery_Date
# ═══════════════════════════════════════════════════════════════
@router.get("/readiness-monthly")
def readiness_monthly(request: Request, plant: str = "", year: str = ""):
    _require_admin(request)

    plant_cond = "AND t.plant = %s" if plant else ""
    year_cond  = "AND EXTRACT(YEAR FROM t.reqmts_date::date) = %s" if year else ""
    params_p   = []
    if plant: params_p.append(plant)
    if year:  params_p.append(int(year))

    # Plan: material yang req_date jatuh di bulan tsb
    plan_rows = query(f"""
        SELECT
            TO_CHAR(t.reqmts_date::date, 'YYYY-MM') AS bulan,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE t.reqmts_date IS NOT NULL
          {plant_cond} {year_cond}
        GROUP BY bulan
        ORDER BY bulan
    """, params_p)

    params_a = []
    if plant: params_a.append(plant)
    year_cond_a = "AND EXTRACT(YEAR FROM t.delivery_date::date) = %s" if year else ""
    if year:  params_a.append(int(year))

    # Actual: material yang delivery_date sudah terisi (sudah tiba)
    actual_rows = query(f"""
        SELECT
            TO_CHAR(t.delivery_date::date, 'YYYY-MM') AS bulan,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE t.delivery_date IS NOT NULL
          AND t.po IS NOT NULL AND t.po != ''
          {'AND t.plant = %s' if plant else ''}
          {year_cond_a}
        GROUP BY bulan
        ORDER BY bulan
    """, params_a)

    plan_map   = {r["bulan"]: int(r["jumlah"] or 0) for r in plan_rows}
    actual_map = {r["bulan"]: int(r["jumlah"] or 0) for r in actual_rows}

    all_months = sorted(set(list(plan_map.keys()) + list(actual_map.keys())))

    # Kumulatif
    cum_plan = cum_act = 0
    monthly = []
    for m in all_months:
        p = plan_map.get(m, 0)
        a = actual_map.get(m, 0)
        cum_plan += p
        cum_act  += a
        monthly.append({
            "bulan":         m,
            "plan":          p,
            "actual":        a,
            "kum_plan":      cum_plan,
            "kum_actual":    cum_act,
        })

    return J({"monthly": monthly})


# ═══════════════════════════════════════════════════════════════
# PAGE 2 — Readiness Material (Actual Status)
# ═══════════════════════════════════════════════════════════════
@router.get("/readiness-material")
def readiness_material(request: Request, plant: str = ""):
    _require_admin(request)

    plant_cond = "AND t.plant = %s" if plant else ""
    params = [plant] if plant else []

    # Actual Status
    actual_rows = query(f"""
        SELECT
            CASE
                WHEN COALESCE(t.qty_stock, 0) >= COALESCE(t.qty_reqmts, 0)
                     AND COALESCE(t.qty_reqmts, 0) > 0
                     THEN 'Stock On Hand'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND COALESCE(t.qty_deliv, 0) >= COALESCE(t.qty_reqmts, 0)
                     AND COALESCE(t.qty_reqmts, 0) > 0
                     THEN 'PO-Material Telah Tiba'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND COALESCE(t.qty_deliv, 0) < COALESCE(t.qty_reqmts, 0)
                     THEN 'PO-Material Belum Tiba'
                WHEN t.pr IS NOT NULL AND t.pr != ''
                     AND (t.po IS NULL OR t.po = '')
                     THEN 'PR-Proses Pengadaan'
                ELSE 'Belum PR'
            END AS status_material,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE COALESCE(t.qty_reqmts, 0) > 0
          {plant_cond}
        GROUP BY status_material
        ORDER BY jumlah DESC
    """, params)

    total_actual = sum(int(r["jumlah"] or 0) for r in actual_rows)

    actual = [{
        "status":  r["status_material"],
        "jumlah":  int(r["jumlah"] or 0),
        "pct":     round(int(r["jumlah"] or 0) / total_actual * 100, 2) if total_actual else 0,
    } for r in actual_rows]

    # Prognosa Status — bandingkan delivery_date vs basic_start_date WO
    params_p = [plant] if plant else []
    prognosa_rows = query(f"""
        SELECT
            CASE
                WHEN COALESCE(t.qty_stock, 0) >= COALESCE(t.qty_reqmts, 0)
                     AND COALESCE(t.qty_reqmts, 0) > 0
                     THEN 'Stock On Hand'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND t.delivery_date IS NOT NULL
                     AND wo.basic_start_date IS NOT NULL
                     AND t.delivery_date::date < wo.basic_start_date::date
                     THEN 'PO-DT Sebelum MD'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND t.delivery_date IS NOT NULL
                     AND wo.basic_start_date IS NOT NULL
                     AND t.delivery_date::date = wo.basic_start_date::date
                     THEN 'PO-DT MD'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND t.delivery_date IS NOT NULL
                     AND wo.basic_start_date IS NOT NULL
                     AND t.delivery_date::date > wo.basic_start_date::date
                     THEN 'PO-DT Melebihi MD'
                WHEN t.po IS NOT NULL AND t.po != ''
                     THEN 'PO-DT Sebelum MD'
                WHEN t.pr IS NOT NULL AND t.pr != ''
                     AND (t.po IS NULL OR t.po = '')
                     AND wo.basic_start_date IS NOT NULL
                     AND (t.reqmts_date IS NULL OR t.reqmts_date::date <= wo.basic_start_date::date)
                     THEN 'PR-Prognosa DT sebelum MD'
                WHEN t.pr IS NOT NULL AND t.pr != ''
                     AND (t.po IS NULL OR t.po = '')
                     THEN 'PR-Prognosa DT Melebihi MD'
                ELSE 'Create PR'
            END AS prognosa_status,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        LEFT JOIN LATERAL (
            SELECT basic_start_date FROM work_order wo
            WHERE wo."order" = t."order"
            ORDER BY wo.id LIMIT 1
        ) wo ON TRUE
        WHERE COALESCE(t.qty_reqmts, 0) > 0
          {plant_cond}
        GROUP BY prognosa_status
        ORDER BY jumlah DESC
    """, params_p)

    total_prognosa = sum(int(r["jumlah"] or 0) for r in prognosa_rows)

    prognosa = [{
        "status": r["prognosa_status"],
        "jumlah": int(r["jumlah"] or 0),
        "pct":    round(int(r["jumlah"] or 0) / total_prognosa * 100, 2) if total_prognosa else 0,
    } for r in prognosa_rows]

    return J({
        "actual": {
            "total": total_actual,
            "items": actual,
        },
        "prognosa": {
            "total": total_prognosa,
            "items": prognosa,
        },
    })


# ═══════════════════════════════════════════════════════════════
# Plants list untuk filter
# ═══════════════════════════════════════════════════════════════
@router.get("/plants")
def dashboard_plants(request: Request):
    _require_admin(request)
    rows = query("SELECT plant_code, plant_name FROM plants ORDER BY plant_code")
    return J([{"code": r["plant_code"], "name": r["plant_name"]} for r in rows])
