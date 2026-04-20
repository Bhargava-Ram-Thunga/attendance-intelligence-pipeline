import pytest
from django.urls import reverse
from rest_framework import status
from attendance.models import AttendanceRecord, Student

@pytest.mark.django_db
def test_bulk_mark_success(client, tenant, course, student):
    url = reverse('bulk-mark') # Assuming URL name is 'bulk-mark'
    payload = {
        "course_id": str(course.id),
        "date": "2026-04-21",
        "period": 1,
        "records": [
            {"student_id": str(student.id), "status": "PRESENT"}
        ]
    }
    response = client.post(url, payload, format='json')
    assert response.status_code == status.HTTP_202_ACCEPTED

@pytest.mark.django_db
def test_bulk_mark_payload_limit(client, tenant, course):
    url = reverse('bulk-mark')
    # Create 121 students to trigger the limit
    records = [{"student_id": str(uuid.uuid4()), "status": "PRESENT"} for _ in range(121)]
    payload = {
        "course_id": str(course.id),
        "date": "2026-04-21",
        "period": 1,
        "records": records
    }
    response = client.post(url, payload, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST

@pytest.mark.django_db
def test_bulk_mark_idempotency(client, tenant, course, student):
    url = reverse('bulk-mark')
    payload = {
        "course_id": str(course.id),
        "date": "2026-04-21",
        "period": 1,
        "records": [{"student_id": str(student.id), "status": "PRESENT"}]
    }

    # Submit twice
    client.post(url, payload, format='json')
    client.post(url, payload, format='json')

    # Check that only one record exists
    assert AttendanceRecord.objects.filter(
        tenant=tenant, student=student, course=course, date="2026-04-21", period=1
    ).count() == 1
