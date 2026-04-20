import pytest
from unittest.mock import patch
from attendance.tasks import recompute_attendance, compute_risk
from attendance.models import AttendancePercentage, AttendanceRecord, Student, Course, Tenant

@pytest.mark.django_db
def test_recompute_attendance_risk_crossing(tenant, course, student):
    # Set initial state to SAFE (100%)
    pct_row = AttendancePercentage.objects.create(
        tenant=tenant, student=student, course=course,
        percentage=100.0, risk_status="SAFE"
    )

    # Add records that bring it down to 50% (CRITICAL)
    AttendanceRecord.objects.bulk_create([
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-01", period=1, status="PRESENT"),
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-02", period=1, status="ABSENT"),
    ])

    with patch('attendance.tasks.fan_out_notifications.delay') as mock_fanout:
        recompute_attendance(
            course_id=course.id,
            student_ids=[student.id],
            tenant_id=tenant.id
        )

        # Verify risk status updated
        pct_row.refresh_from_db()
        assert pct_row.risk_status == "CRITICAL"

        # Verify fan_out was triggered due to downward crossing
        mock_fanout.assert_called_once()

@pytest.mark.django_db
def test_recompute_attendance_no_upward_notification(tenant, course, student):
    # Set initial state to CRITICAL (50%)
    pct_row = AttendancePercentage.objects.create(
        tenant=tenant, student=student, course=course,
        percentage=50.0, risk_status="CRITICAL"
    )

    # Add records that bring it up to 100% (SAFE)
    AttendanceRecord.objects.bulk_create([
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-01", period=1, status="PRESENT"),
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-02", period=1, status="PRESENT"),
    ])

    with patch('attendance.tasks.fan_out_notifications.delay') as mock_fanout:
        recompute_attendance(
            course_id=course.id,
            student_ids=[student.id],
            tenant_id=tenant.id
        )

        pct_row.refresh_from_db()
        assert pct_row.risk_status == "SAFE"

        # Verify NO fan_out for upward recovery
        mock_fanout.assert_not_called()
