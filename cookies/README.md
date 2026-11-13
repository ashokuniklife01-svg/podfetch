# Optional login cookies (stories only)

Instagram and Facebook **stories** are not public. This app can use a cookies file from an account that already has the right to view them. It will never try to bypass a login.

If `instagram.txt` or `facebook.txt` is missing, the Story screen shows a short message and does not attempt a download.

## How to add a cookies file

1. Log in to Instagram or Facebook in your own browser.
2. Export cookies for that site in **Netscape cookies.txt** format. A browser extension such as "Get cookies.txt LOCALLY" or "cookies.txt" can do this.
3. Save the file here:
   - Instagram: `cookies/instagram.txt`
   - Facebook: `cookies/facebook.txt`
4. Restart the app.

Do not share these files. They are as sensitive as a logged-in session. They are ignored by git and are not included in the project zip.

If a public reel, video, or post later starts asking for a login (some networks are blocked by the site), you can place the same cookies file here. That is still only for content you are allowed to view.
