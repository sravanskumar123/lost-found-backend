from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from db import get_db_connection
from auth import get_current_user

admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)

# ================= Admin Role Check =================

def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# ================= View All Users =================

@admin_router.get("/users")
def get_all_users(
    role: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
            SELECT id, name, email, phone, department, year, role, created_at
            FROM users
        """

        params = []

        if role:
            query += " WHERE role = %s"
            params.append(role)

        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, tuple(params))
        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


# ================= View All Items =================

@admin_router.get("/items")
def get_all_items(
    type: Optional[str] = None,
    status: Optional[str] = None,
    is_deleted: Optional[bool] = None,
    limit: int = 20,
    offset: int = 0,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
            SELECT i.*, u.name AS owner_name, u.email AS owner_email
            FROM items i
            JOIN users u ON i.user_id = u.id
        """

        conditions = []
        params = []

        if type:
            conditions.append("i.type = %s")
            params.append(type)

        if status:
            conditions.append("i.status = %s")
            params.append(status)

        if is_deleted is not None:
            conditions.append("i.is_deleted = %s")
            params.append(is_deleted)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY i.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, tuple(params))
        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


# ================= View All Claims =================

@admin_router.get("/claims")
def get_all_claims(
    status: Optional[str] = None,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        query = """
            SELECT c.*, 
                   u.name AS claimant_name, 
                   u.email AS claimant_email,
                   i.title AS item_title
            FROM claims c
            JOIN users u ON c.claimant_id = u.id
            JOIN items i ON c.item_id = i.id
        """

        params = []

        if status:
            query += " WHERE c.status = %s"
            params.append(status)

        cursor.execute(query, tuple(params))
        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


# ================= Soft Delete Item =================

@admin_router.delete("/items/{item_id}")
def soft_delete_item(
    item_id: int,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            UPDATE items
            SET is_deleted = TRUE
            WHERE id = %s AND is_deleted = FALSE
        """, (item_id,))

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Item not found or already deleted"
            )

        conn.commit()

        return {"message": "Item soft deleted successfully"}

    except:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ================= Restore Deleted Item =================

@admin_router.put("/items/{item_id}/restore")
def restore_item(
    item_id: int,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            UPDATE items
            SET is_deleted = FALSE
            WHERE id = %s AND is_deleted = TRUE
        """, (item_id,))

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Item not found or not deleted"
            )

        conn.commit()

        return {"message": "Item restored successfully"}

    except:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ================= Force Reject Claim =================

@admin_router.put("/claims/{claim_id}/force-reject")
def force_reject_claim(
    claim_id: int,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT id, status FROM claims WHERE id = %s",
            (claim_id,)
        )
        claim = cursor.fetchone()

        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        # 🔥 FIX: prevent multiple actions
        if claim["status"] != "pending":
            raise HTTPException(
                status_code=400,
                detail="Action already taken"
            )

        cursor.execute(
            "UPDATE claims SET status = 'rejected' WHERE id = %s",
            (claim_id,)
        )

        conn.commit()

        return {"message": "Claim rejected successfully"}

    except:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ================= Admin Approve Claim =================

@admin_router.put("/claims/{claim_id}/approve")
def admin_approve_claim(
    claim_id: int,
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT id, item_id, status FROM claims WHERE id = %s",
            (claim_id,)
        )
        claim = cursor.fetchone()

        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")

        # 🔥 FIX: prevent multiple actions
        if claim["status"] != "pending":
            raise HTTPException(
                status_code=400,
                detail="Action already taken"
            )

        item_id = claim["item_id"]

        # Approve selected claim
        cursor.execute(
            "UPDATE claims SET status = 'approved' WHERE id = %s",
            (claim_id,)
        )

        # Reject others
        cursor.execute(
            """
            UPDATE claims
            SET status = 'rejected'
            WHERE item_id = %s AND id != %s
            """,
            (item_id, claim_id)
        )

        # Update item
        cursor.execute(
            """
            UPDATE items
            SET status = 'claimed'
            WHERE id = %s AND is_deleted = FALSE
            """,
            (item_id,)
        )

        conn.commit()

        return {"message": "Claim approved successfully"}

    except:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# ================= Admin Dashboard =================

@admin_router.get("/dashboard")
def admin_dashboard(
    current_admin: dict = Depends(require_admin)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT COUNT(*) AS total_users FROM users")
        total_users = cursor.fetchone()["total_users"]

        cursor.execute("""
            SELECT COUNT(*) AS total_items
            FROM items
            WHERE is_deleted = FALSE
        """)
        total_items = cursor.fetchone()["total_items"]

        cursor.execute("""
            SELECT COUNT(*) AS lost_count
            FROM items
            WHERE type = 'lost' AND is_deleted = FALSE
        """)
        lost_count = cursor.fetchone()["lost_count"]

        cursor.execute("""
            SELECT COUNT(*) AS found_count
            FROM items
            WHERE type = 'found' AND is_deleted = FALSE
        """)
        found_count = cursor.fetchone()["found_count"]

        cursor.execute("""
            SELECT COUNT(*) AS active_items
            FROM items
            WHERE status = 'active' AND is_deleted = FALSE
        """)
        active_items = cursor.fetchone()["active_items"]

        cursor.execute("""
            SELECT COUNT(*) AS claimed_items
            FROM items
            WHERE status = 'claimed' AND is_deleted = FALSE
        """)
        claimed_items = cursor.fetchone()["claimed_items"]

        cursor.execute("""
            SELECT COUNT(*) AS returned_items
            FROM items
            WHERE status = 'returned' AND is_deleted = FALSE
        """)
        returned_items = cursor.fetchone()["returned_items"]

        cursor.execute("""
            SELECT COUNT(*) AS pending_claims
            FROM claims
            WHERE status = 'pending'
        """)
        pending_claims = cursor.fetchone()["pending_claims"]

        return {
            "total_users": total_users,
            "total_items": total_items,
            "lost_count": lost_count,
            "found_count": found_count,
            "active_items": active_items,
            "claimed_items": claimed_items,
            "returned_items": returned_items,
            "pending_claims": pending_claims
        }

    finally:
        cursor.close()
        conn.close()