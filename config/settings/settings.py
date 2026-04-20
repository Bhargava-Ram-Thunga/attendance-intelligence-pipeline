from django.conf import settings
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-placeholder-key')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'channels',
    'django_celery_beat',
    'attendance',
    'notifications',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'eduerp',
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': 'db',
        'PORT': '5432',
    }
}

# Redis & Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True

from celery.schedules import crontab

# Celery Beat Schedule (Code-based to avoid DatabaseScheduler startup failure)
CELERY_BEAT_SCHEDULE = {
    'nightly-attendance-digest': {
        'func': 'attendance.tasks.nightly_digest',
        # 11:00 PM IST is 17:30 UTC.
        'schedule': crontab(hour=17, minute=30),
    },
    'dead-letter-retry': {
        'func': 'notifications.recovery.dead_letter_retry',
        'schedule': crontab(minute='0,30'),
    },
}

# Channels
ASGI_APPLICATION = 'config.asgi.application'
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [os.environ.get('REDIS_URL', 'redis://redis:6379/0')],
        },
    },
}

# Custom Settings
JWT_SECRET = os.environ.get('JWT_SECRET', 'change-this-to-a-secure-random-secret-key-in-production')
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
