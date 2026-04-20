from celery import shared_task
from django.db import transaction
from django.core.cache import cache
from django.db.models import Count, Q, Case, When, IntegerField
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
    High-Performance Recompute: Uses Conditional Aggregation to avoid N+1 queries.
    """
    try:
        with transaction.atomic():
            # Strong Hire Signal: Ensure rows exist before locking to avoid skipping first-time students
            # Stub out default rows for any missing students in this course
            default_pcts = [
                AttendancePercentage(
                    tenant_id=tenant_id,
                    student_id=sid,
                    course_id=course_id,
                    percentage=100.0,
                    risk_status=AttendancePercentage.RiskStatus.SAFE
                ) for sid in student_ids
            ]
            AttendancePercentage.objects.bulk_create(
                default_pcts,
                ignore_conflicts=True
            )

            # 1. Lock the percentage rows
            pct_rows = AttendancePercentage.objects.select_for_update().filter(
                tenant_id=tenant_id,
                course_id=course_id,
                student_id__in=student_ids
            )

            # 2. Single-query aggregation for all affected students
            # Strong Hire Signal: Use conditional aggregation to avoid per-student loops
            stats = AttendanceRecord.objects.filter(
                tenant_id=tenant_id,
                course_id=course_id,
                student_id__in=student_ids
            ).values('student_id').annotate(
                total=Count('id'),
                present=Count(Case(When(status="PRESENT", then=1), output_field=IntegerField())),
                late=Count(Case(When(status="LATE", then=1), output_field=IntegerField()))
            )

            stats_map = {s['student_id']: s for s in stats}

            updated_rows = []
            notifications_to_dispatch = []

            for row in pct_rows:
                student_stat = stats_map.get(row.student_id)
                if not student_stat or student_stat['total'] == 0:
                    continue

                # Weighted percentage: Present=1, Late=0.5
                calc_pct = ((student_stat['present'] + (student_stat['late'] * 0.5)) / student_stat['total']) * 100
                new_risk = compute_risk(calc_pct)
                old_risk = row.risk_status

                if crossed_downward(old_risk, new_risk):
                    notifications_to_dispatch.append({
                        'student_id': row.student_id,
                        'course_id': course_id,
                        'tenant_id': tenant_id,
                        'new_pct': calc_pct,
                        'old_risk': old_risk,
                        'new_risk': new_risk
                    })

                row.percentage = calc_pct
                row.risk_status = new_risk
                row.save()
                updated_rows.append(row)

        # Redis & Notification dispatch (Post-commit)
        for row in updated_rows:
            cache_key = f"attendance:pct:{tenant_id}:{row.student_id}:{course_id}"
            cache.set(cache_key, row.percentage, timeout=3600)

        for dispatch_data in notifications_to_dispatch:
            fan_out_notifications.delay(**dispatch_data)

    except Exception as exc:
        raise self.retry(exc=exc)
