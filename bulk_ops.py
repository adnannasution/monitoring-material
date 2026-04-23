"""
bulk_ops.py — Operasi bulk INSERT/UPDATE ke PostgreSQL menggunakan psycopg2 execute_values
Langsung kirim semua baris sekaligus — tidak perlu chunking, tetap cepat.
execute_values otomatis handle ribuan bahkan ratusan ribu baris dalam satu round-trip ke DB.
"""
import pandas as pd
from psycopg2.extras import execute_values
from database import get_conn, release_conn
from header_maps import normalize_taex, normalize_sap, normalize_order


def _n(v):
    """Konversi ke float atau None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    """Konversi ke string atau None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s else None


# ─── TAEX RESERVASI ──────────────────────────────────────────
def bulk_replace_taex(df: pd.DataFrame, mode: str = "replace") -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if mode == "replace":
                cur.execute("DELETE FROM taex_reservasi")

            rows = []
            for _, raw in df.iterrows():
                r = normalize_taex(raw.to_dict())
                rows.append((
                    _s(r.get("Plant")), _s(r.get("Equipment")), _s(r.get("Order")),
                    _s(r.get("Revision")), _s(r.get("Material")), _s(r.get("Itm")),
                    _s(r.get("Material_Description")), _n(r.get("Qty_Reqmts")) or 0,
                    _n(r.get("Qty_Stock")) or 0,
                    _s(r.get("PR")), _s(r.get("Item")), _n(r.get("Qty_PR")),
                    _s(r.get("Cost_Ctrs")),
                    _s(r.get("PO")), _s(r.get("PO_Date")), _n(r.get("Qty_Deliv")),
                    _s(r.get("Delivery_Date")),
                    _s(r.get("SLoc")), _s(r.get("Del")), _s(r.get("FIs")),
                    _s(r.get("Ict")), _s(r.get("PG")),
                    _s(r.get("Recipient")), _s(r.get("Unloading_point")),
                    _s(r.get("Reqmts_Date")),
                    _n(r.get("Qty_f_avail_check")), _n(r.get("Qty_Withdrawn")),
                    _s(r.get("UoM")), _s(r.get("GL_Acct")),
                    _n(r.get("Res_Price")), _n(r.get("Res_per")), _s(r.get("Res_Curr")),
                    _s(r.get("Reservno")),
                ))

            if mode == "append":
                sql = """
                    INSERT INTO taex_reservasi
                    (plant, equipment, "order", revision, material, itm,
                     material_description, qty_reqmts, qty_stock,
                     pr, item, qty_pr, cost_ctrs,
                     po, po_date, qty_deliv, delivery_date,
                     sloc, del, fis, ict, pg,
                     recipient, unloading_point, reqmts_date,
                     qty_f_avail_check, qty_withdrawn,
                     uom, gl_acct, res_price, res_per, res_curr, reservno)
                    VALUES %s
                    ON CONFLICT ("order", material, itm) DO UPDATE SET
                        plant=EXCLUDED.plant, equipment=EXCLUDED.equipment,
                        revision=EXCLUDED.revision, material_description=EXCLUDED.material_description,
                        qty_reqmts=EXCLUDED.qty_reqmts, qty_stock=EXCLUDED.qty_stock,
                        cost_ctrs=EXCLUDED.cost_ctrs, sloc=EXCLUDED.sloc,
                        del=EXCLUDED.del, fis=EXCLUDED.fis, ict=EXCLUDED.ict, pg=EXCLUDED.pg,
                        recipient=EXCLUDED.recipient, unloading_point=EXCLUDED.unloading_point,
                        reqmts_date=EXCLUDED.reqmts_date,
                        qty_f_avail_check=EXCLUDED.qty_f_avail_check,
                        qty_withdrawn=EXCLUDED.qty_withdrawn,
                        uom=EXCLUDED.uom, gl_acct=EXCLUDED.gl_acct,
                        res_price=EXCLUDED.res_price, res_per=EXCLUDED.res_per,
                        res_curr=EXCLUDED.res_curr, reservno=EXCLUDED.reservno,
                        updated_at=NOW()
                """
            else:
                sql = """
                    INSERT INTO taex_reservasi
                    (plant, equipment, "order", revision, material, itm,
                     material_description, qty_reqmts, qty_stock,
                     pr, item, qty_pr, cost_ctrs,
                     po, po_date, qty_deliv, delivery_date,
                     sloc, del, fis, ict, pg,
                     recipient, unloading_point, reqmts_date,
                     qty_f_avail_check, qty_withdrawn,
                     uom, gl_acct, res_price, res_per, res_curr, reservno)
                    VALUES %s
                """

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── PRISMA RESERVASI ─────────────────────────────────────────
def bulk_replace_prisma(df: pd.DataFrame) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM prisma_reservasi")
            sql = """
                INSERT INTO prisma_reservasi
                (plant, equipment, revision, "order", reservno, itm, material, material_description,
                 del, fis, ict, pg, recipient, unloading_point, reqmts_date,
                 qty_reqmts, uom, pr_prisma, item_prisma, qty_pr_prisma, qty_stock_onhand, code_kertas_kerja)
                VALUES %s
            """
            rows = []
            for _, raw in df.iterrows():
                r = raw.to_dict()
                rows.append((
                    _s(r.get("Plant")), _s(r.get("Equipment")), _s(r.get("Revision")),
                    _s(r.get("Order")), _s(r.get("Reservno")), _s(r.get("Itm")),
                    _s(r.get("Material")), _s(r.get("Material_Description")),
                    _s(r.get("Del")), _s(r.get("FIs")), _s(r.get("Ict")), _s(r.get("PG")),
                    _s(r.get("Recipient")), _s(r.get("Unloading_point")), _s(r.get("Reqmts_Date")),
                    _n(r.get("Qty_Reqmts")) or 0, _s(r.get("UoM")),
                    _s(r.get("PR_Prisma")), _s(r.get("Item_Prisma")), _n(r.get("Qty_PR_Prisma")),
                    _n(r.get("Qty_StockOnhand")), _s(r.get("CodeKertasKerja")),
                ))

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── SAP PR ──────────────────────────────────────────────────
def bulk_replace_pr(df: pd.DataFrame) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sap_pr")
            sql = """
                INSERT INTO sap_pr
                (plant, pr, item, material, material_description, d, r, pgr, s, tracking_no,
                 qty_pr, un, req_date, valn_price, pr_curr, pr_per, release_date, tracking)
                VALUES %s
            """
            rows = []
            for _, raw in df.iterrows():
                r = normalize_sap(raw.to_dict())
                rows.append((
                    _s(r.get("Plant")), _s(r.get("PR")), _s(r.get("Item")),
                    _s(r.get("Material")), _s(r.get("Material_Description")),
                    _s(r.get("D")), _s(r.get("R")), _s(r.get("PGr")),
                    _s(r.get("S")), _s(r.get("TrackingNo")),
                    _n(r.get("Qty_PR")), _s(r.get("Un")), _s(r.get("Req_Date")),
                    _n(r.get("Valn_price")), _s(r.get("PR_Curr")), _n(r.get("PR_Per")),
                    _s(r.get("Release_Date")), _s(r.get("Tracking")),
                ))

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── SAP PO ──────────────────────────────────────────────────
def bulk_replace_po(df: pd.DataFrame) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sap_po")
            sql = """
                INSERT INTO sap_po
                (plnt, purchreq, item, material, short_text, po, po_item, d, dci, pgr,
                 doc_date, po_quantity, qty_delivered, deliv_date, oun, net_price, crcy, per)
                VALUES %s
            """
            rows = []
            for _, raw in df.iterrows():
                r = raw.to_dict()
                rows.append((
                    _s(r.get("Plnt") or r.get("plnt")),
                    _s(r.get("Purchreq") or r.get("PurchReq")),
                    _s(r.get("Item") or r.get("item")),
                    _s(r.get("Material") or r.get("material")),
                    _s(r.get("Short_Text") or r.get("Short Text") or r.get("short_text")),
                    _s(r.get("PO") or r.get("po")),
                    _s(r.get("PO_Item") or r.get("Item1") or r.get("PO Item")),
                    _s(r.get("D") or r.get("d")),
                    _s(r.get("DCI") or r.get("dci")),
                    _s(r.get("PGr") or r.get("pgr")),
                    _s(r.get("Doc_Date") or r.get("PO Date") or r.get("Doc. Date")),
                    _n(r.get("PO_Quantity") or r.get("Ordered") or r.get("PO Quantity")),
                    _n(r.get("Qty_Delivered") or r.get("Qty Delivered")),
                    _s(r.get("Deliv_Date") or r.get("DelivDate") or r.get("Deliv. Date")),
                    _s(r.get("OUn") or r.get("Un")),
                    _n(r.get("Net_Price") or r.get("Net Price")),
                    _s(r.get("Crcy") or r.get("crcy")),
                    _n(r.get("Per") or r.get("per")),
                ))

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── KUMPULAN SUMMARY ─────────────────────────────────────────
def bulk_replace_kumpulan(df: pd.DataFrame) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kumpulan_summary")
            sql = """
                INSERT INTO kumpulan_summary
                (plant, equipment, revision, "order", reservno, itm, material, material_description,
                 qty_req, qty_stock, qty_pr, qty_to_pr, code_tracking)
                VALUES %s
            """
            rows = []
            for _, r in df.iterrows():
                rows.append((
                    _s(r.get("Plant")), _s(r.get("Equipment")), _s(r.get("Revision")),
                    _s(r.get("Order")), _s(r.get("Reservno")), _s(r.get("Itm")),
                    _s(r.get("Material")), _s(r.get("Material_Description")),
                    _n(r.get("Qty_Req")) or 0, _n(r.get("Qty_Stock")) or 0,
                    _n(r.get("Qty_PR")), _n(r.get("Qty_To_PR")),
                    _s(r.get("CodeTracking")),
                ))

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── WORK ORDER ──────────────────────────────────────────────
def bulk_replace_order(df: pd.DataFrame) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM work_order")
            sql = """
                INSERT INTO work_order
                (plant, "order", superior_order, notification, created_on, description, revision,
                 equipment, system_status, user_status, funct_location, location, wbs_ord_header,
                 cost_center, total_plan_cost, total_act_cost, planner_group, main_work_ctr,
                 entry_by, changed_by, basic_start_date, basic_finish_date, actual_release)
                VALUES %s
            """
            rows = []
            for _, raw in df.iterrows():
                r = normalize_order(raw.to_dict())
                rows.append((
                    _s(r.get("Plant")), _s(r.get("Order")),
                    _s(r.get("Superior_Order")), _s(r.get("Notification")),
                    _s(r.get("Created_On")), _s(r.get("Description")),
                    _s(r.get("Revision")), _s(r.get("Equipment")),
                    _s(r.get("System_Status")), _s(r.get("User_Status")),
                    _s(r.get("FunctLocation")), _s(r.get("Location")),
                    _s(r.get("WBS_Ord_header")), _s(r.get("CostCenter")),
                    _n(r.get("Total_Plan_Cost")), _n(r.get("Total_Act_Cost")),
                    _s(r.get("Planner_Group")), _s(r.get("MainWorkCtr")),
                    _s(r.get("Entry_by")), _s(r.get("Changed_by")),
                    _s(r.get("Basic_start_date")), _s(r.get("Basic_finish_date")),
                    _s(r.get("Actual_Release")),
                ))

            execute_values(cur, sql, rows)

        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)
