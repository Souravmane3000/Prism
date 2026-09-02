# Prism frontend

Next.js App Router UI for Prism. Setup, env vars, and architecture live in the **[root README](../README.md)**.

```bash
cd frontend
npm install
npm run dev
```

Create `frontend/.env.local` from [`.env.local.example`](.env.local.example):

- `NEXT_PUBLIC_API_BASE_URL` — FastAPI origin (local `http://localhost:8000` or the Modal web URL)
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Production: [https://prism-beta-one.vercel.app](https://prism-beta-one.vercel.app). Colors only in `styles/tokens.css`. All HTTP goes through `lib/api.ts`.
