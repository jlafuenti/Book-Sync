FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing + API Proxy
RUN echo 'server { \
    listen 3000; \
    root /usr/share/nginx/html; \
    location / { \
    try_files $uri /index.html; \
    } \
    location /api/ { \
    proxy_pass http://server:8000/api/; \
    proxy_set_header Host $host; \
    proxy_set_header X-Real-IP $remote_addr; \
    } \
    }' > /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
