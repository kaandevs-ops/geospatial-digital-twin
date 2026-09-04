"""
Progressive Mesh (ROADMAP_V4 — Faz E2)
=======================================

Mevcut kısıt: `MeshSimplifier.simplify()` (Faz D1, QEM tabanlı) yalnızca
hedef üçgen sayısına ulaşana kadar art arda kenar-birleştirme (edge-collapse)
uygular ve *son* mesh'i döner — hangi kenarın hangi sırayla, hangi ara
konuma birleştiği (collapse history) atılır. Bu yüzden iki sabit LOD
seviyesi (örn. ratio=1.0 ve ratio=0.5) arasında viewer'da ani bir
"pop" oluşur.

Bu modül, Hoppe (1996, "Progressive Meshes") tarzı bir yaklaşımla D1'in
QEM edge-collapse sırasını **saklayan** (`ProgressiveMesh.build`) ve bu
sırayı tersine (vertex-split) okuyarak iki LOD seviyesi arasında sürekli
(geometrik) bir ara-kare (geomorph) üretebilen (`ProgressiveMesh.interpolate`)
bir yapı sağlar.

Tasarım notu: `mesh_engine.MeshSimplifier.simplify()`'ın genel API'si ve
davranışı **değiştirilmedi** (geriye uyumlu) — bu modül, aynı QEM adımlarını
(`_best_qem_collapse`, `_optimal_collapse_position`) yeniden kullanarak
üstüne "geçmiş kaydı" ekler. Vertex birleştirmede orijinal
`mesh_engine._collapse_edge_to` işlevinin aksine burada `weld_vertices`
uygulanmaz: bu, adımlar arasında vertex indekslerinin **kararlı** kalmasını
sağlar (yeniden indeksleme olmadan) ki `interpolate()` doğru vertex'i
konum-enterpolasyonu için bulabilsin. Birleştirilen `j` vertex'i mesh
dizisinde referanssız ("orphan") kalır; bu, çizim doğruluğunu etkilemez
(hiçbir üçgen ona referans vermez), yalnızca ihmal edilebilir bir bellek
maliyeti ekler.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import (
    Mesh3D,
    Vertex3D,
    _best_qem_collapse,
)

Point3 = tuple[float, float, float]


def _lerp3(a: Point3, b: Point3, t: float) -> Point3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _apply_collapse(mesh: Mesh3D, i: int, j: int, position: Point3) -> Mesh3D:
    """`mesh_engine._collapse_edge_to`'nun weld-etmeyen (indeks-kararlı)
    varyantı: yalnızca `i` vertex'ini birleşim noktasına taşır, `j`'ye
    referans veren üçgenleri `i`'ye yönlendirir, dejenere olanları eler.
    Vertex listesi **yeniden indekslenmez** (progressive mesh geçmişinin
    her adımda aynı indeks uzayında kalması gerekir)."""
    new_vertices = mesh.vertices[:]
    new_vertices[i] = Vertex3D(*position)

    new_triangles = []
    for (a, b, c) in mesh.triangles:
        tri = tuple(i if v == j else v for v in (a, b, c))
        if len(set(tri)) < 3:
            continue
        new_triangles.append(tri)

    return Mesh3D(vertices=new_vertices, triangles=new_triangles, name=mesh.name)  # type: ignore[arg-type]


@dataclass(slots=True, frozen=True)
class CollapseStep:
    """Tek bir QEM edge-collapse adımının tam geçmişi. `i`/`j` orijinal
    (kararlı) vertex indeksleridir; `old_i`/`old_j` bu adım uygulanmadan
    *hemen önceki* konumlarıdır (bir önceki adımların sonucu olabilir),
    `merged` ise QEM'in hesapladığı optimal birleşim noktasıdır."""

    i: int
    j: int
    old_i: Point3
    old_j: Point3
    merged: Point3
    triangles_before: int
    triangles_after: int


