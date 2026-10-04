# Repeat-offender policy and driver records

| Offence (current cycle, last 365 days) | Fine (on time / overdue) | Action |
|---|---|---|
| 1st | Rs 500 / Rs 1000 | warning |
| 2nd | Rs 1000 / Rs 2000 | |
| 3rd | Rs 3000 / Rs 5000 | driving licence suspended 3 months |
| each further one | Rs 3000 / Rs 5000 | suspension extended (+1 month per offence) |

- Applied automatically to every new e-challan (detected or manual) within seconds of issue
  (`backend/enforcement.py`, called by the dashboard and the SMS step). An amount typed by an
  officer is replaced by the schedule and noted on the challan.
- Strikes are counted per licence holder (all their registered vehicles); for unregistered
  vehicles, per plate. Waived and disputed challans do not count.
- Suspensions (`LICENCE_ACTION`) start on the day of the 3rd offence, end automatically, can be
  lifted early by an officer with a reason. An offence while suspended is flagged on the challan.
- After a suspension ends (served or lifted) the strike count restarts from zero. Nothing is
  deleted: every challan, helmet/triple/seat-belt record and suspension stays on the driver record.
- Officers can pull the full record any time: Notice Desk → "Licence actions" → record lookup, and
  Road Safety → Accidents → "Vehicles involved — driver records" (plates of the tracks in the incident).
- The e-challan SMS states the offence number and, if applicable, the suspension end date.
- Settings: `DSX_STRIKE_WINDOW_DAYS` (default 365).
- Existing challans at first run are marked `preexisting` (amounts untouched) but count as history.

## Helmet, triple riding, seat belt
Detected records become real e-challans from Rule Violations → "Issue e-challan" (plate required).
They then get the same SMS, Razorpay link, automatic settlement and driver record as speed challans.

| Offence | Statutory penalty (applied from the 1st offence) |
|---|---|
| No helmet (MV Act s.194D) | Rs 1000 + licence disqualified 3 months |
| Triple riding (MV Act s.194C) | Rs 1000 + licence disqualified 3 months |
| No seat belt (MV Act s.194B) | Rs 1000 |

They count as strikes together with speed challans; when the repeat-offender schedule is higher
(e.g. 3rd offence Rs 3000), the higher fine / longer suspension applies.

## Implausible speeds
A speed challan whose recorded speed is above `DSX_MAX_PLAUSIBLE_KMH` (200) is held: marked
`disputed`, no fine and no licence action, and the note asks for officer review.

## Schema change
On first run, `VIOLATION_NOTICE.violation_id` becomes optional (rule challans have no speed record)
and two columns are added (`violation_kind`, `rule_violation_id`). The database is backed up first
(`overspeed_backup_before_rule_challans_<timestamp>.db`); no rows are changed.
