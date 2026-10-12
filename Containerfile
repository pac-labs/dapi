FROM --platform=$BUILDPLATFORM node:24-slim AS panel-build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY ui ./ui
RUN npm run build:ui

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LOOPRD_DATA=/var/lib/looprd LOOPRD_SECRETS=/etc/looprd/secrets \
    LOOPRD_SOURCES=/etc/looprd/sources.yaml
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY looprd ./looprd
COPY --from=panel-build /ui/looprd/panel_assets/panel.css ./looprd/panel_assets/panel.css
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home looprd \
    && mkdir -p /var/lib/looprd /etc/looprd/secrets \
    && chown -R looprd:looprd /var/lib/looprd
USER 10001:10001
EXPOSE 8080
VOLUME /var/lib/looprd
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_looprd/health', timeout=3)"
CMD ["python", "-m", "looprd"]
