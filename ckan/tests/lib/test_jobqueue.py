# encoding: utf-8
u"""
Tests for ``ckan.lib.jobqueue`` (the storage of background jobs) and for
the forking path of ``ckan.lib.jobs.Worker`` that the RQ-era tests never
exercised.
"""
import datetime
import os
import tempfile
import time

import pytest

import ckan.lib.jobs as jobs
import ckan.lib.jobqueue as jobqueue
from ckan.lib.jobqueue.base import (
    Job, JobBackend, callable_path, resolve_callable,
)
from ckan.lib.jobqueue.postgres import PostgresJobBackend
from ckan.tests.helpers import RQTestBase, recorded_logs


def write_file(path, content):
    with open(path, "w") as f:
        f.write(content)


def sleep_job(seconds):
    time.sleep(seconds)


def stubborn_job(seconds):
    # ignores the soft timeout, so the worker has to kill it
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            time.sleep(deadline - time.monotonic())
        except jobs.JobTimeoutException:
            pass


def crash_job():
    os._exit(3)


def record_current_job(path):
    job = jobs.get_current_job()
    write_file(path, job.id if job else "none")


def slow_job_with_cleanup(path):
    try:
        time.sleep(30)
    except jobs.JobTimeoutException:
        write_file(path, "cleaned up")
        raise


class TestCallablePaths:
    def test_round_trip(self):
        path = callable_path(jobs.test_job)
        assert path == "ckan.lib.jobs:test_job"
        assert resolve_callable(path) is jobs.test_job

    def test_lambdas_are_rejected(self):
        with pytest.raises(ValueError):
            callable_path(lambda: None)


class TestRegistry:
    def test_default_backend(self):
        assert isinstance(jobqueue.get_backend(), PostgresJobBackend)

    @pytest.mark.ckan_config("ckan.jobs.backend", "nope")
    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown background jobs"):
            jobqueue.get_backend()

    def test_contract_is_abstract(self):
        with pytest.raises(NotImplementedError):
            JobBackend().fetch("x")


@pytest.mark.usefixtures("clean_queues")
class TestPostgresJobBackend:
    def setup_method(self):
        self.backend = jobqueue.get_backend()

    def enqueue(self, queue="q", **kwargs):
        options = dict(func_name=callable_path(jobs.test_job), args=[],
                       kwargs={}, meta={}, timeout=180)
        options.update(kwargs)
        return self.backend.enqueue(queue, **options)

    def test_enqueue_and_fetch(self):
        job = self.enqueue(args=[1, "two"], kwargs={"k": 3.0},
                           meta={"title": "t"})
        fetched = self.backend.fetch(job.id)
        assert fetched == job
        assert fetched.args == [1, "two"]
        assert fetched.kwargs == {"k": 3.0}
        assert fetched.meta == {"title": "t"}
        assert fetched.origin == "q"
        assert fetched.status == "queued"
        assert isinstance(fetched.created_at, datetime.datetime)

    def test_deterministic_id_replaces_job(self):
        self.enqueue(job_id="fixed", args=[1])
        self.enqueue(job_id="fixed", args=[2])
        assert [j.args for j in self.backend.list_jobs("q")] == [[2]]

    def test_pickled_arguments_keep_their_types(self):
        when = datetime.datetime(2026, 1, 2, 3, 4, 5)
        job = self.enqueue(args=[when], kwargs={"delta": datetime.timedelta(1)})
        fetched = self.backend.fetch(job.id)
        assert fetched.args == [when]
        assert fetched.kwargs == {"delta": datetime.timedelta(1)}

    def test_scheduled_jobs_are_not_listed_until_due(self):
        later = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        job = self.enqueue(scheduled_at=later)
        assert self.backend.list_jobs("q") == []
        assert self.backend.list_jobs("q", include_scheduled=True) == [job]
        assert self.backend.scheduled_job_ids("q") == [job.id]
        assert self.backend.dequeue(["q"], "w") is None

    def test_dequeue_order_and_queue_priority(self):
        second = self.enqueue(queue="b")
        first = self.enqueue(queue="a")
        third = self.enqueue(queue="a")
        claimed = [self.backend.dequeue(["a", "b"], "w") for _ in range(4)]
        assert claimed == [first, third, second, None]
        assert claimed[0].status == "started"
        assert claimed[0].worker == "w"
        assert self.backend.list_jobs("a") == []

    def test_finish_and_fail(self):
        done = self.enqueue()
        broken = self.enqueue()
        self.backend.dequeue(["q"], "w")
        self.backend.dequeue(["q"], "w")
        self.backend.finish(done.id)
        self.backend.fail(broken.id, "boom")
        assert self.backend.fetch(done.id) is None
        failed = self.backend.fetch(broken.id)
        assert failed.status == "failed"
        assert failed.error == "boom"
        assert failed.ended_at is not None
        # failed jobs do not show up as queued anywhere
        assert self.backend.queues("") == []

    def test_fail_stale(self):
        job = self.enqueue(timeout=1)
        self.backend.dequeue(["q"], "w")
        assert self.backend.fail_stale(datetime.datetime.utcnow()) == []
        far_future = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        assert self.backend.fail_stale(far_future) == [job.id]
        assert self.backend.fetch(job.id).status == "failed"

    def test_queues_and_empty(self):
        self.enqueue(queue="ckan:s:one")
        self.enqueue(queue="ckan:s:two")
        self.enqueue(queue="other:x")
        assert self.backend.queues("ckan:s:") == ["ckan:s:one", "ckan:s:two"]
        assert self.backend.empty("ckan:s:one") == 1
        assert self.backend.queues("ckan:s:") == ["ckan:s:two"]
        self.backend.clear_all()
        assert self.backend.queues("") == []

    def test_save_meta_and_delete(self):
        job = self.enqueue()
        job.meta["title"] = "changed"
        job.save()
        assert self.backend.fetch(job.id).meta == {"title": "changed"}
        job.delete()
        assert self.backend.fetch(job.id) is None


