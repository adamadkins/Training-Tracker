#!/usr/bin/env python
"""Run RQ worker for background jobs (email, etc.). Set REDIS_URL in env."""
import os
from redis import Redis
from rq import Worker

redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PRIVATE_URL")
if not redis_url:
    raise SystemExit("REDIS_URL or REDIS_PRIVATE_URL is required to run the worker.")

conn = Redis.from_url(redis_url)
worker = Worker(["default"], connection=conn)
worker.work()
