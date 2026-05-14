import { randomUUID } from "node:crypto";

const jobs = new Map();
const MAX_EVENTS = 2000;
const TTL_MS = 1000 * 60 * 30;

export function createJob() {
  const id = randomUUID();
  const job = {
    id,
    events: [],
    listeners: new Set(),
    done: false,
    error: null,
    createdAt: Date.now(),
  };
  jobs.set(id, job);
  return job;
}

export function getJob(id) {
  return jobs.get(id);
}

export function pushEvent(job, event) {
  if (job.events.length >= MAX_EVENTS) return;
  job.events.push(event);
  for (const fn of job.listeners) {
    try {
      fn(event);
    } catch {}
  }
  if (event.type === "done" || event.type === "fatal") {
    job.done = true;
    setTimeout(() => jobs.delete(job.id), TTL_MS);
  }
}

export function subscribe(job, fn) {
  job.listeners.add(fn);
  return () => job.listeners.delete(fn);
}

export function runJob(work) {
  const job = createJob();
  const onProgress = (event) => pushEvent(job, event);
  (async () => {
    try {
      await work(onProgress);
      if (!job.done) pushEvent(job, { type: "done", message: "Concluído." });
    } catch (err) {
      pushEvent(job, { type: "fatal", message: err.message });
    }
  })();
  return job;
}
