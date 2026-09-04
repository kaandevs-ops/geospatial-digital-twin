"""
UV Atlas & World-Scale Doku Eşleme
====================================

ROADMAP_V5 — M1.4 (son açık madde): "UV unwrapping kalitesi ... Doku
tekrarının (tiling) gerçekçi ölçekte olması (bir tuğla dokusunun bina
boyutuna göre otomatik ölçeklenmesi — dünya-birim başına doku tekrarı
sabit olmalı, bina büyüdükçe doku gerilmemeli) ... Mesh atlas / doku
atlas'ı: küçük dokuların tek büyük doku sayfasında birleştirilmesi."

Bu modül M1.4'ün iki alt maddesini kapatır:

1. **Dünya-ölçeği tutarlı UV** (`WorldScaleUVMapper`) — mevcut
   `mesh_engine.UVGenerator.box_mapping()` UV'leri **bounding-box'a**
   normalize ediyor ([0,1] aralığına sıkıştırıyor); bu, roadmap'in tam
   olarak şikayet ettiği hatanın kaynağı: büyük bir bina ile küçük bir
   bina aynı [0,1] UV aralığını kullanır, bu yüzden aynı dokuyu
   kullandıklarında büyük binada doku gerilir (daha az tekrar), küçük
   binada sıkışır (daha çok tekrar) — gerçekte istenen, dünya-birimi
   (metre) başına *sabit* doku tekrarıdır. `WorldScaleUVMapper`,
   normalize etmek yerine doğrudan dünya koordinatını `texture_size_m`'e
   bölerek UV üretir (`u = world_x / texture_size_m`), böylece 1x1 metre
   ve 100x100 metre bina, aynı dokuda **aynı fiziksel tekrar sıklığını**
   görür (GPU'nun doğal doku sarma/repeat davranışı 1'i aşan UV
   değerlerini otomatik tekrarlar).
2. **Mesh-seviyeli doku atlası** (`MeshUVAtlasBaker`) — `performance.
   streaming.TextureAtlas` zaten piksel-uzayında bir shelf-packing
   algoritması sağlıyordu (M1.2/Faz 13'te yazılmış) ama hiçbir yerde
   gerçek mesh UV'lerini bu paketlemeye göre **yeniden yazmıyordu** — bu
   yüzden "küçük dokuların tek sayfada birleştirilmesi" kabul kriteri
   fiilen karşılanmıyordu (yalnız *layout* hesaplanıyordu, *uygulama*
   yoktu). Bu sınıf, o boşluğu kapatır: birden fazla (mesh, doku
   boyutu) çiftini tek bir atlas'a paketler ve her mesh'in UV'lerini
   kendi atlas alt-dikdörtgenine yeniden eşler, sonra hepsini tek bir
   `Mesh3D`'de birleştirir — sonuç, tek doku sayfası + tek malzeme ile
   çizilebilen bir mesh (draw call azaltma hedefiyle doğrudan uyumlu,
   M1.2'nin devamı).

Tamamen stdlib-only; harici bir görüntü/piksel işleme kütüphanesi
gerektirmez (gerçek piksel blit'i `material_engine.texture_baking.
TextureMapCodec`'in sorumluluğunda kalır — bu modül yalnızca UV/layout
matematiğiyle ilgilenir).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import Mesh3D, MeshMerger, NormalGenerator, Vertex3D
from ..performance.streaming import AtlasEntry, TextureAtlas


class WorldScaleUVMapper:
    """M1.4: dünya-ölçeği tutarlı (bina boyutundan bağımsız sabit doku
    tekrarı) UV üretimi."""

    @staticmethod
    def box_mapping_world_scale(
        mesh: Mesh3D, texture_size_m: float = 1.0,
    ) -> Mesh3D:
        """`UVGenerator.box_mapping` ile aynı eksen-seçim mantığı (her
        üçgen için baskın normal eksenine göre en uygun düzlemsel
        izdüşüm), ancak UV'ler bounding-box'a normalize edilmez —
        doğrudan dünya koordinatı `texture_size_m`'e bölünür. Sonuç:
        `texture_size_m=1.0` iken dokunun her 1 metrede bir tekrar etmesi
        (tuğla/sıva/beton gibi malzemeler için tipik ölçek); binanın
        boyutu değiştikçe bu tekrar sıklığı **değişmez** (roadmap kabul
        kriteri: "farklı boyutlardaki binalarda doku tekrar sıklığı
        (cm/tekrar) sabit kalmalı").
        """
        if texture_size_m <= 0:
            raise ValueError("texture_size_m pozitif olmalı.")
        result = mesh.clone()
        if not result.vertices or result.vertices[0].normal is None:
            NormalGenerator.compute_face_averaged_normals(result)

        for tri in result.triangles:
            a, b, c = result.triangle_positions(tri)
            # Box mapping ile aynı baskın-eksen seçimi (yüz normaline göre).
            nx = a.normal[0] if a.normal else 0.0
            ny = a.normal[1] if a.normal else 0.0
            nz = a.normal[2] if a.normal else 0.0
            ax, ay, az = abs(nx), abs(ny), abs(nz)
            for v in (a, b, c):
                if az >= ax and az >= ay:
                    v.uv = (v.x / texture_size_m, v.y / texture_size_m)
                elif ay >= ax and ay >= az:
                    v.uv = (v.x / texture_size_m, v.z / texture_size_m)
                else:
                    v.uv = (v.y / texture_size_m, v.z / texture_size_m)
        return result

    @staticmethod
    def measure_texel_density(
        mesh: Mesh3D, texture_size_m: float,
    ) -> float:
        """Doğrulama yardımcı fonksiyonu: mesh'teki bir kenarın dünya-
        uzayı uzunluğu / UV-uzayı uzunluğu oranını (metre/UV-birimi)
        döndürür — `texture_size_m` ile birebir eşleşmesi beklenir (kabul
        kriterinin otomatik test edilebilir hali). Mesh boş/UV yoksa 0
        döner."""
        for tri in mesh.triangles:
            a, b, c = mesh.triangle_positions(tri)
            if a.uv is None or b.uv is None:
                continue
            world_len = a.distance_to(b)
            uv_len = ((a.uv[0] - b.uv[0]) ** 2 + (a.uv[1] - b.uv[1]) ** 2) ** 0.5
            if uv_len > 1e-9:
                return world_len / uv_len
        return 0.0


# ============================================================================ #
# Mesh-Seviyeli Doku Atlası
# ============================================================================ #

@dataclass(slots=True)
class AtlasBakeResult:
    merged_mesh: Mesh3D
    atlas: TextureAtlas
    entries: dict[str, AtlasEntry] = field(default_factory=dict)


class MeshUVAtlasBaker:
    """M1.2/M1.4: `performance.streaming.TextureAtlas`'ın hesapladığı
    piksel-uzayı yerleşimi, gerçek mesh UV'lerine uygular ve tüm
    parçaları tek bir mesh'te (tek malzeme/tek doku sayfası varsayımıyla
    çizilebilir hale) birleştirir."""

    @staticmethod
    def bake(
        parts: list[tuple[str, Mesh3D, int, int]],
        atlas_width: int = 2048,
        atlas_height: int = 2048,
        name: str = "atlas_baked_mesh",
    ) -> AtlasBakeResult:
        """`parts`: `(key, mesh, texture_width_px, texture_height_px)`
        dörtlülerinin listesi — her mesh'in kendi (varsayılan [0,1]
        aralığında, örn. `UVGenerator.box_mapping` çıktısı) UV'leri
        olmalı. Her mesh'in dokusu `atlas_width x atlas_height`'lik ortak
        bir sayfada `TextureAtlas` (shelf-packing) ile yerleştirilir,
        sonra UV'ler `(x + u*tex_w) / atlas_w, (y + v*tex_h) / atlas_h`
        formülüyle o alt-dikdörtgene yeniden eşlenir. Tüm parçalar tek
        mesh'te birleştirilir — sonuç, tek doku sayfası ile çizilebilen,
        draw-call'u azaltan bir mesh (M1.2'nin devamı)."""
        if not parts:
            raise ValueError("En az bir (key, mesh, w, h) parçası gerekir.")

        atlas = TextureAtlas(atlas_width=atlas_width, atlas_height=atlas_height)
        entries: dict[str, AtlasEntry] = {}
        remapped_meshes: list[Mesh3D] = []

        for key, mesh, tex_w, tex_h in parts:
            entry = atlas.add(key, tex_w, tex_h)
            entries[key] = entry
            remapped = MeshUVAtlasBaker._remap_uvs(mesh, entry, atlas_width, atlas_height)
            remapped_meshes.append(remapped)

        merged = MeshMerger.merge(remapped_meshes, name=name)
        return AtlasBakeResult(merged_mesh=merged, atlas=atlas, entries=entries)

    @staticmethod
    def bake_scene(
        parts: list[tuple[str, Mesh3D, int, int]],
        atlas_width: int = 4096,
        atlas_height: int = 4096,
    ) -> tuple[dict[str, Mesh3D], TextureAtlas]:
        """ROADMAP_V8 Faz 6.4 (şehir-ölçeği kalan madde): `bake()` ile
        aynı shelf-packing/UV-yeniden-eşleme mantığını kullanır, ama
        sonucu **tek bir mesh'te birleştirmez** — her `key` (ör. bina
        id'si) kendi remap edilmiş mesh'ini korur.

        Neden ayrı bir metod (neden `bake()` yeniden kullanılamadı):
        `bake()` bilinçli olarak *bina-içi* kullanım için tasarlandı —
        binanın kendi parçalarının (cephe/çatı/bodrum/çift-kabuk) tek bir
        çizilebilir mesh'e birleşmesi istenir, çünkü hepsi zaten aynı
        sahne nesnesi (aynı `Building`, aynı transform). Şehir ölçeğinde
        ise her bina sahnede **ayrı bir yerleştirilebilir nesne**
        (kendi konumu/rotasyonu, `app_shell`'in sahne grafiğinde ayrı bir
        node) — bunları tek bir mega-mesh'te birleştirmek, binaların tek
        tek seçilmesini/taşınmasını/LOD'lanmasını kıracaktı (mevcut sahne
        grafiği mimarisini bozardı). Bu metod, binaları **ayrı nesneler
        olarak bırakırken** yalnızca dokularını tek bir paylaşılan atlas
        sayfasında birleştirir — draw-call azaltma faydasının bir kısmı
        (aynı atlas = aynı malzeme = tek shader/texture-bind, GPU
        batching için hâlâ elverişli) korunur, sahne grafiği bozulmaz.

        Döner: `(key -> remap edilmiş Mesh3D, TextureAtlas)`.
        """
        if not parts:
            raise ValueError("En az bir (key, mesh, w, h) parçası gerekir.")

        atlas = TextureAtlas(atlas_width=atlas_width, atlas_height=atlas_height)
        remapped: dict[str, Mesh3D] = {}
        for key, mesh, tex_w, tex_h in parts:
            entry = atlas.add(key, tex_w, tex_h)
            remapped[key] = MeshUVAtlasBaker._remap_uvs(mesh, entry, atlas_width, atlas_height)
        return remapped, atlas

    @staticmethod
    def _remap_uvs(
        mesh: Mesh3D, entry: AtlasEntry, atlas_width: int, atlas_height: int,
    ) -> Mesh3D:
        result = mesh.clone()
        u0 = entry.x / atlas_width
        v0 = entry.y / atlas_height
        u_scale = entry.width / atlas_width
        v_scale = entry.height / atlas_height
        for v in result.vertices:
            if v.uv is None:
                # UV'siz vertex (örn. UVGenerator hiç çalıştırılmamış) -
                # atlas alt-bölgesinin sol-alt köşesine düşer (dejenere
                # ama en azından atlas sınırları içinde kalır, çökme yok).
                local_u, local_w = 0.0, 0.0
            else:
                # Girdi UV'si [0,1] dışına taşmış olabilir (world-scale
                # mapping'den geliyorsa) - atlas alt-bölgesi içinde kalması
                # için 0-1 aralığına sarılır (frac).
                local_u = v.uv[0] % 1.0
                local_w = v.uv[1] % 1.0
            v.uv = (u0 + local_u * u_scale, v0 + local_w * v_scale)
        return result
