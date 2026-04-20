# EduERP — Attendance Intelligence + Notification Pipeline
**Date**: 2026-04-21  
**Role**: Senior Lead Engineer — Backend Platform  
**Stack**: Django 5, DRF, Celery, Redis, Django Channels, PostgreSQL  
**Target Score**: 85+ (Strong Hire)

---

## 1. System Overview

Two components, one pipeline. An attendance update triggers a notification. A notification reaches a WebSocket. Not two isolated tasks.

```
Faculty POST /api/attendance/bulk-mark/
  → bulk_create AttendanceRecord (HTTP 202 immediately)
  → transaction.on_commit → recompute_attendance.delay()
      → select_for_update on AttendancePercentage
      → compare old vs new risk_status
      → if crossed downward → fan_out_notifications.delay()
          → bulk_create Notification (3 targets)
          → INCR unread:{user_id} per recipient
          → group(deliver_notification.s(id) for each)
              → channel_layer.group_send → WebSocket push (<2s)
              → on 3 failures → ZADD notifications:dead_letter {ts} {id}
      → SET attendance:pct:{tenant_id}:{student_id}:{course_id}
```

---

## 2. Docker Services

| Service | Image | Command |
|---|---|---|
| `db` | `postgres:15` | default |
| `redis` | `redis:7` | default |
| `web` | `app:latest` | `daphne -b 0.0.0.0 -p 8000 config.asgi:application` |
| `worker` | `app:latest` | `celery -A config worker -l info` |
| `beat` | `app:latest` | `celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` |

Single Dockerfile. Zero manual steps beyond `docker-compose up`.

---

## 3. Project Structure

```
eduerp/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── requirements.txt
├── manage.py
├── config/
│   ├── settings.py
│   ├── asgi.py
│   ├── urls.py
│   └── celery.py
├── attendance/
│   ├── models.py       # Tenant, Program, Course, Student, Parent, Counselor,
│   │                   # StudentCounselorAssignment, AttendanceRecord,
│   │                   # AttendancePercentage, WeeklyAttendanceDigest
│   ├── serializers.py
│   ├── views.py        # BulkMarkView
│   ├── tasks.py        # recompute_attendance, nightly_digest
│   ├── urls.py
│   └── migrations/
├── notifications/
│   ├── models.py       # Notification
│   ├── consumers.py    # NotificationConsumer (JWT auth, group_add/discard)
│   ├── tasks.py        # fan_out_notifications, deliver_notification, dead_letter_retry
│   ├── routing.py
│   └── migrations/
└── tests/
    ├── conftest.py
    ├── test_bulk_mark.py
    ├── test_recompute.py
    ├── test_fan_out.py
    └── test_websocket.py
```

---

## 4. Data Models

### Abstract Base
```python
class TenantModel(models.Model):
    tenant = models.ForeignKey('attendance.Tenant', on_delete=models.PROTECT)
    class Meta:
        abstract = True
```

### Key Models

**AttendanceRecord**
- Fields: `tenant, student, course, date, period, status (Present/Absent/Late)`
- UNIQUE: `(tenant_id, student_id, course_id, date, period)`
- Indexes:
  - `(tenant_id, course_id, date)` — bulk-mark lookup
  - `(tenant_id, student_id, course_id)` — recompute aggregation

**AttendancePercentage**
- Fields: `tenant, student, course, percentage, risk_status (Safe/Warning/Critical), updated_at`
- UNIQUE: `(tenant_id, student_id, course_id)`
- Index: `(tenant_id, student_id, course_id)` — select_for_update target

**Notification**
- Fields: `tenant, recipient (User FK), title, body, category, delivery_status, dedup_key, created_at, read_at`
- delivery_status: `Pending | Delivered | Failed | PermanentlyFailed`
- UNIQUE: `(dedup_key, recipient_id)` — idempotent fan-out
- Index: `(tenant_id, recipient_id, delivery_status)` — dead-letter query

