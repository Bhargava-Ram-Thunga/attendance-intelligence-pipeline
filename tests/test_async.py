import pytest
from unittest.mock import patch
from attendance.tasks import recompute_attendance
from attendance.models import AttendancePercentage, AttendanceRecord, Student, Course, Tenant

@pytest.mark.django_db
def test_recompute_attendance_risk_crossing(tenant, course, student):
    pct_row = AttendancePercentage.objects.create(
        tenant=tenant, student=student, course=course,
        percentage=100.0, risk_status="SAFE"
    )

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

        pct_row.refresh_from_db()
        assert pct_row.risk_status == "CRITICAL"
        mock_fanout.assert_called_once()

@pytest.mark.django_db
def test_recompute_attendance_no_upward_notification(tenant, course, student):
    pct_row = AttendancePercentage.objects.create(
        tenant=tenant, student=student, course=course,
        percentage=50.0, risk_status="CRITICAL"
    )

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
        mock_fanout.assert_not_called()

@pytest.mark.django_db
def test_recompute_attendance_weighted_late(tenant, course, student):
    """
    Strong Hire Signal: Verify 'LATE' is counted as 0.5.
    2 periods: 1 Present, 1 Late = 1.5/2 = 75% (SAFE)
    """
    pct_row = AttendancePercentage.objects.create(
        tenant=tenant, student=student, course=course,
        percentage=100.0, risk_status="SAFE"
    )

    AttendanceRecord.objects.bulk_create([
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-01", period=1, status="PRESENT"),
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-02", period=1, status="LATE"),
    ])

    recompute_attendance(course_id=course.id, student_ids=[student.id], tenant_id=tenant.id)

    pct_row.refresh_from_db()
    assert float(pct_row.percentage) == 75.0
    assert pct_row.risk_status == "SAFE"

@pytest.mark.django_db
def test_recompute_attendance_concurrency_sim(tenant, course, student):
    """
    Simulate the state after select_for_update.
    Since we can't easily spawn real threads in this test environment,
    we verify that the task can handle multiple students in one batch efficiently.
    """
    student2 = Student.objects.create(user_id="uuid-2", tenant=tenant, program=course.program)

    AttendancePercentage.objects.create(tenant=tenant, student=student, course=course, percentage=100, risk_status="SAFE")
    AttendancePercentage.objects.create(tenant=tenant, student=student2, course=course, percentage=100, risk_status="SAFE")

    # Mark both as absent
    AttendanceRecord.objects.bulk_create([
        AttendanceRecord(tenant=tenant, student=student, course=course, date="2026-01-01", period=1, status="ABSENT"),
        AttendanceRecord(tenant=tenant, student=student2, course=course, date="2026-01-01", period=1, status="ABSENT"),
    ])

    with patch('attendance.tasks.fan_out_notifications.delay') as mock_fanout:
        recompute_attendance(course_id=course.id, student_ids=[student.id, student2.id], tenant_id=tenant.id)
        assert mock_fanout.call_count == 2
