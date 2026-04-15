from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from auth import get_current_user
from db import get_db_connection
import os
import shutil
from uuid import uuid4

router = APIRouter()
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ================= CREATE CLAIM =================
@router.post("/claims")
async def create_claim(
    item_id: int = Form(...),
    message: str = Form(...),
    proof_image: UploadFile = File(None),
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Check item exists
        cursor.execute("SELECT * FROM items WHERE id = %s", (item_id,))
        item = cursor.fetchone()
        print("ITEM:", item)
        print("ITEM OWNER:", item["user_id"] if item else None)
        print("CURRENT USER:", current_user["id"])
        print("STATUS:", item["status"] if item else None)

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if item["status"] != "active":
            raise HTTPException(status_code=400, detail="Item not available")

        if item["user_id"] == current_user["id"]:
            raise HTTPException(status_code=400, detail="Cannot claim your own item")

        # Prevent duplicate claim
        cursor.execute(
            "SELECT * FROM claims WHERE item_id = %s AND claimant_id = %s",
            (item_id, current_user["id"])
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="You already claimed this item")

        image_url = None

        if proof_image:
            allowed_extensions = [".jpg", ".jpeg", ".png", ".webp"]

            filename = proof_image.filename.lower()

            if not any(filename.endswith(ext) for ext in allowed_extensions):
                raise HTTPException(status_code=400, detail="Only image files are allowed")

            MAX_FILE_SIZE = 5 * 1024 * 1024
            contents = await proof_image.read()

            if len(contents) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File too large (max 5MB)")

            proof_image.file.seek(0)

            filename = f"{uuid4()}_{proof_image.filename}"
            file_path = os.path.join(UPLOAD_FOLDER, filename)

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(proof_image.file, buffer)

            image_url = filename

        cursor.execute(
            """
            INSERT INTO claims (item_id, claimant_id, message, proof_image, status, created_at)
            VALUES (%s, %s, %s, %s, 'pending', NOW())
            """,
            (item_id, current_user["id"], message, image_url)
        )

        conn.commit()
        return {"message": "Claim submitted successfully"}

    finally:
        cursor.close()
        conn.close()


# ================= MY CLAIMS =================
@router.get("/claims/my")
def my_claims(current_user = Depends(get_current_user)):

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute(
            """
            SELECT 
                c.id,
                c.status,
                c.message,
                i.title,
                i.location_name,

                -- ✅ FIX ADDED (DO NOT REMOVE ORIGINAL LOGIC)
                u.phone AS owner_phone,

                CASE
                    WHEN c.status = 'approved' THEN u.phone
                    ELSE NULL
                END AS owner_phone_old

            FROM claims c
            JOIN items i ON c.item_id = i.id
            JOIN users u ON i.user_id = u.id
            WHERE c.claimant_id = %s
            ORDER BY c.created_at DESC
            """,
            (int(current_user["id"]),)
        )

        claims = cursor.fetchall()
        print("CLAIMS FOUND:", claims)

        # ✅ ADDED (SAFE RETURN)
        if not claims:
            return []

        return claims

    finally:
        cursor.close()
        conn.close()


# ================= VIEW CLAIMS FOR ITEM (OWNER) =================
@router.get("/claims/item/{item_id}")
def view_claims_for_item(
    item_id: int,
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Verify owner
        cursor.execute("SELECT * FROM items WHERE id = %s", (item_id,))
        item = cursor.fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if item["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        cursor.execute(
            """
            SELECT 
                c.id,
                c.message,
                c.status,
                c.created_at,
                u.name,
                u.email,
                u.phone
            FROM claims c
            JOIN users u ON c.claimant_id = u.id
            WHERE c.item_id = %s
            ORDER BY c.created_at DESC
            """,
            (item_id,)
        )

        claims = cursor.fetchall()

        # ✅ ADDED
        if not claims:
            return []

        return claims

    finally:
        cursor.close()
        conn.close()


# ================= APPROVE CLAIM =================
@router.put("/claims/{claim_id}/approve")
def approve_claim(
    claim_id: int,
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Get claim
        cursor.execute("SELECT * FROM claims WHERE id = %s", (claim_id,))
        claim = cursor.fetchone()

        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        # Get related item
        cursor.execute("SELECT * FROM items WHERE id = %s", (claim["item_id"],))
        item = cursor.fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Verify owner
        if item["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Ensure still active
        if item["status"] != "active":
            raise HTTPException(status_code=400, detail="Item already claimed")

        # Approve this claim
        cursor.execute(
            "UPDATE claims SET status = 'approved' WHERE id = %s",
            (claim_id,)
        )

        # Reject all other claims for this item
        cursor.execute(
            "UPDATE claims SET status = 'rejected' WHERE item_id = %s AND id != %s",
            (claim["item_id"], claim_id)
        )

        # Update item status
        cursor.execute(
            "UPDATE items SET status = 'claimed' WHERE id = %s",
            (claim["item_id"],)
        )

        #Fetch phone number
        cursor.execute(
            "SELECT phone FROM users WHERE id = %s",
            (item["user_id"],)
        )
        owner = cursor.fetchone()

        conn.commit()
        return {            
            "message": "Claim approved successfully",
            "contact_phone": owner["phone"]
        }


    finally:
        cursor.close()
        conn.close()


# ================= REJECT CLAIM =================
@router.put("/claims/{claim_id}/reject")
def reject_claim(
    claim_id: int,
    current_user = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM claims WHERE id = %s", (claim_id,))
        claim = cursor.fetchone()

        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        cursor.execute("SELECT * FROM items WHERE id = %s", (claim["item_id"],))
        item = cursor.fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        if item["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not authorized")

        cursor.execute(
            "UPDATE claims SET status = 'rejected' WHERE id = %s",
            (claim_id,)
        )

        conn.commit()
        return {"message": "Claim rejected successfully"}

    finally:
        cursor.close()
        conn.close()


# ================= MY ITEMS (WITH CLAIM COUNT) =================
@router.get("/items/my")
def get_my_items(current_user = Depends(get_current_user)):

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:

        cursor.execute(
            """
            SELECT 
                i.id,
                i.user_id,
                i.type,
                i.title,
                i.category,
                i.description,
                i.location_name,
                i.status,
                i.created_at,

                COUNT(c.id) AS claim_count

            FROM items i

            LEFT JOIN claims c 
                ON c.item_id = i.id

            WHERE i.user_id = %s
            AND i.is_deleted = FALSE

            GROUP BY i.id

            ORDER BY i.created_at DESC
            """,
            (int(current_user["id"]),)
        )

        items = cursor.fetchall()

        print("MY ITEMS:", items)

        # ✅ ADDED
        if not items:
            return []

        return items

    finally:
        cursor.close()
        conn.close()