[main (2).py](https://github.com/user-attachments/files/33307158/main.2.py)
"""
PillCheck Backend — dorilar ma'lumotlarini mobil ilovaga yetkazuvchi API.

Ishga tushirish:
    uvicorn main:app --reload

Keyin brauzerda oching: http://127.0.0.1:8000/docs
Bu yerda barcha API'larni sinab ko'rish uchun avtomatik interfeys chiqadi.
"""
import os
from contextlib import asynccontextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/pillcheck",
)


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Server ishga tushganda bazaga ulanishni sinab ko'ramiz — muammo bo'lsa darhol bilamiz
    try:
        conn = get_connection()
        conn.close()
        print("Bazaga ulanish OK.")
    except Exception as e:
        print("OGOHLANTIRISH: bazaga ulanib bo'lmadi:", e)
    yield


app = FastAPI(
    title="PillCheck API",
    description="Dorilar haqida ma'lumot beruvchi backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Mobil ilova (yoki brauzerdagi test sahifasi) so'rov yubora olishi uchun
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Medicine(BaseModel):
    id: int
    name: str
    active_ingredient: Optional[str] = None
    usage: Optional[str] = None
    dosage: Optional[str] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    min_age: Optional[str] = None
    duration: Optional[str] = None
    contraindications: Optional[str] = None
    manufacturer: Optional[str] = None
    status: str


SELECT_COLUMNS = """
    id, name, active_ingredient, usage, dosage,
    price_min, price_max, min_age, duration,
    contraindications, manufacturer, status
"""


@app.get("/", tags=["Holat"])
def root():
    """Server ishlab turganini tekshirish uchun oddiy javob."""
    return {"xabar": "PillCheck backend ishlayapti", "hujjat": "/docs"}


@app.get("/medicines", response_model=list[Medicine], tags=["Dorilar"])
def search_medicines(
    q: Optional[str] = Query(
        None, description="Dori nomi (to'liq yoki qisman, imlo xatosi bo'lsa ham topiladi)"
    ),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Dori nomi bo'yicha qidiradi.
    - `q` berilmasa — barcha dorilardan bir nechtasini qaytaradi.
    - `q` berilsa — imlo xatosiga chidamli o'xshashlik bo'yicha saralab qaytaradi.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if q:
                cur.execute(
                    f"""
                    SELECT {SELECT_COLUMNS}
                    FROM medicines
                    WHERE name ILIKE %s OR similarity(name, %s) > 0.2
                    ORDER BY similarity(name, %s) DESC
                    LIMIT %s
                    """,
                    (f"%{q}%", q, q, limit),
                )
            else:
                cur.execute(f"SELECT {SELECT_COLUMNS} FROM medicines ORDER BY name LIMIT %s", (limit,))
            return cur.fetchall()
    finally:
        conn.close()


@app.get("/medicines/{medicine_id}", response_model=Medicine, tags=["Dorilar"])
def get_medicine(medicine_id: int):
    """Bitta dorining to'liq ma'lumotini id bo'yicha qaytaradi."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {SELECT_COLUMNS} FROM medicines WHERE id = %s", (medicine_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bunday id bilan dori topilmadi")
            return row
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Statistika (faqat o'qish uchun, haqiqiy ma'lumotdan hisoblanadi)
# ---------------------------------------------------------------------------

CATEGORY_RULES = [
    ("Og'riq va yallig'lanish", ["og'riq", "isitma", "yallig", "spazm"]),
    ("Antibiotik va virusga qarshi", ["antibiotik", "antibakterial", "virus"]),
    ("Oshqozon-ichak", ["oshqozon", "ichak", "ich ketish", "toksin", "suv balans"]),
    ("Yurak va qon bosimi", ["yurak", "qon bosimi", "diuretik", "qon suyult"]),
    ("Nafas yo'llari va allergiya", ["yo'tal", "burun", "allergiya"]),
    ("Vitaminlar", ["vitamin"]),
]


def _norm(text: str) -> str:
    """Turli apostrof belgilarini (‘ ’ ʻ ʼ `) oddiy ' ga keltiradi."""
    t = (text or "").lower()
    for ch in ("\u2018", "\u2019", "\u02bb", "\u02bc", "`"):
        t = t.replace(ch, "'")
    return t


def category_name(usage: Optional[str]) -> str:
    u = _norm(usage or "")
    for name, keys in CATEGORY_RULES:
        if any(k in u for k in keys):
            return name
    return "Boshqa"


def _interaction_counts(cur, conn):
    """Migratsiya qilinmagan bo'lsa ham xato bermasligi uchun ehtiyotkor hisoblaydi."""
    try:
        cur.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE severity = 'danger') AS danger "
            "FROM medicine_interactions"
        )
        r = cur.fetchone()
        return int(r["total"]), int(r["danger"])
    except Exception:
        conn.rollback()
        try:
            cur.execute("SELECT COUNT(*) AS total FROM medicine_interactions")
            return int(cur.fetchone()["total"]), 0
        except Exception:
            conn.rollback()
            return 0, 0


@app.get("/stats", tags=["Statistika"])
def stats():
    """Ma'lumotlar bazasi holati: nechta dori, nechtasi tekshirilgan, toifalar, to'liqlik."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'verified') AS verified,
                    COUNT(*) FILTER (WHERE active_ingredient IS NULL OR active_ingredient = '') AS missing_active,
                    COUNT(*) FILTER (WHERE min_age IS NULL OR min_age = '') AS missing_age,
                    COUNT(*) FILTER (WHERE duration IS NULL OR duration = '') AS missing_duration,
                    COUNT(*) FILTER (WHERE contraindications IS NULL OR contraindications = '') AS missing_contra,
                    MIN(price_min) AS price_min,
                    MAX(price_max) AS price_max
                FROM medicines
                """
            )
            s = cur.fetchone()
            cur.execute("SELECT usage FROM medicines")
            counts: dict = {}
            for row in cur.fetchall():
                name = category_name(row["usage"])
                counts[name] = counts.get(name, 0) + 1
            inter_total, inter_danger = _interaction_counts(cur, conn)

        total = int(s["total"])
        categories = [
            {"name": n, "count": c}
            for n, c in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        return {
            "total": total,
            "verified": int(s["verified"]),
            "draft": total - int(s["verified"]),
            "interactions_total": inter_total,
            "interactions_danger": inter_danger,
            "price_min": s["price_min"],
            "price_max": s["price_max"],
            "categories": categories,
            "missing": {
                "active_ingredient": int(s["missing_active"]),
                "min_age": int(s["missing_age"]),
                "duration": int(s["missing_duration"]),
                "contraindications": int(s["missing_contra"]),
            },
        }
    finally:
        conn.close()
