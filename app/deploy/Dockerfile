# Northstar app: one image, two processes (web + worker) chosen by the command. Not built on the dev box (no Docker there).
FROM node:24-bookworm-slim AS deps
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --include=dev && npm rebuild @prisma/engines prisma @prisma/client esbuild @tailwindcss/oxide

FROM deps AS build
COPY . .
# Postgres in production: same models, provider switched
RUN node scripts/make-postgres-schema.mjs && npx prisma generate --schema prisma/postgres/schema.prisma \
 && NEXT_TELEMETRY_DISABLED=1 npx next build \
 && cp -r public .next/standalone/public && cp -r .next/static .next/standalone/.next/static

FROM node:24-bookworm-slim AS run
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/* && useradd -r -u 1001 northstar
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3100
COPY --from=build /app /app
USER northstar
EXPOSE 3100
# web (default). Worker: `node node_modules/tsx/dist/cli.mjs worker/index.ts`
CMD ["node", ".next/standalone/server.js"]
