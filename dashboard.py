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

    plant_filter = "AND wo.plant = %s" if plant else ""
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
                wo.plant,
                wo.equipment_no,
                BOOL_AND(
                    CASE WHEN COALESCE(or_.total_mat,0) > 0
                              AND or_.ready_mat = or_.total_mat
                         THEN TRUE ELSE FALSE END
                ) AS equipment_ready,
                COUNT(DISTINCT wo."order") AS total_wo,
                SUM(COALESCE(or_.total_mat,0)) AS total_mat,
                SUM(COALESCE(or_.ready_mat,0)) AS ready_mat
            FROM vw_joblist_wo wo
            LEFT JOIN order_ready or_ ON or_."order" = wo."order"
            WHERE wo.equipment_no IS NOT NULL
              {plant_filter}
            GROUP BY wo.plant, wo.equipment_no
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
# PAGE 1 — Readiness Material & Jasa per Bulan
# ═══════════════════════════════════════════════════════════════
@router.get("/readiness-monthly")
def readiness_monthly(request: Request, plant: str = "", year: str = ""):
    _require_admin(request)

    plant_cond = "AND t.plant = %s" if plant else ""
    year_cond  = "AND EXTRACT(YEAR FROM t.reqmts_date::date) = %s" if year else ""
    params_p   = []
    if plant: params_p.append(plant)
    if year:  params_p.append(int(year))

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

    # Top project per bulan
    params_proj = []
    if plant: params_proj.append(plant)
    proj_rows = query(f"""
        SELECT
            TO_CHAR(t.reqmts_date::date, 'YYYY-MM') AS bulan,
            jld.project_number,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        LEFT JOIN vw_joblist_detail jld ON jld.equipment_no = (
            SELECT wo.equipment_no FROM vw_joblist_wo wo
            WHERE wo."order" = t."order" LIMIT 1
        )
        WHERE t.reqmts_date IS NOT NULL
          AND jld.project_number IS NOT NULL
          {'AND t.plant = %s' if plant else ''}
        GROUP BY bulan, jld.project_number
        ORDER BY bulan, jumlah DESC
    """, params_proj)

    from collections import defaultdict
    proj_by_month = defaultdict(list)
    for r in proj_rows:
        b = r["bulan"]
        if len(proj_by_month[b]) < 3:
            proj_by_month[b].append({
                "project": r["project_number"],
                "jumlah":  int(r["jumlah"] or 0)
            })

    cum_plan = cum_act = 0
    monthly = []
    for m in all_months:
        p = plan_map.get(m, 0)
        a = actual_map.get(m, 0)
        cum_plan += p
        cum_act  += a
        monthly.append({
            "bulan":        m,
            "plan":         p,
            "actual":       a,
            "kum_plan":     cum_plan,
            "kum_actual":   cum_act,
            "top_projects": proj_by_month.get(m, []),
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
        "actual":   {"total": total_actual,   "items": actual},
        "prognosa": {"total": total_prognosa, "items": prognosa},
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
# ═══════════════════════════════════════════════════════════════
DRILLDOWN_BASE = """
    WITH wo_ready AS (
        SELECT
            wo."order",
            wo.equipment_no,
            wo.disiplin        AS wo_disiplin,
            wo.plant,
            jld.project_number,
            jld.joblist_detail_desc  AS jd_desc,
            jld.no_joblist           AS no_joblist,
            jld.joblist_description  AS jl_desc,
            jld.no_document          AS jd_id,
            jld.area_name,
            jld.area_alias_name,
            jld.unit_name,
            jld.unit_alias_name,
            CASE
                WHEN COUNT(t.id) = 0 THEN FALSE
                WHEN SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                               AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                              THEN 1 ELSE 0 END) = COUNT(t.id)
                THEN TRUE ELSE FALSE
            END AS wo_ready,
            COUNT(t.id) AS total_mat,
            SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                      AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                     THEN 1 ELSE 0 END) AS ready_mat
        FROM vw_joblist_wo wo
        LEFT JOIN vw_joblist_detail jld ON jld.equipment_no = wo.equipment_no
        LEFT JOIN taex_reservasi    t   ON t."order"        = wo."order"
        WHERE 1=1
        {extra_where}
        GROUP BY
            wo."order", wo.equipment_no, wo.disiplin, wo.plant,
            jld.project_number, jld.joblist_detail_desc,
            jld.no_joblist, jld.joblist_description, jld.no_document,
            jld.area_name, jld.area_alias_name,
            jld.unit_name, jld.unit_alias_name
    )
"""

def _drilldown_query(level, plant="", project_id="", area_id="", unit_id="",
                     eq_id="", jl_id="", jd_id=""):
    extra_conds = []
    params = []
    if plant:      extra_conds.append("wo.plant = %s");             params.append(plant)
    if project_id: extra_conds.append("jld.project_number = %s");  params.append(project_id)
    if area_id:    extra_conds.append("jld.area_name = %s");        params.append(area_id)
    if unit_id:    extra_conds.append("jld.unit_name = %s");        params.append(unit_id)
    if eq_id:      extra_conds.append("wo.equipment_no = %s");      params.append(eq_id)
    if jl_id:      extra_conds.append("jld.no_joblist = %s");       params.append(jl_id)
    if jd_id:      extra_conds.append("jld.no_document = %s");      params.append(jd_id)

    extra_where = ("AND " + " AND ".join(extra_conds)) if extra_conds else ""
    base = DRILLDOWN_BASE.format(extra_where=extra_where)

    GROUP_COLS = {
        "project":   ("project_number", "project_number", "project_number"),
        "area":      ("area_name",       "area_name",      "area_alias_name"),
        "unit":      ("unit_name",       "unit_name",      "unit_alias_name"),
        "equipment": ("equipment_no",    "equipment_no",   "wo_disiplin"),
        "joblist":   ("no_joblist",      "no_joblist",     "jl_desc"),
        "jobdetail": ("jd_id",           "jd_id",          "jd_desc"),
        "wo":        ('"order"',         '"order"',        '"order"'),
    }
    id_col, name_col, desc_col = GROUP_COLS[level]

    sql = f"""
        {base}
        SELECT
            {id_col}    AS id,
            {name_col}  AS name,
            {desc_col}  AS description,
            COUNT(DISTINCT "order") AS total_wo,
            SUM(CASE WHEN wo_ready THEN 1 ELSE 0 END) AS ready_wo,
            SUM(CASE WHEN NOT wo_ready THEN 1 ELSE 0 END) AS not_ready_wo,
            ROUND(
                SUM(CASE WHEN wo_ready THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(DISTINCT "order"), 0), 1
            ) AS pct_ready,
            SUM(total_mat)  AS total_mat,
            SUM(ready_mat)  AS ready_mat
        FROM wo_ready
        WHERE {id_col} IS NOT NULL AND {id_col}::text != ''
        GROUP BY {id_col}, {name_col}, {desc_col}
        ORDER BY pct_ready DESC, name
    """
    rows = query(sql, params)
    return [{
        "id":          str(r["id"]          or ""),
        "name":        str(r["name"]        or "—"),
        "description": str(r["description"] or ""),
        "total_wo":    int(r["total_wo"]    or 0),
        "ready_wo":    int(r["ready_wo"]    or 0),
        "not_ready_wo":int(r["not_ready_wo"]or 0),
        "pct_ready":   float(r["pct_ready"] or 0),
        "total_mat":   int(r["total_mat"]   or 0),
        "ready_mat":   int(r["ready_mat"]   or 0),
    } for r in rows if r["id"]]


@router.get("/debug/plant")
def debug_plant(request: Request):
    _require_admin(request)
    rows = query("SELECT DISTINCT plant FROM vw_joblist_wo WHERE plant IS NOT NULL ORDER BY plant LIMIT 20")
    plants_db = query("SELECT plant_code, plant_name FROM plants ORDER BY plant_code")
    return {"vw_plants": [r["plant"] for r in rows], "db_plants": [r["plant_code"] for r in plants_db]}


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
# DETAIL PANEL
# ═══════════════════════════════════════════════════════════════
@router.get("/drilldown/detail")
def drilldown_detail(request: Request, level: str = "equipment",
                     item_id: str = "", plant: str = ""):
    _require_admin(request)
    if not item_id:
        raise HTTPException(400, "item_id wajib")

    FILTER_MAP = {
        "project":   "jld.project_number = %s",
        "area":      "jld.area_name = %s",
        "unit":      "jld.unit_name = %s",
        "equipment": "wo.equipment_no = %s",
        "joblist":   "jld.no_joblist = %s",
        "jobdetail": "jld.no_document = %s",
        "wo":        'wo."order" = %s',
    }
    if level not in FILTER_MAP:
        raise HTTPException(400, "level tidak valid")

    conds = [FILTER_MAP[level]]
    params = [item_id]
    if plant:
        conds.append("wo.plant = %s"); params.append(plant)

    where = " AND ".join(conds)
    rows = query(f"""
        SELECT
            jld.project_number, jld.area_name, jld.unit_name,
            wo.equipment_no, wo.disiplin AS eq_desc,
            jld.no_joblist, jld.joblist_description AS jl_desc,
            jld.joblist_detail_desc AS jd_desc,
            jld.no_document AS no_joblist_detail,
            jld.is_jasa, jld.is_material,
            jld.planning_jasa_status, jld.planning_material_status,
            wo."order", wo.system_status, wo.planner_group,
            COUNT(t.id) AS total_mat,
            SUM(CASE WHEN COALESCE(t.qty_reqmts,0) > 0
                      AND COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                     THEN 1 ELSE 0 END) AS ready_mat,
            SUM(COALESCE(t.qty_reqmts,0)) AS sum_reqmts,
            SUM(COALESCE(t.qty_deliv,0))  AS sum_deliv
        FROM vw_joblist_wo wo
        LEFT JOIN vw_joblist_detail jld ON jld.equipment_no = wo.equipment_no
        LEFT JOIN taex_reservasi    t   ON t."order"        = wo."order"
        WHERE {where}
        GROUP BY
            jld.project_number, jld.area_name, jld.unit_name,
            wo.equipment_no, wo.disiplin,
            jld.no_joblist, jld.joblist_description,
            jld.joblist_detail_desc, jld.no_document,
            jld.is_jasa, jld.is_material,
            jld.planning_jasa_status, jld.planning_material_status,
            wo."order", wo.system_status, wo.planner_group
        ORDER BY wo.equipment_no, jld.no_joblist, jld.joblist_detail_desc, wo."order"
    """, params)

    def _mat_status(total, ready, reqmts, deliv):
        if total == 0: return "N/R"
        if ready == total: return "READY"
        return "NOT READY"

    def _jasa_status(status):
        s = str(status or "").strip().lower()
        if "planning complete" in s or "sudah sp" in s or "sp3mk" in s: return "READY"
        if s and s not in ("not planned", "belum", ""): return "ON PROCESS"
        return "N/R"

    result = []
    for r in rows:
        total = int(r["total_mat"] or 0)
        ready = int(r["ready_mat"] or 0)
        mat_status  = _mat_status(total, ready, r["sum_reqmts"], r["sum_deliv"])
        jasa_status = _jasa_status(r["planning_jasa_status"])
        wo_ready    = mat_status == "READY" and (not r["is_jasa"] or jasa_status == "READY")
        result.append({
            "project_number":    r["project_number"],
            "area_name":         r["area_name"],
            "unit_name":         r["unit_name"],
            "equipment_no":      r["equipment_no"],
            "eq_desc":           r["eq_desc"],
            "no_joblist":        r["no_joblist"],
            "jl_desc":           r["jl_desc"],
            "no_joblist_detail": r["no_joblist_detail"],
            "jd_desc":           r["jd_desc"],
            "order":             r["order"],
            "system_status":     r["system_status"],
            "planner_group":     r["planner_group"],
            "is_jasa":           bool(r["is_jasa"]),
            "is_material":       bool(r["is_material"]),
            "planning_jasa_status": r["planning_jasa_status"],
            "total_mat":         total,
            "ready_mat":         ready,
            "sum_reqmts":        float(r["sum_reqmts"] or 0),
            "sum_deliv":         float(r["sum_deliv"]  or 0),
            "mat_status":        mat_status,
            "jasa_status":       jasa_status,
            "wo_ready":          wo_ready,
        })

    if result:
        r0 = result[0]
        titles = {"equipment": r0["equipment_no"], "joblist": r0["no_joblist"],
                  "area": r0["area_name"], "unit": r0["unit_name"],
                  "project": r0["project_number"]}
        header = {"title": titles.get(level, item_id), "subtitle": r0.get("eq_desc","") if level=="equipment" else ""}
    else:
        header = {"title": item_id, "subtitle": ""}

    total_wo  = len(set(r["order"] for r in result))
    ready_wo  = len({r["order"] for r in result if r["wo_ready"]})
    pct_ready = round(ready_wo / total_wo * 100, 1) if total_wo else 0

    return J({
        "header":  header,
        "level":   level,
        "item_id": item_id,
        "summary": {
            "total_wo":  total_wo,
            "ready_wo":  ready_wo,
            "not_ready": total_wo - ready_wo,
            "pct_ready": pct_ready,
        },
        "rows": result,
    })


# ═══════════════════════════════════════════════════════════════
# PER PROJECT — Tab 4
# ═══════════════════════════════════════════════════════════════

@router.get("/projects")
def get_projects(request: Request):
    """List semua project untuk dropdown filter."""
    _require_admin(request)
    rows = query("""
        SELECT DISTINCT project_number
        FROM vw_joblist_detail
        WHERE project_number IS NOT NULL AND project_number != ''
        ORDER BY project_number
    """)
    return J([r["project_number"] for r in rows])


@router.get("/project-equipment")
def project_equipment(request: Request, project_number: str = ""):
    """Readiness equipment untuk 1 project."""
    _require_admin(request)
    if not project_number:
        return J({"summary": {"total_equipment":0,"ready":0,"not_ready":0,"pct_ready":0}, "by_area": []})

    rows = query("""
       WITH equipment_list AS (
    SELECT
        wo.equipment_no,
        wo.plant,
        jld.area_name,
        jld.unit_name
    FROM vw_joblist_wo wo
    LEFT JOIN vw_joblist_detail jld ON jld.equipment_no = wo.equipment_no
    WHERE jld.project_number = %s
      AND wo.equipment_no IS NOT NULL
),
wo_per_eq AS (
    SELECT DISTINCT
        el.equipment_no,
        el.area_name,
        el.plant,
        wo."order"
    FROM equipment_list el
    LEFT JOIN vw_joblist_wo wo ON wo.equipment_no = el.equipment_no
),
        order_ready AS (
            -- Cek readiness tiap WO dari taex_reservasi
            SELECT t."order",
                   COUNT(*) AS total_mat,
                   SUM(CASE WHEN COALESCE(t.qty_deliv,0) >= t.qty_reqmts
                                 AND t.qty_reqmts > 0 THEN 1 ELSE 0 END) AS ready_mat
            FROM taex_reservasi t
            WHERE t."order" IN (SELECT "order" FROM wo_per_eq WHERE "order" IS NOT NULL)
            GROUP BY t."order"
        ),
        eq_ready AS (
            -- Equipment ready = semua WO-nya ready
            SELECT
                we.equipment_no,
                we.area_name,
                we.plant,
                BOOL_AND(
                    CASE WHEN we."order" IS NULL THEN FALSE
                         WHEN COALESCE(or_.total_mat,0) > 0
                              AND or_.ready_mat = or_.total_mat THEN TRUE
                         ELSE FALSE END
                ) AS equipment_ready
            FROM wo_per_eq we
            LEFT JOIN order_ready or_ ON or_."order" = we."order"
            GROUP BY we.equipment_no, we.area_name, we.plant
        )
        SELECT
            area_name,
            COUNT(*) AS total_equipment,
            SUM(CASE WHEN equipment_ready THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN NOT equipment_ready THEN 1 ELSE 0 END) AS not_ready,
            ROUND(
                SUM(CASE WHEN equipment_ready THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(*), 0), 2
            ) AS pct_ready
        FROM eq_ready
        GROUP BY area_name
        ORDER BY area_name
    """, [project_number])

    total_eq  = sum(int(r["total_equipment"] or 0) for r in rows)
    total_rdy = sum(int(r["ready"] or 0) for r in rows)
    total_nrd = sum(int(r["not_ready"] or 0) for r in rows)

    return J({
        "summary": {
            "total_equipment": total_eq,
            "ready":     total_rdy,
            "not_ready": total_nrd,
            "pct_ready": round(total_rdy / total_eq * 100, 2) if total_eq else 0,
        },
        "by_area": [{
            "area_name":       r["area_name"] or "—",
            "total_equipment": int(r["total_equipment"] or 0),
            "ready":           int(r["ready"] or 0),
            "not_ready":       int(r["not_ready"] or 0),
            "pct_ready":       float(r["pct_ready"] or 0),
        } for r in rows],
    })


@router.get("/project-material")
def project_material(request: Request, project_number: str = ""):
    """Readiness material untuk 1 project."""
    _require_admin(request)
    if not project_number:
        return J({"actual": {"total":0,"items":[]}, "prognosa": {"total":0,"items":[]}})

    # Sub-query orders milik project ini
    order_sub = """
        SELECT DISTINCT wo."order"
        FROM vw_joblist_wo wo
        JOIN vw_joblist_detail jld ON jld.equipment_no = wo.equipment_no
        WHERE jld.project_number = %s
    """

    actual_rows = query(f"""
        SELECT
            CASE
                WHEN COALESCE(t.qty_stock,0) >= COALESCE(t.qty_reqmts,0)
                     AND COALESCE(t.qty_reqmts,0) > 0
                     THEN 'Stock On Hand'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND COALESCE(t.qty_deliv,0) >= COALESCE(t.qty_reqmts,0)
                     AND COALESCE(t.qty_reqmts,0) > 0
                     THEN 'PO-Material Telah Tiba'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND COALESCE(t.qty_deliv,0) < COALESCE(t.qty_reqmts,0)
                     THEN 'PO-Material Belum Tiba'
                WHEN t.pr IS NOT NULL AND t.pr != ''
                     AND (t.po IS NULL OR t.po = '')
                     THEN 'PR-Proses Pengadaan'
                ELSE 'Belum PR'
            END AS status_material,
            COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE COALESCE(t.qty_reqmts,0) > 0
          AND t."order" IN ({order_sub})
        GROUP BY status_material
        ORDER BY jumlah DESC
    """, [project_number])

    total_actual = sum(int(r["jumlah"] or 0) for r in actual_rows)
    actual = [{
        "status": r["status_material"],
        "jumlah": int(r["jumlah"] or 0),
        "pct":    round(int(r["jumlah"] or 0) / total_actual * 100, 2) if total_actual else 0,
    } for r in actual_rows]

    prognosa_rows = query(f"""
        SELECT
            CASE
                WHEN COALESCE(t.qty_stock,0) >= COALESCE(t.qty_reqmts,0)
                     AND COALESCE(t.qty_reqmts,0) > 0
                     THEN 'Stock On Hand'
                WHEN t.po IS NOT NULL AND t.po != ''
                     AND t.delivery_date IS NOT NULL
                     AND wo.basic_start_date IS NOT NULL
                     AND t.delivery_date::date < wo.basic_start_date::date
                     THEN 'PO-DT Sebelum MD'
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
        WHERE COALESCE(t.qty_reqmts,0) > 0
          AND t."order" IN ({order_sub})
        GROUP BY prognosa_status
        ORDER BY jumlah DESC
    """, [project_number])

    total_prognosa = sum(int(r["jumlah"] or 0) for r in prognosa_rows)
    prognosa = [{
        "status": r["prognosa_status"],
        "jumlah": int(r["jumlah"] or 0),
        "pct":    round(int(r["jumlah"] or 0) / total_prognosa * 100, 2) if total_prognosa else 0,
    } for r in prognosa_rows]

    return J({
        "actual":   {"total": total_actual,   "items": actual},
        "prognosa": {"total": total_prognosa, "items": prognosa},
    })


@router.get("/project-monthly")
def project_monthly(request: Request, project_number: str = ""):
    """Readiness material per bulan untuk 1 project."""
    _require_admin(request)
    if not project_number:
        return J({"monthly": []})

    order_sub = """
        SELECT DISTINCT wo."order"
        FROM vw_joblist_wo wo
        JOIN vw_joblist_detail jld ON jld.equipment_no = wo.equipment_no
        WHERE jld.project_number = %s
    """

    plan_rows = query(f"""
        SELECT TO_CHAR(t.reqmts_date::date, 'YYYY-MM') AS bulan, COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE t.reqmts_date IS NOT NULL
          AND t."order" IN ({order_sub})
        GROUP BY bulan ORDER BY bulan
    """, [project_number])

    actual_rows = query(f"""
        SELECT TO_CHAR(t.delivery_date::date, 'YYYY-MM') AS bulan, COUNT(*) AS jumlah
        FROM taex_reservasi t
        WHERE t.delivery_date IS NOT NULL
          AND t.po IS NOT NULL AND t.po != ''
          AND t."order" IN ({order_sub})
        GROUP BY bulan ORDER BY bulan
    """, [project_number])

    plan_map   = {r["bulan"]: int(r["jumlah"] or 0) for r in plan_rows}
    actual_map = {r["bulan"]: int(r["jumlah"] or 0) for r in actual_rows}
    all_months = sorted(set(list(plan_map.keys()) + list(actual_map.keys())))

    cum_plan = cum_act = 0
    monthly = []
    for m in all_months:
        p = plan_map.get(m, 0)
        a = actual_map.get(m, 0)
        cum_plan += p
        cum_act  += a
        monthly.append({"bulan": m, "plan": p, "actual": a,
                        "kum_plan": cum_plan, "kum_actual": cum_act})

    return J({"monthly": monthly})