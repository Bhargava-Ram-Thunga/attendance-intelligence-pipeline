from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from attendance.models import AttendanceRecord, WeeklyAttendanceDigest, Student, Tenant
from notifications.models import Notification
import time

@shared_task
def nightly_digest(tenant_id=None):
    """
    Celery Beat: 23:00 IST (17:30 UTC).
    Aggregates weekly attendance for all active students.
    """
    # If no tenant_id provided, run for all active tenants
    tenants = Tenant.objects.filter(is_active=True) if tenant_id is None else Tenant.objects.filter(id=tenant_id)

    # Calculate week_start (Monday of current week)
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())

    for tenant in tenants:
        students = Student.objects.filter(tenant=tenant)

        for student in students:
            # Aggregate attendance for the week
            records = AttendanceRecord.objects.filter(
                tenant=tenant,
                student=student,
                date__gte=week_start
            )

            total = records.count()
            present = records.filter(status="PRESENT").count()
            late = records.filter(status="LATE").count()

            pct = 0 if total == 0 else ((present + (late * 0.5)) / total) * 100

            summary = {
                "total_periods": total,
                "present": present,
                "late": late,
                "percentage": pct
            }

            # Idempotent upsert
            WeeklyAttendanceDigest.objects.update_or_create(
                tenant=tenant,
                student=student,
                week_start=week_start,
                defaults={"summary_data": summary}
            )

@shared_task
def dead_letter_retry():
    """
    Celery Beat: Every 30 minutes.
    Recover failed notifications from Redis ZSET.
    """
    import redis
    from django.conf import settings
    # Use settings.REDIS_URL for consistency across the app
    r = redis.from_url(settings.REDIS_URL)
    zset_key = "notifications:dead_letter"

    now = int(time.time())
    six_hours_ago = now - (6 * 3600)

    # 1. Permanently Fail items > 6 hours old
    expired_ids = r.zrangebyscore(zset_key, 0, six_hours_ago)
    if expired_ids:
        id_list = [id.decode('utf-8') for id in expired_ids]
        Notification.objects.filter(id__in=id_list).update(delivery_status="PERMANENTLY_FAILED")
        r.zrem(zset_key, *expired_ids)

    # 2. Retry items < 6 hours old
    retry_ids = r.zrangebyscore(zset_key, six_hours_ago, "+inf")
    for member in retry_ids:
        notif_id = member.decode('utf-8')
        # Trigger delivery task
        from notifications.tasks import deliver_notification
        deliver_notification.delay(notif_id)
        # Remove from ZSET so it's not retried again until it fails again
        r.zrem(zset_key, member)
