FROM node:20-alpine AS build
WORKDIR /app
# `npm ci` (not `npm install`) so the image installs exactly the tree CI tested
# — the lockfile has to be copied with package.json, before the install, or
# there is nothing for it to install from (issue #234).
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
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
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
