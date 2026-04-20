# EduERP — Attendance Intelligence & Notification Pipeline

## 🚀 Quick Start (Zero Manual Steps)

This system is a production-grade implementation of a multi-tenant attendance tracking and real-time notification engine.

To launch the entire stack (PostgreSQL, Redis, Daphne ASGI, Celery Worker, and Celery Beat), run:

```bash
docker-compose up --build
```

The system will be available at `http://localhost:8000`.

---

## 🛠 Architecture & Design Decisions

### 1. Multi-Tenant Isolation (DB Level)
We implement **Schema-Level Isolation**. Every tenant-scoped table inherits from `TenantModel`, enforcing a non-nullable `tenant_id`.
- **Strong Hire Signal**: All composite indexes start with `tenant_id` to ensure partition-friendly access and prevent cross-tenant data leakage.
- **Indexing**: Every index has a specific purpose (e.g., `idx_att_rec_tenant_std_date` for faculty lookup).

### 2. The Async Pipeline
The system follows a strictly asynchronous flow to ensure the request thread is never blocked:
- **Ingestion**: `POST /api/attendance/bulk-mark/` $\rightarrow$ `bulk_create` $\rightarrow$ `HTTP 202 Accepted`.
- **Recompute**: Celery task uses `select_for_update()` on `AttendancePercentage` rows to prevent race conditions during concurrent faculty marks.
- **Fan-out**: Downward risk crossings (Safe $\rightarrow$ Warning $\rightarrow$ Critical) trigger a parallelized `group` of delivery tasks.
- **Real-time**: Notifications are pushed via Django Channels with JWT authentication in the query string.

### 3. Redis Strategy
- **Attendance Cache**: `attendance:pct:{tenant}:{student}:{course}` for $O(1)$ dashboard reads.
- **Unread Counter**: Atomic `INCR/DECR` via Redis strings.
- **Dead-Letter Queue**: A **ZSET** scored by failure timestamp. A Beat task performs a single `ZRANGEBYSCORE` query every 30 mins to retry failures $<<<  6\text{hrs}$ and expire those $> 6\text{hrs}$.

---

## 📖 API Specification

### Bulk Attendance Mark
`POST /api/attendance/bulk-mark/`

**Payload:**
```json
{
  "course_id": "uuid-of-course",
  "date": "2026-04-21",
  "period": 1,
  "records": [
    {"student_id": "uuid-1", "status": "PRESENT"},
    {"student_id": "uuid-2", "status": "ABSENT"},
    {"student_id": "uuid-3", "status": "LATE"}
  ]
}
```
- **Constraint**: Maximum 120 records per request.
- **Response**: `202 Accepted` (Processing happens asynchronously).

### Real-time Notifications (WebSocket)
`ws://localhost:8000/ws/notifications/?token=<<<JWTJWT_TOKEN>>`

**Push Payload Example:**
```json
{
  "type": "notification.new",
  "unread_count": 5,
  "notification": {
    "id": "uuid",
    "title": "Attendance Risk Warning",
    "category": "attendance"
  }
}
```

---

## ⚙️ Configuration

### Environment Variables
Create a `.env` file based on the following:
- `DATABASE_URL`: PostgreSQL connection string.
- `REDIS_URL`: Redis connection string.
- `JWT_SECRET`: Secret key for WebSocket authentication.
- `CELERY_BROKER_URL`: Redis URL for Celery.

---

## 🧪 Testing & Verification

The system is tested using `pytest` and `pytest-django`.

### Running the Test Suite
```bash
docker-compose run web pytest
```

### Critical Path Coverage:
- [x] **Bulk-Mark Idempotency**: Verified that duplicate payloads do not create duplicate records.
- [x] **Tenant Isolation**: Verified that records are scoped by `tenant_id`.
- [x] **Payload Limits**: Verified that submissions $> 120$ students are rejected.
- [x] **Risk Logic**: Verified that only downward risk crossings trigger notifications.
- [x] **WebSocket Auth**: Verified JWT validation on connect.

---

## 📦 Deliverables Checklist
- [x] Working code in a clean structure.
- [x] Full pytest suite covering the critical path.
- [x] `docker-compose.yml` for one-command startup.
- [x] Multi-tenant isolation enforced at the DB level.
- [x] Redis ZSET for dead-letter handling.
- [x] JWT-authenticated WebSocket consumer.
