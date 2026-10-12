"""Synthetic fault injection, registered only in the audit background worker."""
import os, sys, time
sys.path.insert(0,'/app')
from app.celery_app import celery_app

@celery_app.task(name='audit.cpu',bind=True)
def cpu(self, seconds=180):
    import redis
    client=redis.Redis.from_url(os.environ['TEMPO_REDIS_URL'])
    client.set('audit:running:'+self.request.id,'cpu',ex=300)
    deadline=time.monotonic()+seconds
    iterations=0
    while time.monotonic()<deadline:
        iterations+=sum(number*number for number in range(1000))
    return iterations

@celery_app.task(name='audit.database',bind=True)
def database(self, seconds=180):
    import redis
    from app import postgres_store
    client=redis.Redis.from_url(os.environ['TEMPO_REDIS_URL'])
    client.set('audit:running:'+self.request.id,'database',ex=300)
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            with postgres_store.connection(background=True) as connection:
                connection.execute_native('SELECT sum(number) FROM generate_series(1,2000000) number')
        except Exception as error:
            client.hincrby('audit:database-errors',type(error).__name__,1)
    return True

if __name__=='__main__':
    celery_app.worker_main(['worker','--queues=background','--concurrency=1','--prefetch-multiplier=1','--loglevel=INFO'])
