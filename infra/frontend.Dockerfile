# Frontend image for local development (`next dev`). Build context: frontend/
# Production is deployed on Vercel and does not use this image.
FROM node:24-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]
