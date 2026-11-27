# UNIK Post – one-time setup

Replace `<BASE_URL>` with the site address, e.g. `https://unik-post.onrender.com` (no trailing slash).
You do this once. Afterwards you only press **Connect** on the Channels page.

---

## 1. Google / YouTube (about 10 minutes)

1. Open <https://console.cloud.google.com/> and sign in with the Google account that manages the channels.
2. Top bar → project picker → **New project** → name `UNIK Post` → **Create**, then select it.
3. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
4. **Google Auth Platform** (or *APIs & Services → OAuth consent screen*) → **Get started**
   * App name `UNIK Post`, support email = your email → Next
   * Audience: **External** → Next
   * Contact email → Next → agree → **Create**
5. **Data Access → Add or remove scopes** → tick / paste:
   * `https://www.googleapis.com/auth/youtube.upload`
   * `https://www.googleapis.com/auth/youtube.readonly`
   → **Update** → **Save**.
6. **Audience → Test users → Add users** → add every Google account that owns or manages one of the channels
   (for Brand-account channels, add the Google account that manages the Brand account) → **Save**.
7. **Clients → Create client** → Application type **Web application** → name `UNIK Post`
   * Authorized redirect URIs → **Add URI** → `<BASE_URL>/oauth/google/callback`
   * **Create** → copy **Client ID** and **Client secret**.
8. On Render set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
9. **Important – stop weekly logouts:** while the app is in *Testing*, Google expires the connection every 7 days.
   Go to **Audience → Publishing status → Publish app → Confirm** (status becomes *In production*).
   No review is needed for your own use; at sign-in Google shows *"Google hasn't verified this app"* →
   **Advanced → Go to UNIK Post** → **Continue**. (Unverified apps are capped at 100 users – plenty.)
10. In UNIK Post → **Channels** → YouTube row → **Connect** → pick the Google account → **pick the matching channel**
    (Brand accounts appear in the chooser) → **Continue**. Repeat for each of the 4 channels.

**Limits (Google docs, Nov 2026):** uploads have their own quota bucket of **100 uploads per day per project** (all
channels together), resetting at midnight Pacific time (≈ 1:30 PM IST, 12:30 PM IST in US summer). This replaced
the old "about 6 uploads/day" limit. Uploads from unverified projects are **no longer locked to Private**.
More quota needs the YouTube API compliance audit form.

---

## 2. Meta – Facebook Pages + Instagram (about 15 minutes)

**Before you start (on the phone):**
* Each Instagram account must be **Professional (Business or Creator)**: Instagram → Settings → *Account type and tools* → *Switch to professional account*.
* For the Facebook route, each Instagram account must be **linked to a Facebook Page you manage**
  (Facebook Page → Settings → *Linked accounts* → Instagram → Connect). One Page links to one Instagram account.
  Today there are 2 Pages (venukalyan01, Soujanyaparentingcoach) for 5 Instagram accounts, so the other 3
  Instagram accounts either need their own Page **or** use the Instagram Login route in step 3 (no Page needed).

**Create the app**
1. Open <https://developers.facebook.com/apps> (log in with the Facebook account that is admin of the Pages)
   → **Create app**.
2. Pick the use cases **"Manage everything on your Page"** and **"Manage messaging & content on Instagram"**
   (or, on the older flow: *Other* → app type **Business**). Name `UNIK Post`, contact email → **Create app**.
3. **App settings → Basic**: copy **App ID** and **App secret**. Set *App domains* to your Render domain
   (e.g. `unik-post.onrender.com`), add a *Privacy policy URL* (any page you own) → **Save**.
4. Add the product **Facebook Login for Business** → **Settings**:
   * Client OAuth login **On**, Web OAuth login **On**, Enforce HTTPS **On**, Use Strict Mode **On**
   * **Valid OAuth Redirect URIs**: `<BASE_URL>/oauth/meta/callback` → **Save**.
5. (Recommended) **Facebook Login for Business → Configurations → Create configuration**:
   * Login variation **General**, token type **User access token**
   * Assets: **Pages** and **Instagram accounts**
   * Permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
     `instagram_basic`, `instagram_content_publish`, `business_management`
   * Create → copy the **Configuration ID**.
   If you skip this, the app asks for the same permissions directly.
6. On Render set `META_APP_ID`, `META_APP_SECRET` and (if made) `META_CONFIG_ID`.
7. **Development mode is fine.** Everyone who logs in must have a role on the app: **App roles → Roles** → add any
   other Facebook account that manages a Page as *Administrator* or *Developer*. App Review is only needed if
   people without a role should use it.
8. In UNIK Post → **Channels** → Facebook or Instagram → **Sync** (or **Connect**) → log in → tick **all** Pages and
   Instagram accounts → **Save**. Matching accounts connect automatically; others appear under
   *"Also found in your Facebook login"* with an **Add** button.

**Limits:** Instagram publishing ≈ 100 posts per account per 24 h. Instagram Reels via API: 3 s – 15 min.
Facebook Reels: 3 – 90 s (longer vertical videos go up as normal Page videos). Instagram photos must be JPEG
(the app converts automatically), aspect ratio 4:5 – 1.91:1.

---

## 3. Optional – Instagram Login (for Instagram accounts without a Facebook Page)

1. In the same Meta app → **Add product / use case → Instagram → "API setup with Instagram login"**.
2. Copy the **Instagram app ID** and **Instagram app secret** shown there.
3. **Set up Instagram business login → Business login settings → OAuth redirect URIs**:
   `<BASE_URL>/oauth/instagram/callback` → **Save**.
4. **App roles → Roles → Instagram testers** → add each Instagram account; accept the invite in Instagram
   (Settings → *Apps and websites* → *Tester invites*). Needed while the app is in Development mode.
5. On Render set `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`.
6. Channels → Instagram row → **Connect** now opens Instagram's own login; log in to that account → **Allow**.
   The app refreshes these tokens automatically (they last 60 days).

---

## 4. Server settings (Render)

| Var | Value |
| --- | --- |
| `APP_PASSWORD` | the site password |
| `DATABASE_URL` | Postgres URL. Free option: <https://neon.tech> → New project (region Singapore) → copy the connection string. Render's own free Postgres works too but is deleted after 30 days. |
| `SECRET_KEY`, `CRON_TOKEN` | generated by Render – don't change `SECRET_KEY` later (saved connections would need reconnecting) |
| Google / Meta / Instagram keys | from the steps above |

**Scheduler ping (for Instagram schedules):** create a free job at <https://cron-job.org> →
URL `<BASE_URL>/cron/run?token=<CRON_TOKEN>` → every 10 minutes (e.g. 06:00–23:59 IST to save free hours).
YouTube and Facebook schedules are held by YouTube/Facebook themselves and don't need it.
