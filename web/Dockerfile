FROM node:20-alpine AS build
WORKDIR /app
# `npm ci` (not `npm install`) so the image installs exactly the tree CI tested
# — the lockfile has to be copied with package.json, before the install, or
# there is nothing for it to install from (issue #234).
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# nginxinc/nginx-unprivileged, not nginx:alpine (issue #180). The stock image's
# master process runs as root — it needs to, because its default config binds
# port 80 — and then forks workers as `nginx`. This variant runs the master as
# an ordinary uid too, keeps its pid file and every scratch path under /tmp, and
# listens above 1024, which is what lets the compose service carry
# `cap_drop: [ALL]`: with no CAP_NET_BIND_SERVICE a privileged port is
# unbindable, and the container has no capabilities left to bind one with.
#
# Port 3000 is unchanged — the compose `ports:` mapping, Caddyfile.example and
# docs/operations.md all still point at it. The base image's own default.conf
# (which listens on 8080) is overwritten below.
FROM nginxinc/nginx-unprivileged:alpine
# The COPY and the config write need root; USER drops back at the end.
USER root
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing + API proxy + PWA cache headers (issue #62, docs/web-pwa.md).
#
# The service worker, its precache manifest, index.html and the web manifest
# must never be served from a stale HTTP cache — the worker's update check is
# what rolls a new deploy out to installed apps. Hashed /assets/* are immutable
# by construction (Vite content-hashes the names), so cache them hard.
RUN echo 'server { \
    listen 3000; \
    root /usr/share/nginx/html; \
    # nginx add_header does not merge: any location block with an add_header of \
    # its own discards every inherited one. This server-level line covers the \
    # blocks that set no headers, and /index.html repeats it -- that is the one \
    # that matters, since the document is what sends a referrer (issue #284). \
    add_header Referrer-Policy "same-origin" always; \
    location = /sw.js { \
    add_header Cache-Control "no-cache, must-revalidate"; \
    try_files $uri =404; \
    } \
    location ~ ^/workbox-.*\.js$ { \
    add_header Cache-Control "no-cache, must-revalidate"; \
    try_files $uri =404; \
    } \
    location = /manifest.webmanifest { \
    add_header Cache-Control "no-cache, must-revalidate"; \
    types { } default_type application/manifest+json; \
    try_files $uri =404; \
    } \
    location = /index.html { \
    add_header Cache-Control "no-cache, must-revalidate"; \
    add_header Referrer-Policy "same-origin" always; \
    } \
    location /assets/ { \
    add_header Cache-Control "public, max-age=31536000, immutable"; \
    try_files $uri =404; \
    } \
    location / { \
    try_files $uri /index.html; \
    } \
    location /api/ { \
    proxy_pass http://server:8000/api/; \
    proxy_set_header Host $host; \
    proxy_set_header X-Real-IP $remote_addr; \
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
    proxy_set_header X-Forwarded-Proto $scheme; \
    } \
    }' > /etc/nginx/conf.d/default.conf

# The base image chowns its scratch directories to its own uid (101) with group
# 0. The compose service overrides the uid with `user: "${PUID:-1000}:${PGID:-1000}"`
# so it can match the rest of the stack, and that uid is in neither — nginx
# would then fail on the first proxied response big enough to need a temp file,
# which is a confusing intermittent 502 rather than a startup error. Widening
# the mode (rather than chowning to a fixed uid) is what makes the PUID knob
# work for any value. `a+rwX` adds execute only where it already exists.
#
# /usr/share/nginx/html and /etc/nginx are read-only at runtime, so they only
# need to stay world-*readable*, which they already are.
RUN chmod -R a+rwX /var/cache/nginx /tmp

EXPOSE 3000

# uid 101 is the base image's `nginx` user and the default it would use anyway;
# naming it explicitly is what tests/test_dockerfile_contract.py pins, and what
# protects a plain `docker run` of this image without the compose `user:`.
USER 101

# Unchanged from the stock image, and deliberately so: the pid path is a
# main-context directive that the base image already points at /tmp, and adding
# `pid` to `-g` here would collide with it ("directive is duplicate") and stop
# nginx from starting at all.
CMD ["nginx", "-g", "daemon off;"]
