# Deploying Stock Dashboard to Render (free, always-on URL)

This gets your dashboard a public URL your friends can open anytime,
without your computer needing to be on. Render's free tier sleeps after
15 minutes of no traffic — the first visit after sleeping takes up to ~1
minute to wake up, then it's normal speed.

## Important limitation: no persistent storage on the free tier

Render's free tier filesystem is **not persistent across restarts**.
Every time the app sleeps and wakes up, or you push a new deploy,
`watchlist.json` resets to empty. This means:
- Tickers your friends add will disappear after the app sleeps/restarts
- This is a Render free-tier limitation, not a bug in the app
- Fine for "let a friend poke around for a session" — not fine for
  "build a watchlist that persists for weeks"

If you need persistence later, the fix is swapping `watchlist.json` for
a real database (Render offers a free 90-day PostgreSQL trial) — not
needed for casual testing, but worth knowing the ceiling.

---

## Step 1 — Push your code to GitHub

In your `Stock-Dashboard` folder:

```
git init
git add .
git commit -m "Initial commit"
```

Create a new repo on https://github.com/new (e.g. `stock-dashboard`),
then:

```
git remote add origin https://github.com/YOUR_USERNAME/stock-dashboard.git
git branch -M main
git push -u origin main
```

## Step 2 — Create a Render account

Go to https://render.com and sign up (free, can use GitHub login directly).

## Step 3 — Create a new Web Service

1. From the Render dashboard, click **New +** → **Web Service**
2. Connect your GitHub account if prompted, then select your
   `stock-dashboard` repo
3. Fill in:
   - **Name**: anything, e.g. `stock-dashboard`
   - **Region**: closest to you (e.g. Singapore)
   - **Branch**: `main`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: Free
4. Click **Create Web Service**

Render will pull your repo, install dependencies, and start the app.
First build takes a few minutes — watch the logs on screen.

## Step 4 — Get your public URL

Once deployed, Render shows a URL like:
```
https://stock-dashboard-xxxx.onrender.com
```

This is what you send to your friends. Open it yourself first to wake
the app up before sharing, so they don't hit the ~1 minute cold start.

## Step 5 — Updating the app later

Any time you want to push a fix:
```
git add .
git commit -m "describe the change"
git push
```
Render auto-redeploys on every push to `main`.

---

## Files added for this deployment

- `Procfile` — tells Render to start the app with `gunicorn app:app`
  instead of Flask's built-in dev server (which isn't meant for real
  traffic)
- `requirements.txt` — now includes `gunicorn` alongside `flask` and
  `yfinance`

## Troubleshooting

**Build fails with a missing package error**
→ Check `requirements.txt` has every package your code imports

**App crashes on startup, logs show a port error**
→ Gunicorn binds to the port Render provides automatically — no code
  change needed, this is handled by the Procfile

**Friends see "Application Error" or a blank page**
→ Check the Render dashboard's **Logs** tab for the actual Python
  traceback — paste it back here and we'll debug it the same way we've
  been doing locally

**Data (watchlist) disappeared**
→ Expected on the free tier after a sleep/restart — see the limitation
  note above
