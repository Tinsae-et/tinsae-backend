from celery import shared_task

from .services import expire_confirmation_requests


@shared_task
def expire_confirmation_requests_task():
    return expire_confirmation_requests()
