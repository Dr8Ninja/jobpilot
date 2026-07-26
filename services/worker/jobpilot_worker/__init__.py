"""Pipeline stages.

Every stage is a plain function with no framework in its signature. Celery tasks
and CLI commands are thin wrappers over the same functions, so tests need neither
a broker nor a running API.
"""
