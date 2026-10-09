# Cutline Studio frontend

React, TypeScript, Vite, and Tailwind CSS client for the FastAPI service in `../backend3`.

## Local setup

1. Install Node.js 22 or newer.
2. From this directory, install packages with `npm install`.
3. Copy `.env.example` to `.env.local` and set the API URL and upload limit for your environment.
4. Run `npm run dev` and open the local URL printed by Vite. This workspace is currently using `http://localhost:3001` because port 3000 already has a listener.

`VITE_API_BASE_URL` must include the API version prefix, for example `http://localhost:8000/api/v1`. `VITE_MAX_VIDEO_SIZE_MB` is a client-side guard and should match the backend's `MAX_VIDEO_SIZE_MB`. Vite exposes `VITE_` variables to the browser, so never put credentials, signing keys, or other secrets in them.

## API integration

- `POST /auth/login` uses form fields `username` and `password`; the access token is sent as a bearer token.
- `POST /upload` creates a job and returns a presigned S3 `PUT`; the browser uploads the MP4 directly to that URL using the returned headers.
- `GET /titles` and `GET /titles/{job_id}` provide title recommendations, render state, and signed download URLs.
- `POST /titles/{job_id}/clips` queues selected title IDs for rendering.

The backend currently has no jobs-list endpoint. The dashboard therefore combines uploads saved locally in the current browser with jobs discoverable from `GET /titles`. Existing jobs that have no titles and were not uploaded in this browser cannot be listed. The backend also reports both unrequested and queued-but-not-started titles as `PENDING`; the UI remembers requests locally to prevent repeat selection in the same browser, while `RENDERING` and `READY` states are disabled from the API response.

For local development, configure backend CORS to allow the exact frontend origin (`http://localhost:3001` in this workspace; normally `http://localhost:3000`). The S3 bucket CORS policy must also allow browser `PUT` requests from the frontend origin and the signed `Content-Type` header. A production deployment should serve the SPA over HTTPS and configure both CORS policies for its exact origin.

## Checks

- `npm run build` runs the TypeScript project build and creates the production bundle in `dist/`.
- `npm run lint` runs Oxlint.# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
