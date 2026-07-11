# Lender Portal Web

Next.js 15 App Router frontend for the Langfuse lender portal.

## Required runtime configuration

Copy `.env.example` to `.env.local` for local development. Production should set the same variables in the deployment environment.

| Variable | Purpose |
| --- | --- |
| `AUTH_SECRET` | Auth.js session secret. Also used as the API JWT signing fallback when `PORTAL_API_JWT_SECRET` is unset. |
| `AUTH_URL` | Canonical app URL, for example `https://portal.example.com`. |
| `AUTH_GOOGLE_ID` | Google OAuth client ID. |
| `AUTH_GOOGLE_SECRET` | Google OAuth client secret. |
| `GOOGLE_WORKSPACE_DOMAIN` | Required Workspace domain. Sign-in fails closed when unset or when Google does not return a verified email and matching `hd`. |
| `PORTAL_API_BASE_URL` | Base URL for the backend API that exposes `GET /use-cases`. |
| `PORTAL_API_JWT_SECRET` | Shared HS256 secret for web-to-API session JWTs. |
| `PORTAL_API_JWT_ISSUER` | JWT `iss`; defaults to `lender-portal-web`. |
| `PORTAL_API_JWT_AUDIENCE` | JWT `aud`; defaults to `lender-portal-api`. |

## Local commands

```bash
npm install
npm run dev
npm run typecheck
npm run build
```

The root page is protected. Authenticated users are sent to `/`, where the server component calls `GET /use-cases` with a short-lived bearer JWT derived from the Auth.js session.
