from django.db import models

class Notification(models.Model):
    class DeliveryStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Failed"
        PERMANENTLY_FAILED = "PERMANENTLY_FAILED", "Permanently Failed"

    class Category(models.TextChoices):
        ATTENDANCE = "attendance", "Attendance"
        GENERAL = "general", "General"

    # Tenant isolation
    tenant = models.ForeignKey(
        'attendance.Tenant',
        on_delete=models.PROTECT,
        db_index=False
    )

    recipient_user_id = models.UUIDField()
    title = models.CharField(max_length=255)
    body = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    delivery_status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING
    )

    # For idempotency during fan-out
    dedup_key = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Lead with tenant_id for partition-friendly access
        indexes = [
            models.Index(fields=['tenant', 'recipient_user_id', 'delivery_status'], name='idx_notif_tenant_user_status'),
        ]
        unique_together = ('dedup_key', 'recipient_user_id')

    def __str__(self):
        return f"Notif to {self.recipient_user_id} - {self.delivery_status}"

class WeeklyAttendanceDigest(models.Model):
    tenant = models.ForeignKey(
        'attendance.Tenant',
        on_delete=models.PROTECT,
        db_index=False
    )
    student = models.ForeignKey(
        'attendance.Student',
        on_delete=models.CASCADE
    )
    week_start = models.DateField()
    summary_data = models.JSONField() # Flexible for future analytics
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'student', 'week_start')
        indexes = [
            models.Index(fields=['tenant', 'student', 'week_start'], name='idx_digest_tenant_std_week'),
        ]

    def __str__(self):
        return f"Digest {self.student} - Week {self.week_start}"
