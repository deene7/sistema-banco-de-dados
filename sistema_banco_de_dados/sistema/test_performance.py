import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import threading
import unittest
from unittest.mock import Mock
from home_summary import HomeSummary
from import_jobs import ImportManager
from read_cache import ReadCache


class ReadCacheTests(unittest.TestCase):
    def test_expiry_capacity_and_independent_copies(self):
        now = [0]
        cache = ReadCache(ttl=5, max_entries=2, clock=lambda: now[0])
        loader = Mock(return_value=[{'id': 1}])
        first = cache.get('a', loader, cache.generation)
        first[0]['id'] = 999
        self.assertEqual(cache.get('a', loader, cache.generation), [{'id': 1}])
        self.assertEqual(loader.call_count, 1)
        now[0] = 6
        cache.get('a', loader, cache.generation)
        self.assertEqual(loader.call_count, 2)
        cache.get('b', loader, cache.generation)
        cache.get('c', loader, cache.generation)
        cache.get('a', loader, cache.generation)
        self.assertEqual(loader.call_count, 5)

    def test_commit_during_load_cannot_repopulate_stale_data(self):
        cache = ReadCache()
        generation = cache.generation
        def stale():
            cache.invalidate()
            return 'old'
        self.assertEqual(cache.get('page', stale, generation), 'old')
        fresh = Mock(return_value='new')
        self.assertEqual(cache.get('page', fresh, cache.generation), 'new')
        self.assertEqual(fresh.call_count, 1)
        # Uma requisição cujo snapshot é anterior também não reutiliza o cache novo.
        self.assertEqual(cache.get('page', lambda: 'snapshot', generation), 'snapshot')
        self.assertEqual(cache.get('page', fresh, cache.generation), 'new')
        self.assertEqual(fresh.call_count, 1)

    def test_failure_is_not_cached(self):
        cache = ReadCache()
        with self.assertRaises(ValueError):
            cache.get('a', Mock(side_effect=ValueError), cache.generation)
        self.assertEqual(cache.get('a', lambda: 2, cache.generation), 2)


class BackgroundTests(unittest.TestCase):
    def test_cold_home_does_not_wait_for_summary_lock(self):
        summary = HomeSummary({})
        with summary.lock:
            self.assertIsNone(summary.get(wait=False))
        summary.data = {'total': 12}
        summary.updated = summary.attempted = __import__('time').monotonic()
        self.assertEqual(summary.get(wait=False), {'total': 12})

    def test_import_dispatch_returns_while_processing_and_queues_duplicates_once(self):
        manager = ImportManager({})
        started = threading.Event()
        release = threading.Event()
        calls = []
        def prepare(job_id):
            calls.append(job_id)
            started.set()
            release.wait(3)
        manager.prepare = prepare
        try:
            self.assertTrue(manager.dispatch(1, 'preparar'))
            self.assertTrue(started.wait(1))
            self.assertFalse(release.is_set())
            self.assertTrue(manager.active(1))
            self.assertTrue(manager.dispatch(2, 'preparar'))
            self.assertFalse(manager.dispatch(2, 'preparar'))
            self.assertEqual(calls, [1])
            snapshot=manager.snapshot()
            self.assertEqual(snapshot[1]['situacao'],'executando')
            self.assertEqual(snapshot[2]['situacao'],'fila')
            self.assertEqual(snapshot[2]['posicao'],1)
        finally:
            release.set()
            manager.worker.shutdown(wait=True)
        self.assertEqual(calls, [1, 2])
        self.assertFalse(manager.active(1))
        self.assertFalse(manager.active(2))
