# Deploy Saans on Vercel (frontend + backend together)

## One-time setup
1. Put this whole folder in a GitHub repo (the `.gitignore` keeps `.env` out).
   `api/index.py` and `vercel.json` must be at the repo ROOT, next to `app.py`.
2. Vercel dashboard -> Add New -> Project -> import the repo.
   - Framework Preset: **Other**
   - Root Directory: the folder that contains `vercel.json`
   - Build / Output settings: leave empty
3. Settings -> Environment Variables (apply to Production, Preview and Development):
   - `GROQ_API_KEY` = your key from https://console.groq.com/keys
   - `GROQ_MODEL` = `llama-3.3-70b-versatile` (optional)
4. Deploy. Environment variables only apply to NEW deployments, so redeploy after adding them.

## Or with the CLI
    npm i -g vercel
    vercel            # first run: link the project
    vercel env add GROQ_API_KEY
    vercel --prod

## Check it works
- `https://YOUR-SITE.vercel.app/`            landing page
- `https://YOUR-SITE.vercel.app/app`         the assistant
- `https://YOUR-SITE.vercel.app/api/health`  must show `"groq": true`
  (if it shows false, the key is missing: add it and redeploy)

## If something still fails
- 404 on every page: `vercel.json` is not at the project root (check Root Directory).
- 404 only on `/app`: an old deployment is cached. Redeploy and hard-refresh.
- Plan says "offline plan": `GROQ_API_KEY` is not set for that environment.
- Function timeout: `maxDuration` is 60 s in `vercel.json`; use `GROQ_MODEL=llama-3.1-8b-instant` for faster replies.
- Logs: Vercel dashboard -> your project -> Logs.
