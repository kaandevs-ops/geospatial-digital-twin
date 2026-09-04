# syntax=docker/dockerfile:1
# ---- build stage -----------------------------------------------------------
FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml README.md CHANGELOG.md ./
COPY harita ./harita

RUN python -m pip install --upgrade pip build \
    && python -m build --wheel

# ---- runtime stage ----------------------------------------------------------
FROM python:3.12-slim AS runtime

# Çekirdek platform stdlib-only'dir; runtime imajı bilinçli olarak küçük tutulur.
RUN useradd --create-home --uid 10001 harita
WORKDIR /app

COPY --from=build /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
    && rm -rf /tmp/*.whl \
    && mkdir -p /home/harita/.harita \
    && chown -R harita:harita /home/harita/.harita

USER harita
ENV HARITA_REGISTRY_PATH=/home/harita/.harita/registry.hprojreg

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).status==200 else 1)" || exit 1

# app_shell tek-tenant sunucusu (Faz 18); host 0.0.0.0 yapılmadan konteyner
# dışından erişilemez.
ENTRYPOINT ["harita-app"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--registry", "/home/harita/.harita/registry.hprojreg"]