@pytest.mark.usefixtures("clean_queues")
class TestForkingWorker:
    """The real thing: jobs run in a forked child process."""

    def test_job_runs_in_a_child_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.txt")
            jobs.enqueue(write_file, [path, "done"])
            assert jobs.Worker().work(burst=True) is True
            with open(path) as f:
                assert f.read() == "done"
        assert jobs.get_all_queues() == []

    def test_soft_timeout_stops_the_job(self):
        job = jobs.enqueue(sleep_job, [30], rq_kwargs={"timeout": 1})
        started = time.monotonic()
        jobs.Worker().work(burst=True)
        assert time.monotonic() - started < 10
        failed = jobqueue.get_backend().fetch(job.id)
        assert failed.status == "failed"
        assert "timed out" in failed.error

    def test_hard_timeout_kills_a_stubborn_job(self, monkeypatch):
        monkeypatch.setattr(jobs, "TIMEOUT_GRACE", 1.0)
        job = jobs.enqueue(stubborn_job, [60], rq_kwargs={"timeout": 1})
        with recorded_logs("ckan.lib.jobs") as logs:
            started = time.monotonic()
            jobs.Worker().work(burst=True)
            assert time.monotonic() - started < 10
        logs.assert_log("error", "killing it")
        failed = jobqueue.get_backend().fetch(job.id)
        assert failed.status == "failed"
        assert "timed out" in failed.error

    def test_crashing_child_marks_job_failed(self):
        job = jobs.enqueue(crash_job)
        jobs.Worker().work(burst=True)
        failed = jobqueue.get_backend().fetch(job.id)
        assert failed.status == "failed"
        assert "exited" in failed.error

    def test_get_current_job_inside_the_job(self):
        assert jobs.get_current_job() is None
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "job-id.txt")
            job = jobs.enqueue(record_current_job, [path])
            jobs.Worker().work(burst=True)
            with open(path) as f:
                assert f.read() == job.id
        assert jobs.get_current_job() is None

    def test_soft_timeout_lets_the_job_clean_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cleanup.txt")
            job = jobs.enqueue(slow_job_with_cleanup, [path],
                               rq_kwargs={"timeout": 1})
            jobs.Worker().work(burst=True)
            with open(path) as f:
                assert f.read() == "cleaned up"
        failed = jobqueue.get_backend().fetch(job.id)
        assert failed.status == "failed"
        assert "JobTimeoutException" in failed.error

    def test_exception_is_recorded(self):
        job = jobs.enqueue(resolve_callable, ["nope:nothing"])
        jobs.Worker().work(burst=True)
        failed = jobqueue.get_backend().fetch(job.id)
        assert failed.status == "failed"
        assert "ModuleNotFoundError" in failed.error


class TestQueueObject(RQTestBase):
    def test_enqueue_in_and_fetch_job(self):
        queue = jobs.get_queue("later")
        job = queue.enqueue_in(datetime.timedelta(hours=1), jobs.test_job,
                               1, job_id="my-id")
        assert job.id == "my-id"
        assert queue.fetch_job("my-id") == job
        assert jobs.get_queue("other").fetch_job("my-id") is None
        assert queue.scheduled_job_registry.get_job_ids() == ["my-id"]
        assert queue.jobs == []

    def test_enqueue_at(self):
        queue = jobs.get_queue()
        when = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
        job = queue.enqueue_at(when, jobs.test_job)
        assert job.scheduled_at == datetime.datetime(2030, 1, 1)

    def test_no_timeout(self):
        job = jobs.enqueue(jobs.test_job, rq_kwargs={"timeout": -1})
        assert job.timeout == -1
        assert jobs.Worker().work(burst=True)


def test_job_equality_by_id():
    a = Job(id="1", origin="q", func_name="x:y")
    b = Job(id="1", origin="other", func_name="x:z", args=[1])
    assert a == b and hash(a) == hash(b)
    assert a != Job(id="2", origin="q", func_name="x:y")
