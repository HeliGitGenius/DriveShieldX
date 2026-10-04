# Unpaid e-challans: escalation (Indian procedure)

| When (from issue) | What DriveShieldX does automatically |
|---|---|
| day 0 | e-challan SMS with payment link |
| day 7, 13 | reminder SMS with a fresh payment link |
| due date (14) | overdue: fine rises to the higher amount, SMS |
| day 0–45 | owner can **contest** online (Owner dashboard → "Contest a challan"). Clock stops while under review; the challan does not count as a strike |
| decision | accepted → challan cancelled (and any licence suspension it caused is lifted); rejected → pay within 30 days or appeal in court |
| day 75 (45 + 30) | still unpaid → vehicle **NOT TO BE TRANSACTED** (hotlist): owner SMS, banner on owner dashboard, RC/licence services blocked; after a rejected contest: decision + 30 + 15 days |
| hotlisted | any DriveShieldX camera that reads the plate raises a **live alert** (officer pop-up, Notice Desk list, SMS to the control-room phone if allow-listed) |
| day 90 | still unpaid → listed for **virtual-court referral** (CSV export on Notice Desk) |
| 5 challans in a year | licence suspended (3 months), independent of the strike cycle |
| paid / cancelled | hotlist cleared automatically; court list drops it |

- No money is ever deducted from a bank account: that needs a law or the person's consent.
- Runs in the background with the dashboard / API server (every ~30 s) and from "Run escalation now".
- First run is silent: stages already due for existing challans are recorded without any SMS.
- Every stage happens once per challan (`ESCALATION_EVENT`).

Settings (`.env`): `DSX_CONTEST_DAYS=45`, `DSX_PAY_AFTER_CONTEST_DAYS=30`, `DSX_GRACE_AFTER_REJECTION_DAYS=15`,
`DSX_COURT_AFTER_DAYS=90`, `DSX_REMINDER_DAYS=7,13`, `DSX_ANNUAL_SUSPENSION_COUNT=5`,
`DSX_ANNUAL_SUSPENSION_MONTHS=3`, and for a live demo only `DSX_ESCALATION_DAY_SECONDS=60`
(one "day" per minute; the dashboard shows a DEMO TIME banner).
