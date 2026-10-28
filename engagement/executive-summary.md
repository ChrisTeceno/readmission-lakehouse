# Executive summary: Contoso Health readmissions

Date: 2026-10-21. Contoso Health is a fictional client: its 12 facilities are real public Ohio
hospitals under invented names, its operational data are synthetic, and no patient data is used.
Every number here comes from the readout (`engagement/readout.md`), which lists its sources.

## What we asked

Medicare cuts payments to hospitals whose readmissions run above what it expects for their
patients, condition by condition. Contoso wanted to know which facilities and conditions drive
its penalty, what would reduce it, and whether being penalized makes hospitals improve.

## What we found

- **The penalty is concentrated.** In the latest year, 10 of 12 facilities had at least one
  condition over the line (about 9 in 10 hospitals nationally did). In the year before, four
  facilities, Birch, Dogwood, Hawthorn and Oak, accounted for over half of Contoso's conditions
  over the line, and hip and knee replacement was over the line at 8 of the 9 facilities that do
  it. Overall, Contoso looks like a typical Ohio hospital network.
- **The penalty costs Contoso about $2.0M a year in FY2026 and $1.8M in FY2027 (estimates).**
  Medicare names each penalty after the year it is paid, not the years it measures: the FY2027
  bill, paid from October 2026 to September 2027, is charged on discharges from July 2023 to June
  2025 and is already fixed. Between the two years, heart attack care improved at Dogwood and hip
  and knee at Birch, while bypass surgery got worse at Alder and Hawthorn. FY2028 is mostly
  decided too; what Contoso does now first shows up around FY2029 to FY2031.
- **Next year will look much like this year.** Our forecast expects 28 facility-condition pairs
  over the line next year, against 25 this year, led by Oak (hip and knee, heart failure) and
  Hawthorn (bypass surgery). The forecast ranks well where Medicare's rule was approximated
  (correct order about 87 times in 100) but less well on the one recent year with Medicare's
  exact rule (about 68 times in 100), so treat it as a list of where to look first, not a
  promise.
- **Being penalized does not by itself make hospitals improve.** Comparing hospitals just over
  the line with near-identical ones just under it, their readmissions moved the same way
  afterwards. The data could have spotted an effect a quarter of the size that would matter, so
  this is a clear "no", for hospitals near the line. Hospitals far over the line were not
  studied. One consequence: a facility whose numbers improve the year after a penalty may just
  be returning to normal, so programs need a comparison group to show they work.
- **Clinical notes can add risk factors Medicare data lacks.** An AI model run inside Contoso's
  data platform pulled comorbidities, prior admissions and follow-up plans from notes, scoring
  0.81 on a 0 to 1 accuracy scale against 50 test notes. Our project lead drafted the 50 test
  answers and checked them against the written rules; Chris Teceno, a clinician, reviewed all 50
  and decided the four judgment calls.

## Three recommendations

1. **Focus review on the four facilities and on hip and knee replacement.** Start each year's
   quality review with the forecast's top pairs; the dashboard answers the five questions your
   team agreed on.
2. **Test nurse follow-up calls before rolling them out.** Switch a structured call within 72
   hours of discharge on two facilities at a time, every three months, over 21 months and about
   29,000 discharges. It will detect a drop in readmissions from about 17 to 13.5 percent; smaller
   gains may be missed. The decision for each outcome is agreed before it starts. The best
   trials find no effect from a call alone, but the program, at about $525K a year, pays for
   itself in avoided penalties if it lowers readmissions by about half a point to a point, and
   at a 1-point drop it about breaks even, a gain too small for the pilot to confirm. Medicare's own savings would be larger, but they are
   Medicare's, not Contoso's.
3. **Close two gaps.** Share Medicare's peer-group cutoffs for past
   years if Contoso has them, and approve the short-lived Azure resources (Key Vault and Data
   Factory) for the last pipeline run.

## Limits

Contoso's operations data are synthetic. Penalty dollars are estimates built from Medicare's
own modelling file rather than Contoso's paid claims, and the program's costs rest on stated
assumptions. The extraction labels were drafted by the project lead and reviewed by Chris
Teceno, a clinician.
