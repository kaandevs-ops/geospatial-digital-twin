"""
Application Shell (Phase 18 — ROADMAP V2, Track B)
====================================================

Amaç: önceki fazların (render engine, persistence, editor, AI assistant)
ürettiği yetenekleri, kullanıcının **hiç kod yazmadan** kullanabileceği tek
bir uygulama kabuğu altında birleştirmek — proje gezgini, katman paneli,
düzenleyici araç çubuğu, AI asistan sohbet paneli ve gerçek zamanlı 3D
görüntüleyici.

Mimari (roadmap ilkesiyle uyumlu: stdlib-only backend):

    ``session.py``   — ``AppSession``: tek bir proje/oturum için durum
                        (açık proje, bina koleksiyonu, undo/redo yığını,
                        AI asistan orkestratörü) ve yüksek seviyeli
                        operasyonlar (proje aç/oluştur/kaydet, bina ekle,
                        sahne üret, doğal dil komutu çalıştır).
    ``api.py``       — ``build_app_router()``: Phase 14
                        ``extensibility.rest_api.RestRouter`` üzerine inşa
                        edilmiş, ``AppSession``'ı saran HTTP-çerçevesi
                        bağımsız route seti. Soket açmadan ``dispatch()``
                        ile test edilebilir.
    ``server.py``    — stdlib ``http.server`` tabanlı gerçek bir HTTP
                        sunucusu: ``web/`` altındaki statik uygulama
                        kabuğunu servis eder, ``/api/*`` isteklerini
                        ``RestRouter``'a yönlendirir.
    ``web/index.html`` — Tek dosyalık, framework-siz uygulama kabuğu arayüzü
                        (Faz 15 viewer'ının üzerine inşa edilmiş): proje
                        gezgini, katman/inspector paneli, editör araç
                        çubuğu, AI asistan sohbet paneli.

Kapsam ve bilinçli sınırlamalar `README.md` içinde belgelenmiştir.
"""

from .session import AppSession, AppSessionError
from .api import build_app_router

__all__ = ["AppSession", "AppSessionError", "build_app_router"]
