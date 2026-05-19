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


# ═══════════════════════════════════════════════════════════════
# DRILL-DOWN READINESS
# Hierarki: project → area → unit → equipment → joblist → jobdetail → wo
# Readiness selalu dihitung dari WO (qty_deliv >= qty_reqmts)
# ═══════════════════════════════════════════════════════════════

DRILLDOWN_BASE = """
    WITH wo_ready AS (
        SELECT
            wo."order",
            jd.id                         AS jd_id,
            jl.id                         AS jl_id,
            jl.no_joblist,
            jl.joblist_description        AS jl_desc,
            eq.id                         AS eq_id,
            eq.equipment_no,
            eq.description_of_technical_object AS eq_desc,
            u.id                          AS unit_id,
            u.unit_name,
            u.unit_alias_name,
            a.id                          AS area_id,
            a.area_name,
            p.id                          AS project_id,
            p.project_number,
            p.description                 AS project_desc,
            jl.plant,
            jd.no_joblist_detail,
            jd.joblist_detail_description AS jd_desc,
            -- WO ready: semua material sudah delivered
            CASE
                WHEN COUNT(t.id) = 0 THEN FALSE
                WHEN SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                               AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                              THEN 1 ELSE 0 END) = COUNT(t.id)
                THEN TRUE ELSE FALSE
            END AS wo_ready,
            COUNT(t.id)          AS total_mat,
            SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                      AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                     THEN 1 ELSE 0 END) AS ready_mat
        FROM job_detail_work_order wo
        JOIN job_detail     jd  ON wo.joblist_detail_id = jd.id
        JOIN job_list       jl  ON jd.joblist_id        = jl.id
        JOIN project        p   ON jl.project_id        = p.id
        LEFT JOIN equipment_taex eq ON jl.equipment_id  = eq.id
        LEFT JOIN job_unit       u  ON eq.unit_id       = u.id
        LEFT JOIN job_area       a  ON u.area_id        = a.id
        LEFT JOIN taex_reservasi t  ON t."order"        = wo."order"
        WHERE wo.is_deleted = 0
        {extra_where}
        GROUP BY
            wo."order", jd.id, jl.id, jl.no_joblist, jl.joblist_description,
            eq.id, eq.equipment_no, eq.description_of_technical_object,
            u.id, u.unit_name, u.unit_alias_name,
            a.id, a.area_name,
            p.id, p.project_number, p.description,
            jl.plant, jd.no_joblist_detail, jd.joblist_detail_description
    )
"""

def _drilldown_query(level, plant="", project_id="", area_id="", unit_id="",
                     eq_id="", jl_id="", jd_id=""):
    """Return rows for the given drill-down level."""
    extra_conds = []
    params = []

    if plant:
        extra_conds.append("jl.plant = %s"); params.append(plant)
    if project_id:
        extra_conds.append("p.id = %s"); params.append(project_id)
    if area_id:
        extra_conds.append("a.id = %s"); params.append(area_id)
    if unit_id:
        extra_conds.append("u.id = %s"); params.append(unit_id)
    if eq_id:
        extra_conds.append("eq.id = %s"); params.append(eq_id)
    if jl_id:
        extra_conds.append("jl.id = %s"); params.append(jl_id)
    if jd_id:
        extra_conds.append("jd.id = %s"); params.append(jd_id)

    extra_where = ("AND " + " AND ".join(extra_conds)) if extra_conds else ""
    base = DRILLDOWN_BASE.format(extra_where=extra_where)

    GROUP_COLS = {
        "project":   ("project_id", "project_number", "project_desc"),
        "area":      ("area_id",    "area_name",       "area_name"),
        "unit":      ("unit_id",    "unit_name",       "unit_alias_name"),
        "equipment": ("eq_id",      "equipment_no",    "eq_desc"),
        "joblist":   ("jl_id",      "no_joblist",      "jl_desc"),
        "jobdetail": ("jd_id",      "no_joblist_detail","jd_desc"),
        "wo":        ('"order"',    '"order"',         '"order"'),
    }

    id_col, name_col, desc_col = GROUP_COLS[level]

    sql = f"""
        {base}
        SELECT
            {id_col}          AS id,
            {name_col}        AS name,
            {desc_col}        AS description,
            COUNT(*)          AS total_wo,
            SUM(CASE WHEN wo_ready THEN 1 ELSE 0 END) AS ready_wo,
            SUM(CASE WHEN NOT wo_ready THEN 1 ELSE 0 END) AS not_ready_wo,
            ROUND(
                SUM(CASE WHEN wo_ready THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(*), 0), 1
            ) AS pct_ready,
            SUM(total_mat)    AS total_mat,
            SUM(ready_mat)    AS ready_mat
        FROM wo_ready
        GROUP BY {id_col}, {name_col}, {desc_col}
        ORDER BY pct_ready DESC, name
    """
    rows = query(sql, params)
    return [{
        "id":          str(r["id"] or ""),
        "name":        str(r["name"] or "—"),
        "description": str(r["description"] or ""),
        "total_wo":    int(r["total_wo"]    or 0),
        "ready_wo":    int(r["ready_wo"]    or 0),
        "not_ready_wo":int(r["not_ready_wo"]or 0),
        "pct_ready":   float(r["pct_ready"] or 0),
        "total_mat":   int(r["total_mat"]   or 0),
        "ready_mat":   int(r["ready_mat"]   or 0),
    } for r in rows]