@dataclass(slots=True)
class ProgressiveMesh:
    """Bir mesh'in tam-detaydan (ratio=1.0) belirli bir minimum orana kadar
    olan tüm QEM edge-collapse geçmişini (`steps`) saklar. `steps[0]` en
    yüksek maliyetsiz/ilk birleşimdir; `steps[-1]` en basitleştirilmiş
    duruma ulaştıran son birleşimdir. `steps` sırasıyla uygulandığında
    `base` → en basitleştirilmiş mesh dönüşümü elde edilir; sıra tersine
    okunduğunda (vertex-split) basitleştirilmiş mesh'ten `base`'e geri
    dönülür."""

    base: Mesh3D
    steps: list[CollapseStep] = field(default_factory=list)

    @staticmethod
    def build(mesh: Mesh3D, min_triangle_ratio: float = 0.1) -> "ProgressiveMesh":
        """D1'in QEM sırasını (`_best_qem_collapse`) tekrar tekrar çağırıp
        `min_triangle_ratio`'ya karşılık gelen üçgen sayısına ulaşana kadar
        her adımı `CollapseStep` olarak kaydeder."""
        if not (0 < min_triangle_ratio <= 1.0):
            raise ValueError("min_triangle_ratio 0 ile 1 arasında olmalı.")

        base = mesh.clone()
        working = mesh.clone()
        target_count = max(1, int(len(working.triangles) * min_triangle_ratio))

        steps: list[CollapseStep] = []
        guard = 0
        while len(working.triangles) > target_count and guard < 50_000:
            guard += 1
            collapse = _best_qem_collapse(working)
            if collapse is None:
                break
            (i, j), position, _cost = collapse
            old_i = working.vertices[i].as_tuple()
            old_j = working.vertices[j].as_tuple()
            before = len(working.triangles)
            working = _apply_collapse(working, i, j, position)
            after = len(working.triangles)
            steps.append(
                CollapseStep(
                    i=i, j=j, old_i=old_i, old_j=old_j, merged=position,
                    triangles_before=before, triangles_after=after,
                )
            )
        return ProgressiveMesh(base=base, steps=steps)

    # -- Ayrık (discrete) LOD yeniden üretimi -------------------------------

    def mesh_at_step(self, step_count: int) -> Mesh3D:
        """`base`'den başlayarak ilk `step_count` collapse'ı sırayla
        uygulayan mesh'i döner. `step_count=0` → orijinal `base`;
        `step_count=len(self.steps)` → en basitleştirilmiş mesh."""
        if not (0 <= step_count <= len(self.steps)):
            raise ValueError(f"step_count [0, {len(self.steps)}] aralığında olmalı.")
        m = self.base.clone()
        for step in self.steps[:step_count]:
            m = _apply_collapse(m, step.i, step.j, step.merged)
        return m

    def mesh_at_triangle_count(self, target_triangle_count: int) -> Mesh3D:
        """Üçgen sayısı `target_triangle_count`'a en yakın (küçük-eşit)
        olan basitleştirme seviyesini bulup döner (ikili arama, `steps`
        boyunca üçgen sayısı monoton azalmayan bir dizidir)."""
        lo, hi = 0, len(self.steps)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            count = self.triangle_count_at_step(mid)
            if count <= target_triangle_count:
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return self.mesh_at_step(best)

    def triangle_count_at_step(self, step_count: int) -> int:
        if step_count == 0:
            return len(self.base.triangles)
        return self.steps[step_count - 1].triangles_after

    # -- Sürekli (geomorph) geçiş --------------------------------------------

    def interpolate(self, step_index: int, t: float) -> Mesh3D:
        """`step_index`'inci collapse'ın (0-tabanlı) uygulanmadan hemen
        önceki topolojisini (`mesh_at_step(step_index)`) temel alarak, o
        adımın birleştirdiği iki vertex'i (`i`, `j`) kendi eski
        konumlarından (`old_i`, `old_j`) ortak birleşim noktasına
        (`merged`) doğru `t` (0..1) oranında **lineer olarak** taşır.
        Üçgen bağlantısı (topoloji) bu sırada DEĞİŞMEZ — yalnızca `t=1.0`
        olduğunda (yani `mesh_at_step(step_index + 1)`'e geçildiğinde)
        dejenere üçgenler gerçekten elenir. Bu, klasik geomorph tekniğidir:
        kullanıcı `t` arttıkça vertex'lerin birbirine kaydığını görür,
        üçgen sayısı yalnızca adımın tam sonunda (ayrık) düşer — bu da
        kabul kriterindeki "ara-mesh üçgen sayısı iki uç seviye arasında
        monoton artar/azalır" şartını sağlar (ayrık sayı, hiçbir zaman
        `triangles_before`'ı aşmaz ya da `triangles_after`'ın altına
        inmez)."""
        if not (0 <= step_index < len(self.steps)):
            raise ValueError(f"step_index [0, {len(self.steps) - 1}] aralığında olmalı.")
        t = max(0.0, min(1.0, t))

        step = self.steps[step_index]
        base_mesh = self.mesh_at_step(step_index)
        vertices = base_mesh.vertices[:]
        vertices[step.i] = Vertex3D(*_lerp3(step.old_i, step.merged, t))
        vertices[step.j] = Vertex3D(*_lerp3(step.old_j, step.merged, t))
        return Mesh3D(vertices=vertices, triangles=base_mesh.triangles, name=base_mesh.name)

    def interpolate_between_counts(
        self, triangle_count_a: int, triangle_count_b: int, t: float,
    ) -> Mesh3D:
        """İki üçgen-sayısı hedefi (`triangle_count_a` > `triangle_count_b`,
        yani ince→kaba) arasında `t` (0..1) oranında ara-mesh üretir.
        `t=0` → `triangle_count_a` seviyesi, `t=1` → `triangle_count_b`
        seviyesi. Aradaki tüm ayrık collapse adımlarını `t`'ye orantılı
        sırayla uygular, yalnızca son (kısmi) adımı geomorph ile
        yumuşatır — böylece iki *herhangi* ayrık LOD arasında (yalnızca
        ardışık seviyeler değil) sürekli geçiş desteklenir."""
        if triangle_count_a < triangle_count_b:
            raise ValueError("triangle_count_a >= triangle_count_b olmalı (ince -> kaba).")
        t = max(0.0, min(1.0, t))

        # Bu iki hedefe karşılık gelen adım indekslerini bul.
        step_a = self._step_index_for_count(triangle_count_a)
        step_b = self._step_index_for_count(triangle_count_b)
        if step_a >= step_b:
            return self.mesh_at_step(step_a)

        span = step_b - step_a
        exact_progress = t * span
        whole_steps = int(exact_progress)
        partial_t = exact_progress - whole_steps
        current_step = step_a + whole_steps

        if whole_steps >= span:
            return self.mesh_at_step(step_b)
        if partial_t <= 1e-12:
            return self.mesh_at_step(current_step)
        return self.interpolate(current_step, partial_t)

    def _step_index_for_count(self, triangle_count: int) -> int:
        lo, hi = 0, len(self.steps)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            count = self.triangle_count_at_step(mid)
            if count <= triangle_count:
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return best
