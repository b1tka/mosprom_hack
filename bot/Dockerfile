FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY assets ./assets
# platform-api2.max.ru подписан российским УЦ Минцифры, которого нет в стандартных хранилищах
COPY certs/russian_trusted_root_ca.pem /app/certs/russian_trusted_root_ca.pem
ENV NODE_EXTRA_CA_CERTS=/app/certs/russian_trusted_root_ca.pem
CMD ["node", "src/index.js"]
