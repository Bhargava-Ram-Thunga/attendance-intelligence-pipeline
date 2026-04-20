from celery import shared_task, group
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
from .models import Notification, Student, Parent, StudentCounselorAssignment
from django.core.management import call_command

def resolve_targets(student_id, course_id, tenant_id):
    """
    Resolves all users who should be notified.
    Returns a list of user_ids.
    """
    targets = []

    # 1. Student
    try:
        student = Student.objects.get(id=student_id, tenant_id=tenant_id)
        targets.append(student.user_id)
    except Student.DoesNotExist:
        return [] # Critical failure if student not found

    # 2. Parent (Optional)
    parents = Parent.objects.filter(student_id=student_id, tenant_id=tenant_id)
    for parent in parents:
        targets.append(parent.user_id)

    # 3. Counselor (Optional)
    counselors = StudentCounselorAssignment.objects.filter(
        student_id=student_id,
        course_id=course_id,
        tenant_id=tenant_id
    ).values_list('counselor_id', flat=True)

    # Counselor IDs are linked to User model via Counselor model
    # Assuming Counselor.user_id is the mapping
    from .models import Counselor # Local import to avoid circularity if needed
    counselor_users = Counselor.objects.filter(id__in=counselors).values_list('user_id', flat=True)
    targets.extend(list(counselor_users))

    return list(set(targets)) # De-duplicate

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    countdown=60
)
def fan_out_notifications(self, student_id, course_id, tenant_id, new_pct, old_risk, new_risk):
    """
    Creates Notification records and triggers parallel delivery.
    """
    targets = resolve_targets(student_id, course_id, tenant_id)
    if not targets:
        return

    # Deduplication key: student + course + risk event + date
    # Ensures we don't spam the same risk crossing multiple times a day
    event_date = timezone.now().date()
    dedup_key = f"risk_{student_id}_{course_id}_{old_risk}_{new_risk}_{event_date}"

    # Check for existing notification to avoid duplicates
    if Notification.objects.filter(dedup_key=dedup_key).exists():
        return

    # Bulk create notifications
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

    # Parallelize delivery using Celery group
    job = group(deliver_notification.s(notif.id) for notif in created_notifs)
    job.apply_async()

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    countdown=60
)
def deliver_notification(self, notification_id):
    """
    Handles the actual delivery: Redis counter increment -> WebSocket push.
    """
    try:
        notif = Notification.objects.get(id=notification_id)
        if notif.delivery_status == "DELIVERED":
            return

        # 1. Atomic Redis Unread Counter Increment
        # Key: unread:{user_id}
        unread_key = f"unread:{notif.recipient_user_id}"
        unread_count = cache.incr(unread_key, 1)

        # 2. WebSocket Push via Channels
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

        # Group name: notifications_{user_id}
        group_name = f"notifications_{notif.recipient_user_id}"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {"type": "notification.new", **payload}
        )

        # 3. Update status
        notif.delivery_status = "DELIVERED"
        notif.save(update_fields=["delivery_status"])

    except Exception as exc:
        # On final failure, the Celery on_failure hook (or a custom handler)
        # will move this to the Dead-Letter ZSET.
        if self.request.retries == self.max_retries:
            move_to_dead_letter.delay(notification_id)
        raise self.retry(exc=exc)

@shared_task
def move_to_dead_letter(notification_id):
    """
    Moves failed notification to Redis ZSET for recovery.
    Key: notifications:dead_letter (Score: Unix Timestamp)
    """
    from django.utils import timezone
    import time

    now = int(time.time())
    # Store in ZSET: score=timestamp, member=id
    cache.set(f"zadd:notifications:dead_letter:{notification_id}", now)
    # Note: Real implementation uses redis-py's zadd directly
    # For this scaffold, we assume the cache backend supports ZSETs or use a raw redis client

    Notification.objects.filter(id=notification_id).update(delivery_status="FAILED")
