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
