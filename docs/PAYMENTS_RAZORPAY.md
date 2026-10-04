# DriveShieldX online payments (Razorpay): runbook

## How a challan gets paid
```
Owner clicks "Pay"  ──► DriveShieldX creates a Razorpay Payment Link (amount, challan ref, owner phone/e-mail)
                        Razorpay hosts the checkout: UPI · cards · netbanking · wallets
Owner pays          ──► confirmation reaches DriveShieldX by up to three independent paths:
   1. Redirect   browser returns to DSX_PUBLIC_BASE_URL with a signed URL      (HMAC-SHA256, key secret)
   2. Webhook    Razorpay POSTs payment_link.paid to /razorpay/webhook         (HMAC-SHA256, webhook secret)
   3. Reconcile  "Check payment status" / owner page / "Sync all" ask Razorpay  (authenticated API call)
Settle (once)   ──► challan → PAID, method "Razorpay UPI/CARD/…", reference "DSX-<id>-<ts>/<pay_id>",
                    payment stored in PAYMENT_LINK, confirmation SMS + e-mail sent once
Refund          ──► Notice Desk → Online payments → Refund; challan returns to pending
```
Whichever path arrives first settles the challan; the others become no-ops (conditional update on
`PAYMENT_LINK.payment_id IS NULL`). A forged redirect URL or webhook fails the signature check and
changes nothing. A webhook whose paid amount is below the challan amount is not settled.

## Files
| File | Role |
|---|---|
| `backend/razorpay_gateway.py` | create link, verify signatures, settle, sync, refund |
| `database/payments_db.py` | `PAYMENT_LINK` table (created automatically) |
| `backend/api_server.py` | `POST /razorpay/webhook`, `GET /razorpay/status` |
| `dashboard/app.py` | owner checkout option, redirect handling, receipts |
| `dashboard/payments_admin.py` | authority panel under Notice Desk: all links, sync, refund |
| `backend/env_loader.py` | reads `.env` (shell variables win) |
| `tests/test_razorpay.py` | 12 tests with Razorpay mocked, on a copy of the database |

Without keys in `.env` nothing changes: the original UPI-QR and card demo checkout is shown.

## Settings (`.env`, never committed)
| Variable | Meaning |
|---|---|
| `DSX_PAYMENT_MODE` | `test` (default) or `live`; the key prefix must match (`rzp_test_` / `rzp_live_`) |
| `RAZORPAY_KEY_ID_TEST`, `RAZORPAY_KEY_SECRET_TEST` | test-mode API keys |
| `RAZORPAY_KEY_ID_LIVE`, `RAZORPAY_KEY_SECRET_LIVE` | live-mode API keys |
| `RAZORPAY_WEBHOOK_SECRET` | the secret typed when creating the webhook |
| `DSX_PUBLIC_BASE_URL` | where the browser returns after paying (default `http://localhost:8501`) |
| `DSX_LIVE_MAX_AMOUNT` | live-mode cap per challan in rupees (default 10) |

## Setup
1. Razorpay Dashboard → switch to **Test mode** → Account & Settings → API Keys → Generate. Copy Key ID and Key Secret (the secret is shown once).
2. Put them in `.env`:
   ```
   DSX_PAYMENT_MODE=test
   RAZORPAY_KEY_ID_TEST=rzp_test_...
   RAZORPAY_KEY_SECRET_TEST=...
   ```
3. Run `streamlit run dashboard\app.py`. Notice Desk → "Online payments · Razorpay" shows **TEST**.
4. Owner login → a pending challan → Pay → "Razorpay · UPI / Card / NetBanking" → Generate secure payment link → Pay now.
   Pay with test UPI ID `success@razorpay` (or a test card from Razorpay's "Test Card Details" docs page).
5. You return to the dashboard: green "Payment received" banner, challan shows PAID, payment history has the Razorpay payment id.
   Paid from a phone instead? Press "Check payment status" (or just reopen the owner page).

### Webhook (server-to-server confirmation)
1. Start the API: `uvicorn backend.api_server:app --port 8000`
2. Expose it over HTTPS: `ngrok http 8000` (a free ngrok account gives one fixed domain).
3. Razorpay Dashboard (Test mode) → Account & Settings → Webhooks → Add:
   URL `https://<your-ngrok-domain>/razorpay/webhook`, a secret of your choice, events `payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`.
4. `.env`: `RAZORPAY_WEBHOOK_SECRET=<that secret>`; restart uvicorn. `GET /razorpay/status` should show `webhook_secret_set: true`.
5. Pay a challan, close the browser tab before it redirects: the challan still turns PAID (webhook). Razorpay's webhook page shows the delivery with a 200.

### Live demonstration (₹1, your own money)
Live mode is for a ₹1 demo paid by a team member and refunded afterwards. A student deployment
must not collect real traffic fines from the public; only the traffic authority can.
1. Create a ₹1 challan for a team vehicle (Notice Desk), owned by a team member's account.
2. Razorpay Dashboard → Live mode → generate live keys and a live webhook (same URL, a new secret).
3. `.env`: `DSX_PAYMENT_MODE=live`, `RAZORPAY_KEY_ID_LIVE`, `RAZORPAY_KEY_SECRET_LIVE`, `RAZORPAY_WEBHOOK_SECRET` (the live one). Restart.
4. Pay ₹1 by UPI → challan PAID → Notice Desk → Online payments → tick confirm → Refund.
5. Set `DSX_PAYMENT_MODE=test` again.
Anything above `DSX_LIVE_MAX_AMOUNT` is refused in live mode.

## Checks
```powershell
python -m pytest tests\test_razorpay.py -q
```
Covers: link body (paise, INR, +91 contact, callback), link reuse, redirect + webhook + reconcile settle once,
forged redirect, bad webhook signature, short payment, reconcile without public URL, live cap, refund,
webhook endpoint, retry without callback URL.

## What an evaluator can verify
- Razorpay Dashboard → Payment Links: the link with reference `DSX-<challan>-<ts>`, status Paid.
- Razorpay Dashboard → Payments: the same payment id that DriveShieldX stores on the challan.
- Razorpay Dashboard → Webhooks: delivery log with HTTP 200.
- Notice Desk → Online payments: `settled_via` shows which path confirmed it (redirect / webhook / reconcile).
