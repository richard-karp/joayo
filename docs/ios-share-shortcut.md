# Share an Instagram post to joayo (iOS Shortcut)

Extract a place from an Instagram reel or post **without opening the app**: tap
**Share** inside Instagram, pick **Extract to joayo**, and the extraction fires in
the background. You stay in Instagram; results show up on the dashboard when the job
finishes.

This works by having an Apple Shortcut call the backend directly. iOS Safari can't
register a web "share target," so a Shortcut is the native way to appear in
Instagram's share sheet and run silently.

## What it calls (the backend contract)

- **Endpoint:** `POST https://joayo-api.fly.dev/api/extract`
  (this is the Fly app `joayo-api`; it must match `NEXT_PUBLIC_BACKEND_URL`)
- **Header:** `X-Extract-Secret: <your access code>` — the same code you type into
  the web app's "Access code" field (stored there as `joayo_extract_code`). It must
  equal `EXTRACT_SECRET` set on the Fly backend.
- **Body (form):** `urls = <the shared Instagram URL>`
- **Response:** `{ "job_id": "…" }`, returned immediately. Extraction then runs in a
  background job.

Canonical `/p/…` and `/reel/…` links (including ones with a `?igsh=…` suffix) are
handled server-side — the tracking suffix is stripped automatically. No client-side
URL cleanup is needed.

## Build the Shortcut (once, ~2 minutes)

Open the **Shortcuts** app on the iPhone → **+** to create a new shortcut. Name it
**Extract to joayo**. Add these actions in order:

1. **Shortcut settings** (the ⓘ / settings toggle):
   - Enable **Show in Share Sheet**.
   - Set **Accepted Types** to **URLs**, **Safari web pages**, and **Text**.

2. **Get Contents of URL**
   - **URL:** `https://joayo-api.fly.dev/api/extract`
   - Expand **Show More**:
     - **Method:** `POST`
     - **Headers:** add one — key `X-Extract-Secret`, value = *your access code*
     - **Request Body:** `Form`
       - Add field: key `urls`, value = **Shortcut Input**
         (tap the value field → select the **Shortcut Input** variable)

3. **Get Dictionary Value**
   - **Get:** `Value` for **Key** `job_id`
   - **from:** *Contents of URL* (the previous action's output)

4. **If** — condition: `Dictionary Value` **has any value**
   - Inside the **If**: **Show Notification** → text `joayo ✓ extraction started`
   - **Otherwise**: **Show Notification** → text `joayo ✗ failed — ` followed by the
     **Contents of URL** variable (this surfaces the API error, e.g. a bad code or an
     unsupported URL)
   - **End If**

Save. The notification is your only feedback (nothing opens), so the success/error
branch matters — don't skip it.

## Use it

1. In Instagram, open a reel or post → tap **Share** (paper-plane / •••).
2. Choose **Extract to joayo** from the share sheet.
3. First time only, iOS asks to allow the Shortcut to contact `joayo-api.fly.dev` —
   tap **Allow**.
4. You'll get a `joayo ✓ extraction started` notification. Open the joayo dashboard
   later to see the extracted place.

## Verify it works (before relying on it)

Dry-run the API from a terminal first — this proves the backend path independent of
the Shortcut:

```sh
curl -i -X POST https://joayo-api.fly.dev/api/extract \
  -H "X-Extract-Secret: <your code>" \
  --data-urlencode "urls=https://www.instagram.com/p/Da6d9zIjwkt/"
```

- `200` + `{"job_id":"…"}` → good. Check progress with
  `curl https://joayo-api.fly.dev/api/jobs/<job_id>` (or the dashboard).
- `401` → the access code is wrong or missing.
- `422` → the URL wasn't recognized as a supported post/reel.

Then build the Shortcut and share a real reel to confirm the end-to-end flow and the
`✓` notification. To confirm the error path, put a wrong code in the Shortcut, share
once (expect `✗ failed …`), then restore the correct code.

## Troubleshooting

- **`✗ failed — …Invalid or missing extract access code`** → the `X-Extract-Secret`
  header value doesn't match `EXTRACT_SECRET` on Fly. Fix the value in the Shortcut's
  **Get Contents of URL** header.
- **`✗ failed — …No supported URLs found`** → the shared link wasn't a `/p/…` or
  `/reel/…` URL. If Instagram ever hands over a `https://www.instagram.com/share/…`
  link instead, it won't be recognized yet — capture the exact URL and we'll add
  handling for it in the backend (`backend/services/url_parser.py`).
- **No notification at all** → the Shortcut may not have run. Confirm **Show in Share
  Sheet** is on and **Accepted Types** includes URLs.
- **Rotated the secret?** If `EXTRACT_SECRET` changes on Fly, update the header value
  in the Shortcut (this doc is versioned; the Shortcut on your phone is not).
