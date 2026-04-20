import pytest
import uuid
from django.core.cache import cache
from attendance.models import Tenant, Program, Course, Student

@pytest.fixture
def tenant():
    return Tenant.objects.create(name="Test University")

@pytest.fixture
def program(tenant):
    return Program.objects.create(name="Computer Science", tenant=tenant)

@pytest.fixture
def course(tenant, program):
    return Course.objects.create(name="Distributed Systems", tenant=tenant, program=program)

@pytest.fixture
def student(tenant, program):
    return Student.objects.create(
        user_id=uuid.uuid4(),
        tenant=tenant,
        program=program
    )

@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()
