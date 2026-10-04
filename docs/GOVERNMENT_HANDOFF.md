# Handing DriveShieldX to a traffic authority: what a real deployment needs

DriveShieldX is a working prototype of the full enforcement chain. A government deployment replaces
the student-grade connectors with official ones and adds the compliance work below.

## Official integrations (replace the demo connectors)
| Prototype | Production |
|---|---|
| Local registry + optional paid RC API | Vahan (vehicle) and Sarathi (licence) via NIC, for authorised government systems |
| DriveShieldX challan tables | Parivahan e-Challan system (push challans, pull status) |
| Hotlist "NOT TO BE TRANSACTED" flag | the Vahan flag of the same name |
| Razorpay (test / Rs 1 live demo) | the state's government payment gateway / treasury account |
| Android SMS gateway | DLT-registered sender ID and templates through a licensed SMS provider |
| Court referral CSV | virtual court integration |

## Legal and compliance
- Evidence: each challan's frame, plate crop and speed log must be produced with a certificate for
  electronic records (Bharatiya Sakshya Adhiniyam 2023, s.63); cameras need calibration certificates.
- Personal data: Digital Personal Data Protection Act 2023 (purpose limitation, retention limits,
  access control, breach reporting). DriveShieldX already masks owner names and keeps an audit trail.
- Security: CERT-In directions (incident reporting, log retention), a security audit before go-live,
  hosting on government cloud.
- Human in the loop: the disagreement gate, the officer review queue, contest decisions and
  licence-suspension lifts are all officer actions with recorded reasons.

## Before go-live
1. Pilot on a few junction cameras with officers reviewing every challan for a month.
2. Measure accuracy on local, labelled footage (helmet, plates, speed against a calibrated radar).
3. Publish the fine schedule, contest procedure and data-retention policy.