@router.get("/drilldown")
def drilldown(
    request: Request,
    level:      str = "project",
    plant:      str = "",
    project_id: str = "",
    area_id:    str = "",
    unit_id:    str = "",
    eq_id:      str = "",
    jl_id:      str = "",
    jd_id:      str = "",
):
    _require_admin(request)
    VALID = ["project","area","unit","equipment","joblist","jobdetail","wo"]
    if level not in VALID:
        raise HTTPException(400, f"level harus salah satu dari: {VALID}")
    rows = _drilldown_query(
        level, plant=plant, project_id=project_id,
        area_id=area_id, unit_id=unit_id,
        eq_id=eq_id, jl_id=jl_id, jd_id=jd_id
    )
    return J({"level": level, "data": rows})


# ═══════════════════════════════════════════════════════════════
# DETAIL PANEL — detail readiness per item (untuk modal/side panel)
# Bisa dipanggil dari level manapun
# ═══════════════════════════════════════════════════════════════
@router.get("/drilldown/detail")
def drilldown_detail(
    request: Request,
    level:      str = "equipment",
    item_id:    str = "",
    plant:      str = "",
):
    """
    Return detail job per item — untuk ditampilkan di modal.
    Setiap baris = 1 job detail, dengan status jasa & material.
    """
    _require_admin(request)

    if not item_id:
        raise HTTPException(400, "item_id wajib")

    FILTER_MAP = {
        "project":   "p.id = %s",
        "area":      "a.id = %s",
        "unit":      "u.id = %s",
        "equipment": "eq.id = %s",
        "joblist":   "jl.id = %s",
        "jobdetail": "jd.id = %s",
        "wo":        'wo."order" = %s',
    }
    if level not in FILTER_MAP:
        raise HTTPException(400, "level tidak valid")

    conds = [FILTER_MAP[level], "wo.is_deleted = 0"]
    params = [item_id]
    if plant:
        conds.append("jl.plant = %s"); params.append(plant)

    where = " AND ".join(conds)

    rows = query(f"""
        SELECT
            p.project_number,
            a.area_name,
            u.unit_name,
            eq.equipment_no,
            eq.description_of_technical_object AS eq_desc,
            jl.no_joblist,
            jl.joblist_description             AS jl_desc,
            jd.no_joblist_detail,
            jd.joblist_detail_description      AS jd_desc,
            jd.is_jasa,
            jd.is_material,
            jd.planning_jasa_status_id,
            jd.planning_material_status_id,
            wo."order",
            wo.system_status,
            wo.planner_group,
            -- Readiness material dari taex
            COUNT(t.id)           AS total_mat,
            SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                      AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                     THEN 1 ELSE 0 END) AS ready_mat,
            SUM(COALESCE(t.qty_reqmts,0)) AS sum_reqmts,
            SUM(COALESCE(t.qty_deliv,0))  AS sum_deliv
        FROM job_detail_work_order wo
        JOIN job_detail     jd  ON wo.joblist_detail_id = jd.id
        JOIN job_list       jl  ON jd.joblist_id        = jl.id
        JOIN project        p   ON jl.project_id        = p.id
        LEFT JOIN equipment_taex eq ON jl.equipment_id  = eq.id
        LEFT JOIN job_unit       u  ON eq.unit_id       = u.id
        LEFT JOIN job_area       a  ON u.area_id        = a.id
        LEFT JOIN taex_reservasi t  ON t."order"        = wo."order"
        WHERE {where}
        GROUP BY
            p.project_number, a.area_name, u.unit_name,
            eq.equipment_no, eq.description_of_technical_object,
            jl.no_joblist, jl.joblist_description,
            jd.no_joblist_detail, jd.joblist_detail_description,
            jd.is_jasa, jd.is_material,
            jd.planning_jasa_status_id, jd.planning_material_status_id,
            wo."order", wo.system_status, wo.planner_group
        ORDER BY eq.equipment_no, jl.no_joblist, jd.no_joblist_detail, wo."order"
    """, params)

    def _mat_status(total, ready, reqmts, deliv):
        if total == 0: return "N/R"
        if ready == total: return "READY"
        return "NOT READY"

    def _jasa_status(planning_jasa_status_id):
        # planning_jasa_status_id: 3 = Sudah SP/SP3MK = READY
        if str(planning_jasa_status_id or "") in ("3",): return "READY"
        if planning_jasa_status_id: return "ON PROCESS"
        return "N/R"

    result = []
    for r in rows:
        total = int(r["total_mat"] or 0)
        ready = int(r["ready_mat"] or 0)
        mat_status  = _mat_status(total, ready, r["sum_reqmts"], r["sum_deliv"])
        jasa_status = _jasa_status(r["planning_jasa_status_id"])
        wo_ready = mat_status == "READY" and (not r["is_jasa"] or jasa_status == "READY")

        result.append({
            "project_number": r["project_number"],
            "area_name":      r["area_name"],
            "unit_name":      r["unit_name"],
            "equipment_no":   r["equipment_no"],
            "eq_desc":        r["eq_desc"],
            "no_joblist":     r["no_joblist"],
            "jl_desc":        r["jl_desc"],
            "no_joblist_detail": r["no_joblist_detail"],
            "jd_desc":        r["jd_desc"],
            "order":          r["order"],
            "system_status":  r["system_status"],
            "planner_group":  r["planner_group"],
            "is_jasa":        bool(r["is_jasa"]),
            "is_material":    bool(r["is_material"]),
            "total_mat":      total,
            "ready_mat":      ready,
            "sum_reqmts":     float(r["sum_reqmts"] or 0),
            "sum_deliv":      float(r["sum_deliv"]  or 0),
            "mat_status":     mat_status,
            "jasa_status":    jasa_status,
            "wo_ready":       wo_ready,
        })

    # Header info
    header = {}
    if result:
        r0 = result[0]
        if level == "equipment":
            header = {"title": r0["equipment_no"], "subtitle": r0["eq_desc"] or ""}
        elif level == "joblist":
            header = {"title": r0["no_joblist"], "subtitle": r0["jl_desc"] or ""}
        elif level == "area":
            header = {"title": r0["area_name"], "subtitle": ""}
        elif level == "unit":
            header = {"title": r0["unit_name"], "subtitle": ""}
        elif level == "project":
            header = {"title": r0["project_number"], "subtitle": ""}
        else:
            header = {"title": item_id, "subtitle": ""}

    total_wo    = len(set(r["order"] for r in result))
    ready_wo    = len({r["order"] for r in result if r["wo_ready"]})
    pct_ready   = round(ready_wo / total_wo * 100, 1) if total_wo else 0

    return J({
        "header":     header,
        "level":      level,
        "item_id":    item_id,
        "summary": {
            "total_wo":  total_wo,
            "ready_wo":  ready_wo,
            "not_ready": total_wo - ready_wo,
            "pct_ready": pct_ready,
        },
        "rows": result,
    })