**WeeklyAttendanceDigest**
- Fields: `tenant, student, week_start (date), summary_data (JSONB), created_at`
- UNIQUE: `(tenant_id, student_id, week_start)` — idempotent upsert

---

## 5. Redis Key Schema

| Purpose | Key | Type | Operation |
|---|---|---|---|
| Attendance % cache | `attendance:pct:{tenant_id}:{student_id}:{course_id}` | STRING | SET/GET, EX 3600 |
| Unread counter | `unread:{user_id}` | STRING (int) | INCR / Lua DECR |
| Pub/Sub | `user:{user_id}:notifications` | PUB/SUB channel | PUBLISH / SUBSCRIBE |
| Dead-letter | `notifications:dead_letter` | ZSET (score=failure_ts) | ZADD / ZRANGEBYSCORE / ZREM |

---

## 6. Celery Tasks

### `recompute_attendance(course_id, student_ids, tenant_id)`
- `select_for_update()` on AttendancePercentage rows
- Compute % from AttendanceRecord aggregate (single query per student)
- Detect downward threshold crossing → dispatch `fan_out_notifications`
- `bulk_update` percentage + risk_status
- `SET` Redis cache per student
- Idempotent: DB is source of truth; safe to retry after kill

### `fan_out_notifications(student_id, course_id, tenant_id, new_pct)`
- Resolve Student, Parent (optional), Counselor (optional) users
- Check dedup_key existence — bail if already created
- `bulk_create` Notification rows
- `group(deliver_notification.s(n.id) for n in created).apply_async()`

### `deliver_notification(notification_id)`
- `INCR unread:{user_id}`
- `channel_layer.group_send` with notification payload
- Update `delivery_status = Delivered`
- On failure (max_retries=3): `ZADD notifications:dead_letter {now} {id}`, set `Failed`

### `nightly_digest` (Beat: 17:30 UTC daily)
- Per active tenant: aggregate weekly attendance per student
- `update_or_create` WeeklyAttendanceDigest on `(student_id, week_start)` — idempotent

### `dead_letter_retry` (Beat: every 30 min)
- `ZRANGEBYSCORE dead_letter 0 {now-21600}` → mark PermanentlyFailed, ZREM
- `ZRANGEBYSCORE dead_letter {now-21600} +inf` → re-dispatch `deliver_notification`

---

## 7. WebSocket Consumer

```python
class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        token = parse_jwt(self.scope["query_string"])
        if not token: await self.close(4001); return
        self.group_name = f"notifications_{token.user_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notification_new(self, event):
        await self.send(json.dumps(event))
```

---

## 8. Idempotency Summary

| Layer | Mechanism |
|---|---|
| DB | UNIQUE constraints on AttendanceRecord, AttendancePercentage, Notification (dedup_key), WeeklyAttendanceDigest |
| Recompute | `select_for_update()` serializes concurrent recomputes |
| Fan-out | dedup_key check before `bulk_create` |
| Deliver | delivery_status check before PUBLISH |
| Digest | `update_or_create` |
| Dead-letter | `ZRANGEBYSCORE` replaces full scan; ZREM on resolution |

---

## 9. Multi-Tenant Isolation

- `tenant_id` FK on every model — schema level, not view filter
- All composite indexes begin with `tenant_id`
- Celery tasks receive `tenant_id` explicitly
- Redis keys namespaced with `tenant_id`
- DRF permission class validates `request.user.tenant == object.tenant`

---

## 10. Evaluation Rubric Targets

| Dimension | Points | Our Approach |
|---|---|---|
| Database Design | 25 | TenantModel base, composite indexes with comments, risk_status cached field, reversible migrations |
| Celery Architecture | 25 | 202 immediately, select_for_update, bulk_create + group(), dead-letter Beat task |
| Redis Usage | 20 | STRING cache + TTL invalidation, INCR/DECR counter, Pub/Sub, ZSET dead-letter |
| WebSocket | 20 | JWT query param, group_add/discard, notification_new handler, silent drop if offline |
| Code Quality & Tests | 10 | pytest covering bulk-mark, recompute, fan-out, WebSocket push |
