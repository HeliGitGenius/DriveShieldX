# Real SMS and vehicle (RC) lookup: runbook

## What happens end to end
```
camera → violation → officer issues e-challan (Notice Desk)
       → plate matched to a registered vehicle → owner's mobile
       → ONE SMS: "DriveShieldX e-Challan #123 for MH12AB1234: Over-speeding 78 km/h in 60 km/h zone
                   at <camera>. Fine Rs 1,000, due 2026-10-15. Pay/view: <Razorpay link>"
       → owner pays on the link → challan PAID → confirmation SMS
Vehicle registry: plate → local registry, else RC-verification API → model, insurance/PUC/fitness
                  validity, blacklist flags (owner name masked)
```
- One SMS per challan, recorded in `CHALLAN_SMS`. Challans that existed before the feature was switched on are
  marked `preexisting` and never texted.
- Speed challans and issued helmet / triple-riding / seat-belt fines are both covered.
- The pay link is a Razorpay Payment Link when Razorpay is configured, otherwise the owner portal URL.
- **Safety:** a real SMS goes only to numbers in `DSX_SMS_ALLOWLIST` (your team's phones). Everything else,
  including hospital numbers in "direct" dispatch mode and plate strings, stays in the outbox
  (`logs/alert_outbox/`). `DSX_SMS_ALLOW_ALL=1` removes this and is not for a student deployment.

## SMS: recommended, your Android phone via SMSGate (free, open source, no message cap)
1. Install "SMS Gateway for Android" (SMSGate) from sms-gate.app on an Android phone with an active SIM; allow SMS.
2. In the app turn on **Cloud server**, tap the status button to go online; it shows a **username** and **password**.
3. `.env`: `DSX_SMS_PROVIDER=smsgate`, `SMSGATE_USER=...`, `SMSGATE_PASSWORD=...`, `DSX_SMS_ALLOWLIST=...`
   (Local mode instead: turn on **Local server**, laptop and phone on the same Wi-Fi, and set
   `SMSGATE_URL=http://<ip shown in app>:8080/message` with the local username/password.)
Normal SIM SMS charges/pack apply; keep the allow-list to your team.

## SMS: alternative, textbee.dev (free tier 50/day, 300/month)
Twilio trial accounts can only send Twilio's predefined templates, and Indian bulk gateways (Fast2SMS, MSG91)
require DLT registration (company, sender ID, approved templates, about Rs 5,900/year). textbee turns an Android
phone into the gateway: the SMS is a normal message from that phone's SIM.
1. Install the textbee app on an Android phone with an active SIM (from textbee.dev); allow SMS permission.
2. Sign up at textbee.dev → Dashboard → Generate API key → scan the QR code with the app → gateway enabled.
3. `.env`: `DSX_SMS_PROVIDER=textbee`, `TEXTBEE_API_KEY=...`, `DSX_SMS_ALLOWLIST=...`
   (if the dashboard shows a device ID and sending fails, also set `TEXTBEE_DEVICE_ID=...`).
Free tier: 50 SMS/day, 300/month. Normal SIM SMS charges/pack apply. Keep the allow-list to your team.

## SMS: option A, Twilio (needs an upgraded account for custom text)
1. Sign up at twilio.com, verify your own mobile.
2. Console → Phone Numbers → Verified Caller IDs: add each teammate's number (a trial account can text only verified numbers).
3. Get a trial phone number. Copy Account SID and Auth Token from the console home.
4. `.env`:
   ```
   DSX_SMS_PROVIDER=twilio
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM=+1...            # the trial number
   DSX_SMS_ALLOWLIST=+91XXXXXXXXXX,+91YYYYYYYYYY,+91ZZZZZZZZZZ
   ```
Messages reach Indian numbers over Twilio's international route (no DLT registration for that route); trial
messages start with "Sent from your Twilio trial account".

## SMS: option B, Fast2SMS (Indian gateway; DLT registration now required)
1. Sign up at fast2sms.com, recharge (minimum shown on their site), Dev API → copy the API key.
2. `.env`: `DSX_SMS_PROVIDER=fast2sms`, `FAST2SMS_API_KEY=...`, `DSX_SMS_ALLOWLIST=...`
Uses the Quick SMS route (`route=q`), which does not need DLT registration.

## Check SMS
Notice Desk → "SMS · e-challan notifications" → Send a test SMS to an allow-listed number.
Then issue a challan for a team vehicle whose owner account has that phone: one SMS arrives with the pay link.

## RC lookup (VAHAN alternative)
There is no free public VAHAN API; access is through paid RC-verification providers (KYC aggregators).
Some offer a few free trial calls. The connector is provider-neutral; set it from the provider's docs:

| Setting | Example |
|---|---|
| `DSX_RC_API_URL` | provider endpoint (`{plate}` allowed for GET) |
| `DSX_RC_API_KEY` | your key |
| `DSX_RC_API_AUTH` | `bearer` or `header:<HeaderName>` |
| `DSX_RC_API_HEADERS` | extra headers as JSON, e.g. a second account-id key |
| `DSX_RC_API_METHOD` | `POST` (default) or `GET` |
| `DSX_RC_API_BODY_KEY` / `DSX_RC_API_BODY_TEMPLATE` | flat `{"id_number": plate}` or a nested template |
| `DSX_RC_API_ROOT` | dotted path to the record in the response |
| `DSX_RC_API_MAP` | JSON mapping our fields to the provider's field names |

Steps:
1. Put URL/key/auth in `.env`.
2. `python -m backend.registry <YOUR OWN PLATE> --fields` prints the provider's field **names** only (no values).
3. Set `DSX_RC_API_ROOT` and, if names differ from the defaults, `DSX_RC_API_MAP`, e.g.
   `{"owner_name":"owner_name","insurance_upto":"insurance_upto","pucc_upto":"pucc_upto","fitness_upto":"fit_up_to"}`.
4. `python -m backend.registry <YOUR OWN PLATE>` prints the masked record and flags
   (insurance_expired, puc_expired, fitness_expired, blacklisted). Same lookup: Road Safety → Vehicle registry.
Results are cached (`DSX_RC_CACHE_HOURS`, default 24) so repeated plates do not spend credits.

**Only look up your own team's vehicles.** RC data is personal data under the DPDP Act 2023; owner names are
masked in the UI, and compliance flags are informational (no automatic challan from them).
