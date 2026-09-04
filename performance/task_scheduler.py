"""
Performance Engine - Task Scheduler
====================================

Roadmap Phase 13 - "Multi-threaded task scheduler", "Async asset loading".

`core_engine.tile_engine.TileEngine` zaten kendi `ThreadPoolExecutor`'ını
kullanıyordu (Phase 1); bu modül o deseni **genelleştirerek** tüm motorun
paylaşabileceği tek bir öncelik kuyruklu görev zamanlayıcısına çevirir:

  * `TaskScheduler`: `concurrent.futures.ThreadPoolExecutor` üzerine kurulu,
    önceliğe göre sıralanmış (`heapq`) bir görev kuyruğu. Aynı önceliğe
    sahip görevler gönderim (submission) sırasına göre çalışır (FIFO
    tie-break - `heapq` stabilite garantisi için monotonik bir sayaç
    kullanılır, çünkü `Task` nesneleri karşılaştırılabilir değildir).
  * `AsyncAssetLoader`: Tekil bir asset'i (texture, mesh, tile...) diskten/
    bir `loader_fn` ile senkron okuyup arka planda yükleyen, sonucu
    `Future` olarak dönen ince bir sarmalayıcı. Aynı `key` için birden
    fazla `load()` çağrısı yapılırsa aynı `Future` paylaşılır (de-dup -
    çakışan yükleme istekleri disk/ağ I/O'sunu tekrarlamaz).

Bilerek stdlib-only: gerçek bir GPU/oyun motoru render backend'i olmadığı
için burada "iş zamanlayıcı + I/O eşzamansızlığı" iskeleti sağlanır; gerçek
GPU submission (Vulkan/DirectX command buffer vb.) render backend'ine özel
kalır ve bu modülün kapsamı dışındadır.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(order=False)
class _QueuedTask:
    priority: int
    seq: int
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict
    future: Future = field(compare=False)
    label: str = field(default="task", compare=False)

    def __lt__(self, other: "_QueuedTask") -> bool:
        # Düşük priority sayısı = yüksek öncelik (heapq min-heap).
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.seq < other.seq


class TaskScheduler:
    """Öncelik kuyruklu, thread-pool destekli görev zamanlayıcı.

    `submit(fn, *args, priority=0, label="task")` -> `Future`. Düşük
    `priority` değeri önce çalışır (0 = en yüksek öncelik). Görevler bir
    işçi thread'i boşaldığında kuyruktan çekilir; bu yüzden anlık öncelik
    sırası yalnızca *o an bekleyen* görevler arasında garanti edilir (bir
    görev zaten bir işçiye atanmışsa, sonradan gelen daha yüksek öncelikli
    bir görev onu kesintiye uğratamaz - kooperatif değil, kuyruk-seviyeli
    zamanlama).
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._heap: list[_QueuedTask] = []
        self._lock = threading.Lock()
        self._counter = itertools.count()
        self._pending = 0
        self._workers = max_workers
        self._active = 0

    def submit(
        self, fn: Callable[..., Any], *args: Any, priority: int = 0, label: str = "task", **kwargs: Any
    ) -> Future:
        future: Future = Future()
        task = _QueuedTask(priority, next(self._counter), fn, args, kwargs, future, label)
        with self._lock:
            heapq.heappush(self._heap, task)
        self._drain()
        return future

    def _drain(self) -> None:
        """Boşta işçi kapasitesi varsa kuyruktaki en öncelikli görevleri
        thread pool'a gönderir."""
        with self._lock:
            while self._heap and self._active < self._workers:
                task = heapq.heappop(self._heap)
                self._active += 1
                self._executor.submit(self._run, task)

    def _run(self, task: _QueuedTask) -> None:
        try:
            result = task.fn(*task.args, **task.kwargs)
        except Exception as exc:  # noqa: BLE001 - future'a hatayı taşı
            task.future.set_exception(exc)
        else:
            task.future.set_result(result)
        finally:
            with self._lock:
                self._active -= 1
            self._drain()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._heap)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)


class AsyncAssetLoader:
    """Tek bir `key` için tekrar eden yükleme isteklerini birleştiren
    (de-dup) asenkron asset yükleyici. Gerçek I/O `loader_fn(key)` ile
    yapılır (dosya okuma, ağ isteği, parse...); bu sınıf sadece scheduling
    ve cache-of-in-flight sorumluluğunu üstlenir."""

    def __init__(self, scheduler: TaskScheduler, loader_fn: Callable[[str], Any]) -> None:
        self._scheduler = scheduler
        self._loader_fn = loader_fn
        self._inflight: dict[str, Future] = {}
        self._cache: dict[str, Any] = {}
        # RLock: `add_done_callback` bir Future zaten tamamlanmışsa
        # callback'i *hemen, aynı thread'de* çalıştırır; bu da load()
        # hâlâ kilidi tutarken _on_done'ın aynı kilidi tekrar almaya
        # çalışmasına yol açabilir. Sıradan Lock ile bu kendi kendini
        # kilitler (deadlock); RLock aynı thread'in tekrar girişine izin
        # verir.
        self._lock = threading.RLock()

    def load(self, key: str, priority: int = 0) -> Future:
        with self._lock:
            if key in self._cache:
                done: Future = Future()
                done.set_result(self._cache[key])
                return done
            if key in self._inflight:
                return self._inflight[key]

            # Placeholder Future'ı iş henüz zamanlanmadan ÖNCE, kilit
            # altında kaydediyoruz. Aksi halde (scheduler'a submit edip
            # dönen Future'ı kaydetmeden önce iş çok hızlı biterse) aynı
            # anahtar için ikinci bir `load()` çağrısı henüz kaydedilmemiş
            # inflight girdisini bulamayıp gereksiz yere yeniden yükleme
            # başlatabilir - ya da tam tersi, tamamlanmış işin callback'i
            # kaydı bu satırdan önce silip sonra bu satır onu "askıda"
            # bırakabilir (yarış durumu / stale inflight entry).
            result_future: Future = Future()
            self._inflight[key] = result_future

        def _work() -> Any:
            try:
                value = self._loader_fn(key)
            except Exception as exc:
                with self._lock:
                    self._inflight.pop(key, None)
                result_future.set_exception(exc)
                raise
            else:
                with self._lock:
                    self._cache[key] = value
                    self._inflight.pop(key, None)
                result_future.set_result(value)
                return value

        self._scheduler.submit(_work, priority=priority, label=f"load:{key}")
        return result_future

    def is_cached(self, key: str) -> bool:
        with self._lock:
            return key in self._cache

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)
