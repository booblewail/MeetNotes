# Stage 1: Base dependencies & audio assets
FROM node:22-alpine AS base
WORKDIR /app
COPY package.json package-lock.json* ./
COPY scripts ./scripts
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Stage 2: Development (used by docker-compose target: dev)
FROM base AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# Stage 3: Production build
FROM base AS builder
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build

# Stage 4: Production runtime (standalone)
FROM node:22-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
