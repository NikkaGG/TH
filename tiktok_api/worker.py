from celery import Celery

from .config import get_settings
from . import jobs

settings = get_settings()
celery = Celery("lead_hunter", broker=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=240,
    task_time_limit=300,
    broker_transport_options={"visibility_timeout": 600},
    beat_schedule={"dispatch-outbox": {"task": "lead_hunter.tick", "schedule": 15.0}},
)


@celery.task(name="lead_hunter.process")
def process(job_id):
    jobs.process(job_id)


@celery.task(name="lead_hunter.tick")
def tick():
    jobs.schedule_due()
    jobs.dispatch(lambda job_id: process.apply_async(args=[job_id]))
