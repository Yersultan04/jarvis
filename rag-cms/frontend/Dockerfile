# --- build stage ---
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund --prefer-offline
COPY . .
RUN npm run build

# --- runtime: nginx serving static + proxy /api -> backend ---
FROM nginx:1.27-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/rag-cms.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
