# Next.js standalone — small runtime image, server-rendered landing.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# next/font качает шрифты Google при сборке. Из Selectel часть соединений к Google
# устанавливается дольше 250 мс, и Node 20 обрывает их как ETIMEDOUT ещё до своих
# повторов (замер 04.11.2026). Без автовыбора адреса ждём обычный таймаут.
ENV NODE_OPTIONS=--no-network-family-autoselection
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
