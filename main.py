from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from db import get_db_connection
from typing import Optional
import os
import shutil
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from auth import hash_password, verify_password, create_access_token, get_current_user
from fastapi.security import OAuth2PasswordRequestForm
from claims import router as claims_router
from admin import admin_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.include_router(claims_router)
app.include_router(admin_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# ================= Pydantic Schemas =================

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    department: Optional[str] = None
    year: Optional[str] = None
    role: Optional[str] = "student"
    password: str = Field(..., min_length=6, max_length=50)


# ================= AUTH ROUTES =================

@app.post("/register")
def register(user: UserCreate):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (user.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")

        hashed_password = hash_password(user.password)

        cursor.execute("""
            INSERT INTO users 
            (name, email, phone, department, year, role, password, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            user.name,
            user.email,
            user.phone,
            user.department,
            user.year,
            user.role,
            hashed_password
        ))

        conn.commit()
        return {"message": "User registered successfully"}

    finally:
        cursor.close()
        conn.close()


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (form_data.username,))
        db_user = cursor.fetchone()

        if not db_user or not verify_password(form_data.password, db_user["password"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        access_token = create_access_token(data={"sub": str(db_user["id"])})

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "role": db_user["role"]   # ✅ IMPORTANT
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/check-email")
def check_email(email: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        return {"exists": user is not None}
    finally:
        cursor.close()
        conn.close()

# ================= CREATE ITEM =================

@app.post("/items")
async def create_item(
    type: str = Form(...),
    title: str = Form(...),
    category: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    date_event: Optional[str] = Form(None),
    location_name: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    status: Optional[str] = Form("active"),
    image: UploadFile = File(None),  # ✅ made optional
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # ❌ Block image for FOUND items
        if type == "found" and image:
            raise HTTPException(
                status_code=400,
                detail="Images are not allowed for found items"
            )

        # ✅ Validate image ONLY for LOST
        if type == "lost" and image:
            if not image.content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail="Only image files allowed")

            MAX_FILE_SIZE = 20 * 1024 * 1024
            contents = await image.read()

            if len(contents) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File too large (max 5MB)")

            image.file.seek(0)

        parsed_date = None
        if date_event:
            try:
                parsed_date = datetime.fromisoformat(date_event)
            except:
                parsed_date = None

        cursor.execute("""
            INSERT INTO items
            (user_id, type, title, category, description, date_event,
             location_name, latitude, longitude, status, is_deleted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
        """, (
            current_user["id"],
            type,
            title,
            category,
            description,
            parsed_date,
            location_name,
            latitude,
            longitude,
            status
        ))

        item_id = cursor.lastrowid

        # ✅ Save image ONLY for LOST
        if type == "lost" and image:
            filename = f"{item_id}_{image.filename}"
            file_path = os.path.join(UPLOAD_FOLDER, filename)

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)

            cursor.execute(
                "INSERT INTO item_images (item_id, image_url) VALUES (%s, %s)",
                (item_id, file_path)
            )

        conn.commit()

        return {"message": "Item created successfully", "item_id": item_id}

    except:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ================= GET ITEMS =================

@app.get("/items")
def get_items(
    type: Optional[str] = None,
    status: Optional[str] = "active",
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
        SELECT i.*, img.image_url
        FROM items i
        LEFT JOIN item_images img ON i.id = img.item_id
        WHERE i.is_deleted = FALSE
        """

        params = []

        if type:
            query += " AND i.type = %s"
            params.append(type)

        if status:
            query += " AND i.status = %s"
            params.append(status)

        if search:
            query += " AND i.title LIKE %s"
            params.append(f"%{search}%")
        if category:
            query += " AND i.category = %s"
            params.append(category)

        query += " ORDER BY i.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, tuple(params))
        items = cursor.fetchall()

        for item in items:
            if item["image_url"]:
                filename = os.path.basename(item["image_url"])
                item["image_url"] = f"http://192.168.1.5:8000/uploads/{filename}"

        return items

    finally:
        cursor.close()
        conn.close()


# ================= MY ITEMS =================

@app.get("/items/my")
def my_items(current_user = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT * FROM items
            WHERE user_id = %s AND is_deleted = FALSE
        """, (current_user["id"],))

        items= cursor.fetchall()

        return items if items else []
    
    finally:
        cursor.close()
        conn.close()


# ================= GET SINGLE ITEM =================

@app.get("/items/{item_id}")
def get_single_item(item_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT i.*, img.image_url
            FROM items i
            LEFT JOIN item_images img ON i.id = img.item_id
            WHERE i.id = %s AND i.is_deleted = FALSE
        """, (item_id,))

        item = cursor.fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if item["image_url"]:
            filename = os.path.basename(item["image_url"])
            item["image_url"] = f"http://192.168.1.5:8000/uploads/{filename}"

        return item

    finally:
        cursor.close()
        conn.close()


# ================= MARK ITEM AS RETURNED =================

@app.put("/items/{item_id}/mark-returned")
def mark_item_returned(
    item_id: int,
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            UPDATE items
            SET status = 'returned'
            WHERE id = %s
            AND user_id = %s
            AND status = 'claimed'
            AND is_deleted = FALSE
        """, (item_id, current_user["id"]))

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=400,
                detail="Item not found, not yours, or not in claimed state"
            )

        conn.commit()
        return {"message": "Item marked as returned successfully"}

    finally:
        cursor.close()
        conn.close()