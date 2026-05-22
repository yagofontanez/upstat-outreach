"""Jobs em background + pub/sub para SSE — equivalente a lib/jobs.js.

Como scrape/personalize/send são síncronos e bloqueantes, cada job roda numa
thread daemon e publica eventos para os assinantes (streams SSE) via filas.
"""

import queue
import threading
import time
import uuid

MAX_EVENTS = 2000
TTL_S = 60 * 30

_jobs = {}


class Job:
    def __init__(self):
        self.id = str(uuid.uuid4())
        self.events = []
        self.listeners = set()  # conjunto de queue.Queue
        self.done = False
        self.created_at = time.time()
        self.lock = threading.Lock()


def create_job():
    job = Job()
    _jobs[job.id] = job
    return job


def get_job(job_id):
    return _jobs.get(job_id)


def push_event(job, event):
    with job.lock:
        if len(job.events) >= MAX_EVENTS:
            return
        job.events.append(event)
        listeners = list(job.listeners)
        if event.get("type") in ("done", "fatal"):
            job.done = True
    for q in listeners:
        try:
            q.put(event)
        except Exception:
            pass
    if job.done:
        threading.Timer(TTL_S, lambda: _jobs.pop(job.id, None)).start()


def subscribe(job):
    """Assina e retorna (eventos_ja_emitidos, fila, job_concluido) de forma atômica."""
    q = queue.Queue()
    with job.lock:
        buffered = list(job.events)
        done = job.done
        if not done:
            job.listeners.add(q)
    return buffered, q, done


def unsubscribe(job, q):
    with job.lock:
        job.listeners.discard(q)


def run_job(work):
    job = create_job()

    def on_progress(event):
        push_event(job, event)

    def runner():
        try:
            work(on_progress)
            if not job.done:
                push_event(job, {"type": "done", "message": "Concluído."})
        except Exception as err:
            push_event(job, {"type": "fatal", "message": str(err)})

    threading.Thread(target=runner, daemon=True).start()
    return job
