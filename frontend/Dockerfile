# Static SPA served by unprivileged nginx, which also proxies /v1 to the API (single origin).

FROM node:24-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30-alpine AS runtime
USER root
RUN apk upgrade --no-cache
USER 101
COPY --chown=101:101 nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build --chown=101:101 /web/dist /usr/share/nginx/html
ENV API_UPSTREAM=http://api:8000 \
    NGINX_RESOLVER=127.0.0.11
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
