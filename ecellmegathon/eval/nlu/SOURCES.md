# Sources

Every case in `cases.jsonl` carries a `source` code. Each case is a first-person spoken rewrite of a real,
publicly reported incident. Facts (what happened, cause, injury, equipment, actions) come from the source. The
wording, place names and times are ours. Outcomes that were fatal in the source were softened in some cases
(noted in the case's `notes`).

OSHA accident summaries are US federal government works (public domain). News reports are used for facts only;
no text is copied from them.

## Form design (the 17 fields)

| standard | fields it gave us | link |
|---|---|---|
| OSHA Form 301, Injury and Illness Incident Report | date (#11), time of event (#13), activity before the incident (#14 → `g`), what happened (#15 → `x`, built by code), injury and body part (#16 → `i`, `b`), object or substance that harmed (#17 → `c`), treated in emergency room (#18) / hospitalised (#19) → `m` | https://www.dir.ca.gov/dosh/DoshReg/ApndxC301.pdf |
| Factories Act 1948, Form 18 (Delhi Factories Rules, rule 96): notice of accident or dangerous occurrence | exact place (`l`), date and hour (`d`), cause or nature (`t`, `c`), machine and part causing it (`e`) | https://labour.delhi.gov.in/sites/default/files/2022-09/form18.pdf |
| Problem statement (Qualcomm PS1) | type, location, time, action taken (`a`), severity (`s`), mandatory-field check | PLAN.md §1 |

## Incident sources

| code | source | type | facts used | cases |
|---|---|---|---|---|
| S00 | Problem statement example | water leak | leak near Conference Room 2, ~10 AM, area isolated by maintenance | ps-01..03 |
| S01 | OSHA accident summary 201169307, *Employee electrocuted while installing electrical panels* — https://www.osha.gov/pls/imis/accidentsearch.accident_detail?id=201169307 | electrical | foreman installing panels in a meter room, 480 V pull panel energized with cover removed, loose live conductor, worked alone, found by coworkers, ~3 PM | el-01..03 |
| S02 | OSHA 960484, *Employee fractures elbow and wrist in fall* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=960484 | slip/trip | ~10:30 AM, preparing medical equipment in an emergency room, slipped on wet floor, hit right arm on metal cabinet, elbow and wrist fractured, hospitalised | sl-01..03 |
| S03 | OSHA 902387, nursing aide fractures ankle — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=902387 | slip/trip | ~8 PM, first-floor shower room, showering a resident, tripped over a metal strip on wet floor, ankle fracture | na-01..03 |
| S04 | OSHA 921361, *Employee killed under falling stowage* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=921361 | struck by | forklift in warehouse hit stored material, driver got off to straighten it, load fell on him, chest trauma | fk-01, fk-03 |
| S05 | OSHA 972182, *Employee injured when struck by overturned forklift* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=972182 | struck by | backing out of warehouse, hit overhead door, turned with forks raised, forklift tipped and pinned him, fracture, hospitalised | fk-02 |
| S06 | OSHA 889352, *Employee injured in fall from ladder* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=889352 | fall from height / water leak | 11:30 AM, going up an extension ladder to shut off a leaking water valve, ladder slipped in standing water, multiple arm fractures, hospitalised | lw-01..03 |
| S07 | OSHA 787184, *Employee injured in fall from ladder* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=787184 | fall from height | carpenter on a 30-ft extension ladder, base moved while top was tied, tie-off wires cut, top section came down, hospitalised | ld-01..02 |
| S08 | OSHA 879155, *Employee's eyes irritated when splashed by chemicals* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=879155 | chemical | tanker wash department manager checking vapour cleaning, pressurised internal valve before closing the external one, solvent splashed into eyes, no eye protection, medical attention | ch-01, ch-03 |
| S09 | OSHA 511717, *Employee's eye splashed by chemical* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=511717 | chemical | ~8:15 AM, mixing a sanitation product, splash into one eye, not serious | ch-02 |
| S10 | OSHA 955005, *Employee's fingertip amputated in chain and sprocket drive* — https://www.osha.gov/ords/imis/accidentsearch.accident_detail?id=955005 | caught in | removing tyres from a conveyor, reached over, finger caught in chain and sprocket, fingertip amputated, hospitalised | cv-01..03 |
| S11 | Siasat, *Fire breaks out at software company office in Jubilee Hills* (Hyderabad, June 2024) — https://www.siasat.com/hyderabad-fire-breaks-out-at-software-company-office-in-jubilee-hills-3050847/amp/ | fire | fire on the fourth floor of a software company's office, short circuit suspected, employees evacuated, firefighters put it out, no injuries | fi-01..03 |
| S12 | Free Press Journal, *Seven injured as lift plunges from second floor at Indore's Navlakha Complex* — https://www.freepressjournal.in/indore/seven-injured-as-lift-plunges-from-second-floor-at-indores-navlakha-complex | lift | ~3:15 PM, Block C, lift fell from second floor, 7 injured mostly legs (one with fractures in both legs), guard and police broke the door to rescue, old lift, overloading suspected | lf-01, lf-03 |
| S13 | Outlook, *9 IT employees injured in Noida lift crash* — https://outlookindia.com/national/9-it-employees-injured-in-noida-lift-crash-news-338223 | lift | 5:45 PM, lift fell from 8th floor as employees left work, 9 injured, 5 in ICU with arm and leg injuries, two maintenance workers held | lf-02, lf-04 |
| S14 | The News Minute, *Gas leak leads to fire in Hyd's Shah Ghouse restaurant* — https://www.thenewsminute.com/telangana/gas-leak-leads-fire-hyds-shah-ghouse-restaurant-1-critically-injured-91289 | fire / gas leak | LPG cylinder leak in kitchen caught fire ~12:30, one worker with severe burns, nine minor, fire out in ~20 minutes, injured taken to KIMS | gs-01..03 |
| S15 | The Week (PTI), Connaught Place restaurant fire, 13 March 2025 — https://www.theweek.in/wire-updates/national/2025/03/13/des26-dl-fire-ld-injury.html | fire | kitchen fire ~11:55 AM, suspected LPG leak, six staff with burns, staff and customers evacuated, six fire tenders | fi-04, rf-01 |
| S16 | Telangana Today, roof collapse at MGM Hospital, Warangal (Sept 2026) — https://telanganatoday.com/three-patients-two-staff-injured-as-roof-collapses-at-mgm-hospital-in-warangal | structural | roof concrete and false ceiling fell onto beds in a ward, 3 patients + 2 sanitation workers injured, shifted to emergency, ceiling fans damaged | cl-01, cl-03 |
| S17 | The News Minute, scaffolding collapse at Kukatpally construction site (Sept 2023) — https://thenewsminute.com/telangana/three-workers-killed-in-hyderabad-as-scaffolding-collapses-at-construction-site | structural / fall | parapet wall on top floor fell on workers on wooden scaffolding after heavy rain, 3 severely injured | sc-01..02 |
| S18 | Business Today, Chennai school gas leak (Oct 2024) — https://www.businesstoday.in/amp/india/story/chennai-30-students-hospitalized-due-to-gas-leak-in-school-3-critically-injured-451617-2024-10-25 | chemical | suspected leak from school laboratory, breathing difficulty, vomiting, fainting, ~30 students hospitalised, evacuated, fire department assisted | lb-01..02 |
| S19 | Free Press Journal, Bhopal police officer collapses, colleagues give CPR (Aug 2026) — https://www.freepressjournal.in/bhopal/bhopal-cop-collapses-at-police-station-colleagues-rush-to-save-him-with-cpr-video | medical | collapsed on night duty, two colleagues gave CPR and fanned him, taken to hospital, stable | md-01, md-03 |
| S20 | Deccan Herald, IT employee cardiac arrest in Nagpur office (Sept 2024) — https://www.deccanherald.com/india/maharashtra/it-company-employee-dies-of-cardiac-arrest-in-office-in-nagpur-3211718 | medical | ~7 PM, employee found unresponsive in the office washroom | md-02 |
| S21 | Tribune, roof seepage in Jalandhar government offices (Aug 2025) — https://www.tribuneindia.com/news/jalandhar/roof-seepage-dampness-hit-jalandhar-govt-offices-amid-rain/amp | water leak / structural | rain leaking into record rooms and computer rooms, buckets placed, floors wiped; POP false ceiling fell near staff chairs, no injuries | cl-02, wl-02 |
| S22 | Sakshi Post, University of Hyderabad laptop theft (Feb 2026) — https://www.sakshipost.com/news/hyderabad/university-hyderabad-laptop-theft-three-arrested-60-laptops-recovered-477481 | security | 60 laptops stolen from a computer lab at night, window broken open, CCTV cables cut, police complaint | sec-01..03 |
| H | Hand-written, no single source | various | gap fillers for asset names, equipment failure, an ambiguous type, an off-topic turn | wl-03, wl-04, eq-01, oth-01 |

Grounded: 52 of 56 cases.

## Not used, and why

- **OSHA Severe Injury Reports bulk CSV** (https://www.osha.gov/severeinjury): the best source for scaling up to
  hundreds of cases, but the zip couldn't be downloaded from the build machine. Download it manually for the M4
  expansion; `narrative`, `EventDate`, `NatureTitle`, `Part of Body Title` and `Hospitalized` map onto `x`, `d`,
  `i`, `b` and `m`.
- **SLURP / MASSIVE / BFCL**: methods only (SLU-F1, irrelevance and missing-parameter cases). Their data is not
  about incidents.
