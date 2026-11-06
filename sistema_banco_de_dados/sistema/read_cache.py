"""Cache curto e limitado de leituras; gravações confirmadas invalidam os resultados."""
from collections import OrderedDict
from copy import deepcopy
import threading
import time


class ReadCache:
    def __init__(self, ttl=5, max_entries=96, clock=time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._generation = 0
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self._entries.clear()

    def get(self, key, loader, generation):
        # O carregamento não segura o bloqueio nem prende consultas diferentes.
        with self._lock:
            current = self._generation
            entry = self._entries.get(key) if generation == current else None
            if entry is not None and self.clock() < entry[0]:
                self._entries.move_to_end(key)
                return deepcopy(entry[1])
        value = loader()
        with self._lock:
            # Uma transação iniciada antes de uma gravação não repovoa o cache.
            if generation == current == self._generation:
                self._entries[key] = (self.clock() + self.ttl, deepcopy(value))
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
        return value
