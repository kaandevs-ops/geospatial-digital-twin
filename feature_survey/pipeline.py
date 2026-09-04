"""Dış fotogrametri araçlarının orkestrasyonu: WebODM (REST API) ve
Meshroom (CLI).

Tasarım kararı (bkz. modül `__init__.py` docstring'i): SfM/MVS burada
yeniden yazılmaz. Bu dosya sadece kurulu bir WebODM sunucusuna görev
gönderen / kurulu bir Meshroom binary'sini çağıran ince bir istemci
katmanıdır. Araç kurulu/erişilebilir değilse, projenin geri kalanındaki
`UnsupportedFormatError` deseniyle tutarlı biçimde **açıkça**
`ExternalToolNotAvailableError` fırlatılır — sessizce mock sonuç
üretilmez.

Bağımlılıklar opsiyoneldir (`pyproject.toml` `[survey]` extra'sı önerilir):
    - WebODM: sadece stdlib `urllib` ile REST API'ye konuşur (ekstra paket
      gerekmez), sunucu adresi kullanıcı tarafından verilir (self-hosted).
    - Meshroom: `subprocess` ile yerel kurulu `meshroom_batch` binary'sini
      çağırır.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


class ExternalToolNotAvailableError(RuntimeError):
    """WebODM sunucusuna ulaşılamadığında veya Meshroom binary'si sistemde
    kurulu olmadığında fırlatılır."""


@dataclass(slots=True)
class PhotogrammetryResult:
    """Bir fotogrametri koşusunun (WebODM veya Meshroom) standartlaştırılmış
    çıktı özeti — hangi motor kullanılırsa kullanılsın aynı şekle sahiptir,
    böylece `bridge.py` motor-agnostik kalır."""

    point_cloud_path: Path | None      # .las/.laz/.ply çıktısı (varsa)
    mesh_path: Path | None             # texture'lı mesh (.obj/.glb) (varsa)
    orthomosaic_path: Path | None      # WebODM'e özgü, opsiyonel
    engine: str                        # "webodm" | "meshroom"
    raw_metadata: dict = field(default_factory=dict)


class WebODMPipeline:
    """Self-hosted bir WebODM/NodeODM sunucusuna (Docker ile ayağa
    kaldırılan, roadmap'te önerilen) REST API üzerinden görev gönderir.

    Kurulum sorumluluğu kullanıcıdadır (``docker-compose`` ile WebODM ayrı
    bir servis olarak çalışır — bu platformun kendi `docker-compose.yml`'ine
    `webodm` servisi eklenerek entegre edilebilir, bkz. proje kökü
    `docker-compose.yml`).
    """

    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        content_type: str | None = None,
        raw: bool = False,
    ) -> dict | bytes:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"JWT {self.token}"} if self.token else {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_bytes = resp.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise ExternalToolNotAvailableError(
                f"WebODM sunucusuna ulaşılamadı ({self.base_url}): {exc}. "
                "Sunucunun çalıştığından ve base_url'in doğru olduğundan emin olun."
            ) from exc
        if raw:
            return raw_bytes
        return json.loads(raw_bytes.decode("utf-8"))

    def ping(self) -> bool:
        """Bağlantı testi (arayüzdeki 'test et' butonuyla eşleşir)."""
        try:
            self._request("/api/")
            return True
        except ExternalToolNotAvailableError:
            return False

    @staticmethod
    def _build_multipart(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
        """Stdlib-only multipart/form-data gövdesi üretir (ek paket yok —
        proje genelindeki stdlib-only ilkesiyle tutarlı)."""
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        for field_name, path in files:
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; '
                    f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'
                ).encode("utf-8")
            )
            parts.append(path.read_bytes())
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        return body, f"multipart/form-data; boundary={boundary}"

    def _get_or_create_project(self, name: str) -> int:
        projects = self._request("/api/projects/", method="GET")
        items = projects.get("results", projects) if isinstance(projects, dict) else projects
        for p in items:
            if p.get("name") == name:
                return p["id"]
        body, ctype = self._build_multipart({"name": name}, [])
        created = self._request("/api/projects/", method="POST", data=body, content_type=ctype)
        return created["id"]

    def submit_task(self, name: str, image_paths: list[str | Path]) -> str:
        """Yeni bir işleme görevi oluşturur: proje bulunur/oluşturulur,
        verilen fotoğraflar gerçekten (multipart/form-data) yüklenir, görev
        id'si döner."""
        if not image_paths:
            raise ValueError("submit_task: en az bir fotoğraf yolu gerekli.")
        project_id = self._get_or_create_project(name)
        files = [("images", Path(p)) for p in image_paths]
        for _, path in files:
            if not path.exists():
                raise FileNotFoundError(f"Fotoğraf bulunamadı: {path}")
        body, ctype = self._build_multipart({}, files)
        result = self._request(
            f"/api/projects/{project_id}/tasks/",
            method="POST",
            data=body,
            content_type=ctype,
        )
        return result["id"]

    def task_status(self, project_id: int, task_id: str) -> dict:
        """Görevin anlık durumunu döner (`status.code`: 10=queued,
        20=running, 40=completed, 30/50=failed/canceled — WebODM API
        sabitleri)."""
        return self._request(f"/api/projects/{project_id}/tasks/{task_id}/")

    def wait_for_completion(
        self,
        project_id: int,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        max_wait: float = 3600.0,
    ) -> dict:
        """Görev bitene kadar (tamamlanana/başarısız olana) periyodik olarak
        durumu sorgular."""
        elapsed = 0.0
        while elapsed < max_wait:
            status = self.task_status(project_id, task_id)
            code = status.get("status", {}).get("code")
            if code == 40:  # COMPLETED
                return status
            if code in (30, 50):  # FAILED / CANCELED
                raise RuntimeError(f"WebODM görevi başarısız oldu (kod {code}): {status}")
            time.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(f"WebODM görevi {max_wait}s içinde tamamlanmadı (task_id={task_id}).")

    def fetch_result(
        self, project_id: int, task_id: str, output_dir: str | Path
    ) -> PhotogrammetryResult:
        """Tamamlanmış görevin `all.zip` varlıklarını indirir, açar ve
        point cloud/mesh/ortomozaik dosyalarını otomatik bulur."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = self._request(
            f"/api/projects/{project_id}/tasks/{task_id}/download/all.zip", raw=True
        )
        zip_path = output_dir / "all.zip"
        zip_path.write_bytes(raw)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(output_dir)

        cloud_candidates = (
            list(output_dir.rglob("*.las"))
            + list(output_dir.rglob("*.laz"))
            + list(output_dir.rglob("*.ply"))
        )
        mesh_candidates = list(output_dir.rglob("*.obj")) + list(output_dir.rglob("*.glb"))
        ortho_candidates = list(output_dir.rglob("*orthophoto*.tif")) + list(
            output_dir.rglob("*orthomosaic*.tif")
        )
        return PhotogrammetryResult(
            point_cloud_path=cloud_candidates[0] if cloud_candidates else None,
            mesh_path=mesh_candidates[0] if mesh_candidates else None,
            orthomosaic_path=ortho_candidates[0] if ortho_candidates else None,
            engine="webodm",
            raw_metadata={"task_id": task_id, "project_id": project_id},
        )

    def run(
        self,
        name: str,
        image_paths: list[str | Path],
        output_dir: str | Path,
        *,
        poll_interval: float = 5.0,
        max_wait: float = 3600.0,
    ) -> PhotogrammetryResult:
        """`submit_task` + `wait_for_completion` + `fetch_result`'ı tek
        çağrıda birleştiren kolaylık metodu."""
        project_id = self._get_or_create_project(name)
        task_id = self.submit_task(name, image_paths)
        self.wait_for_completion(
            project_id, task_id, poll_interval=poll_interval, max_wait=max_wait
        )
        return self.fetch_result(project_id, task_id, output_dir)


class MeshroomPipeline:
    """Yerel kurulu Meshroom (AliceVision) CLI'sini (`meshroom_batch`)
    çağırır — GPU'lu makinede tek makine fotogrametrisi için WebODM'e göre
    daha hızlı/esnek alternatif."""

    def __init__(self, binary: str = "meshroom_batch"):
        self.binary = binary

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def run(
        self,
        image_dir: str | Path,
        output_dir: str | Path,
        *,
        timeout: float | None = None,
    ) -> PhotogrammetryResult:
        if not self.is_available():
            raise ExternalToolNotAvailableError(
                f"'{self.binary}' PATH üzerinde bulunamadı. Meshroom kurulumu "
                "gerekli: https://alicevision.org/#meshroom"
            )

        image_dir = Path(image_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [self.binary, "--input", str(image_dir), "--output", str(output_dir)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError as exc:
            raise ExternalToolNotAvailableError(str(exc)) from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"meshroom_batch başarısız (kod {proc.returncode}):\n{proc.stderr}"
            )

        mesh_candidates = list(output_dir.rglob("*.obj"))
        cloud_candidates = list(output_dir.rglob("*.ply")) + list(output_dir.rglob("*.las"))

        return PhotogrammetryResult(
            point_cloud_path=cloud_candidates[0] if cloud_candidates else None,
            mesh_path=mesh_candidates[0] if mesh_candidates else None,
            orthomosaic_path=None,
            engine="meshroom",
            raw_metadata={"stdout_tail": proc.stdout[-2000:]},
        )
