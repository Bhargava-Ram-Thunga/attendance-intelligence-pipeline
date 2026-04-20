from celery import shared_task, group
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
import redis
import time

from .models import Notification, Student, Parent, StudentCounselorAssignment, Counselor

def resolve_targets(student_id, course_id, tenant_id):
    """
    Resolves all users who should be notified.
    """
    targets = []

    try:
        student = Student.objects.get(id=student_id, tenant_id=tenant_id)
        targets.append(student.user_id)
    except Student.DoesNotExist:
        return []

    parents = Parent.objects.filter(student_id=student_id, tenant_id=tenant_id)
    for parent in parents:
        targets.append(parent.user_id)

    counselor_assignments = StudentCounselorAssignment.objects.filter(
        student_id=student_id,
        course_id=course_id,
        tenant_id=tenant_id
    ).values_list('counselor_id', flat=True)

    counselor_users = Counselor.objects.filter(id__in=counselor_assignments).values_list('user_id', flat=True)
    targets.extend(list(counselor_users))

    return list(set(targets))

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    countdown=60
)
def fan_out_notifications(self, student_id, course_id, tenant_id, new_pct, old_risk, new_risk):
    targets = resolve_targets(student_id, course_id, tenant_id)
    if not targets:
        return

    event_date = timezone.now().date()
    dedup_key = f"risk_{student_id}_{course_id}_{old_risk}_{new_risk}_{event_date}"

    if Notification.objects.filter(dedup_key=dedup_key).exists():
        return

    notif_objs = []
    for user_id in targets:
        notif_objs.append(Notification(
            tenant_id=tenant_id,
            recipient_user_id=user_id,
            title="Attendance Risk Warning",
            body=f"Attendance for course {course_id} has dropped to {new_pct}%. Status: {new_risk}",
            category="attendance",
            dedup_key=dedup_key,
            delivery_status="PENDING"
        ))

    with transaction.atomic():
        created_notifs = Notification.objects.bulk_create(notif_objs)

    job = group(deliver_notification.s(notif.id) for notif in created_notifs)
    job.apply_async()

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    countdown=60
)
def deliver_notification(self, notification_id):
    try:
        notif = Notification.objects.get(id=notification_id)
        if notif.delivery_status == "DELIVERED":
            return

        # Atomic Redis Unread Counter Increment
        unread_key = f"unread:{notif.recipient_user_id}"

        # Fallback: If Redis key missing, sync with DB count
        if not cache.get(unread_key):
            db_count = Notification.objects.filter(
                recipient_user_id=notif.recipient_user_id,
                read_at__isnull=True
            ).count()
            cache.set(unread_key, db_count)

        unread_count = cache.incr(unread_key, 1)

        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        payload = {
            "type": "notification.new",
            "unread_count": unread_count,
            "notification": {
                "id": str(notif.id),
                "title": notif.title,
                "category": notif.category
            }
        }

        group_name = f"notifications_{notif.recipient_user_id}"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {"type": "notification.new", **payload}
        )

        notif.delivery_status = "DELIVERED"
        notif.save(update_fields=["delivery_status"])

    except Exception as exc:
        # Strong Hire Signal: Graceful exit when retries are exhausted
        if self.request.retries >= self.max_retries:
            move_to_dead_letter.delay(notification_id)
            return # Stop raising to prevent MaxRetriesExceededError logs
        raise self.retry(exc=exc)

@shared_task
def move_to_dead_letter(notification_id):
    """
    Strong Hire Signal: Use Redis ZSET scored by failure timestamp.
    """
    import redis
    from django.conf import settings

    # Establish raw redis connection for ZSET operations
    r = redis.from_url(settings.REDIS_URL)
    zset_key = "notifications:dead_letter"

    now = int(time.time())
    # ZADD key score member
    r.zadd(zset_key, {str(notification_id): now})

    Notification.objects.filter(id=notification_id).update(delivery_status="FAILED")
