# monitoring-material

PRISMA · TA-ex System — backend FastAPI + dashboard untuk monitoring material
(TA-ex, PR, PO, Work Order, Joblist).

## Public API

Endpoint publik ditujukan untuk konsumsi eksternal (mis. Power BI, skrip
analisa). Semua endpoint publik berada di bawah prefix `/api/public/`.

### Autentikasi

Sertakan `PUBLIC_API_KEY` melalui salah satu cara:

- Header: `x-api-key: <PUBLIC_API_KEY>`
- Header: `Authorization: Bearer <PUBLIC_API_KEY>`
- Query param: `?api_key=<PUBLIC_API_KEY>`

`PUBLIC_API_KEY` diset di environment variable server. Permintaan tanpa key
atau dengan key salah akan ditolak (HTTP 4xx).

Daftar endpoint publik yang tersedia bisa dilihat di `GET /api/public/info`.

---

### `GET /api/public/tracking.csv` — export seluruh data tracking (CSV)

Mengembalikan **seluruh baris** tracking (TA-ex + SAP PR + SAP PO + Work Order)
sebagai **CSV**, **tanpa batas jumlah baris**. Cocok untuk menarik dataset penuh
sekaligus.

- Query dieksekusi sekali dan di-stream via server-side cursor, sehingga ringan
  di memori server maupun klien (tidak menahan seluruh data di memori).
- Output menyertakan BOM UTF-8 agar terbaca benar di Excel.
- Kolom identik dengan `GET /api/public/tracking` (lihat di bawah), termasuk
  kolom hasil kalkulasi `Status`.

**Response headers**

```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="Tracking_YYYYMMDD.csv"
```

**Query parameters** (semua opsional)

| Param        | Keterangan                                             |
|--------------|--------------------------------------------------------|
| `q`          | Pencarian bebas (order, material, deskripsi, equipment) |
| `plant`      | Filter plant persis, mis. `6601`                       |
| `status`     | Filter status (lihat daftar nilai di bawah)            |
| `material`   | Filter material (ILIKE)                                 |
| `pr`         | Filter nomor PR (ILIKE)                                 |
| `po`         | Filter nomor PO (ILIKE)                                 |
| `order_val`  | Filter nomor order (ILIKE)                              |
| `order_by`   | Kolom urut (default `t.id`)                             |
| `order_dir`  | `ASC` (default) atau `DESC`                             |

**Nilai `status`**

`no-pr`, `pr-created`, `po-created`, `partial`, `complete`, `with_pr`,
`without_pr`, `with_po`, `without_po`.

**Kolom `Status` (hasil kalkulasi per baris)**

| Nilai        | Kondisi                                             |
|--------------|-----------------------------------------------------|
| `no-pr`      | belum ada PR                                         |
| `pr-created` | ada PR, belum ada PO                                 |
| `po-created` | ada PO, `Qty_Deliv` = 0                              |
| `partial`    | `Qty_Deliv` > 0 dan < `Qty_Reqmts`                  |
| `complete`   | `Qty_Deliv` ≥ `Qty_Reqmts` (dan `Qty_Reqmts` > 0)  |

**Contoh**

```bash
# Seluruh data
curl -H "x-api-key: $PUBLIC_API_KEY" \
  "https://<host>/api/public/tracking.csv" -o tracking.csv

# Key lewat query param + filter
curl "https://<host>/api/public/tracking.csv?api_key=$PUBLIC_API_KEY&plant=6601&status=complete" \
  -o tracking_6601_complete.csv
```

Di Power BI: **Get Data → Web**, isi URL di atas (sertakan `?api_key=...`), lalu
gunakan konektor **CSV/Text**.

---

### `GET /api/public/tracking` — tracking (JSON, paginasi)

Versi JSON dari data tracking, dengan paginasi. Berbeda dengan endpoint CSV di
atas, endpoint ini **dibatasi maksimal 99.999 baris per panggilan**, sehingga
dataset yang lebih besar harus ditarik per halaman (`page`).

**Query parameters**: `page`, `limit`, `q`, `order_val`, `material`, `pr`, `po`,
`status`, `plant`, `order_by`, `order_dir`.

**Bentuk response** (kompatibel OData untuk Power BI)

```json
{
  "@odata.count": 123894,
  "meta": { "total": 123894, "page": 1, "limit": 99999, "total_pages": 2 },
  "value": [ { "Plant": "6601", "Material": "...", "Status": "complete", ... } ],
  "data":  [ ... ]
}
```

Gunakan `meta.total_pages` untuk iterasi seluruh halaman.

> Untuk menarik **seluruh** data dalam sekali ambil, pakai
> `GET /api/public/tracking.csv` yang tidak memiliki batas baris.

---

### Endpoint publik lainnya

| Endpoint                            | Keterangan                                              |
|-------------------------------------|---------------------------------------------------------|
| `GET /api/public/joblist-taex`      | Joblist TA-ex                                            |
| `GET /api/public/tracking-joblist`  | Tracking Joblist (WO + JD + JL + Project + Area + Unit)  |
| `GET /api/public/tracking-joblist2` | Tracking Joblist 2 (vw_joblist_wo + vw_joblist_detail)   |
| `GET /api/public/info`              | Daftar endpoint publik + info autentikasi               |

## Menjalankan lokal

```bash
pip install -r requirements.txt
# set environment variables (mis. lewat file .env)
#   DATABASE_URL=postgresql://user:password@localhost:5432/prisma_taex
#   PUBLIC_API_KEY=<key untuk endpoint publik>
uvicorn main:app --reload --port 8080
```

Migrasi database dijalankan otomatis saat startup.
