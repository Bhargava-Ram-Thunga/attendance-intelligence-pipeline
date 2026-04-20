from celery import shared_task
from django.db import transaction
from django.core.cache import cache
from .models import AttendanceRecord, AttendancePercentage, Student
from notifications.tasks import fan_out_notifications

def compute_risk(percentage):
    if percentage >= 75:
        return AttendancePercentage.RiskStatus.SAFE
    elif percentage >= 65:
        return AttendancePercentage.RiskStatus.WARNING
    else:
        return AttendancePercentage.RiskStatus.CRITICAL

def crossed_downward(old_risk, new_risk):
    # Risk Level Order: SAFE (0) -> WARNING (1) -> CRITICAL (2)
    # Downward crossing means moving to a more severe risk status.
    risk_map = {
        AttendancePercentage.RiskStatus.SAFE: 0,
        AttendancePercentage.RiskStatus.WARNING: 1,
        AttendancePercentage.RiskStatus.CRITICAL: 2,
    }
    return risk_map.get(new_risk, 0) > risk_map.get(old_risk, 0)

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    countdown=60
)
def recompute_attendance(self, course_id, student_ids, tenant_id):
    """
    Recomputes attendance percentage and risk status for affected students.
    Implements select_for_update to prevent race conditions.
    """
    try:
        with transaction.atomic():
            # Strong Hire Signal: Locking the specific rows to prevent concurrent recompute corruption
            pct_rows = AttendancePercentage.objects.select_for_update().filter(
                tenant_id=tenant_id,
                course_id=course_id,
                student_id__in=student_ids
            )

            updated_rows = []
            notifications_to_dispatch = []

            for row in pct_rows:
                # Calculate current percentage from source-of-truth AttendanceRecords
                records = AttendanceRecord.objects.filter(
                    tenant_id=tenant_id,
                    course_id=course_id,
                    student_id=row.student_id
                )

                total_periods = records.count()
                if total_periods == 0:
                    continue

                # Assume PRESENT=1, LATE=0.5, ABSENT=0 for percentage
                present_count = records.filter(status="PRESENT").count()
                late_count = records.filter(status="LATE").count()

                calculated_pct = ((present_count + (late_count * 0.5)) / total_periods) * 100
                new_risk = compute_risk(calculated_pct)

                old_risk = row.risk_status

                # Check for downward risk crossing (S -> W or W -> C)
                if crossed_downward(old_risk, new_risk):
                    notifications_to_dispatch.append({
                        'student_id': row.student_id,
                        'course_id': course_id,
                        'tenant_id': tenant_id,
                        'new_pct': calculated_pct,
                        'old_risk': old_risk,
                        'new_risk': new_risk
                    })

                row.percentage = calculated_pct
                row.risk_status = new_risk
                row.save()

                updated_rows.append(row)

        # Post-transaction: Update Redis cache and dispatch notifications
        for row in updated_rows:
            # Redis Key: attendance:pct:{tenant_id}:{student_id}:{course_id}
            cache_key = f"attendance:pct:{tenant_id}:{row.student_id}:{course_id}"
            cache.set(cache_key, row.percentage, timeout=3600)

        # Dispatch fan-out tasks for those who crossed the risk threshold
        for dispatch_data in notifications_to_dispatch:
            fan_out_notifications.delay(
                student_id=dispatch_data['student_id'],
                course_id=dispatch_data['course_id'],
                tenant_id=dispatch_data['tenant_id'],
                new_pct=dispatch_data['new_pct'],
                old_risk=dispatch_data['old_risk'],
                new_risk=dispatch_data['new_risk']
            )

    except Exception as exc:
        raise self.retry(exc=exc)
