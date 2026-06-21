# Lore Verification Tracker
> Verify unverifiable claims against web sources
> 
> Created: 2026-06-21
> Status: in progress

---

## What's verified from trusted data

These are checked programmatically against worldcup.json upstream:
- Squad: player names, shirt numbers, positions, clubs, DOBs
- Groups: team assignments, fixture schedule
- Results: match scores, goalscorers, minutes
- Coaches: names and roles (from FIFA API raw match data)

## What needs web verification

Three categories per team:
1. **Tournament history** — number of appearances, years, best result
2. **Coach background** — nationality, career history, appointment date
3. **Player narratives** — career stories, transfer history, records

## Verification status

Legend: `[ ]` = unverified, `[x]` = verified (corrected if needed)

### Group A

- [x] **MEX** — 18th appearance correct; "tied with Germany" removed (Germany has more); QF 1970+1986 correct; Aguirre 3rd spell confirmed; Jimenez skull fracture Nov 2020 confirmed; Ochoa 6th WC confirmed; Mora born 2008-10-14 youngest-ever Mexico WC player
- [x] **RSA** — 4th appearance (1998,2002,2010,2026) confirmed; Broos AFCON 2017 Cameroon confirmed; Williams first South African CAF GK of Year 2024; Mofokeng born 23 Oct 2004 confirmed
- [x] **KOR** — 11th consecutive (1986-2026) confirmed, Asian record; 4th place 2002 correct; Hong 2002 captain + Bronze Ball; Son to LAFC Aug 2025 MLS-record $26.5M; Kim Min-jae Bayern €50M 2023
- [x] **CZE** — 2nd as Czech Republic (was "7th", corrected); Czechoslovakia 8 times + runners-up 1934+1962; Plasil 103 caps (was 105, corrected); Schick halfway line goal confirmed; Sochurek born 7 Jun 2008 youngest Czech debutant

### Group B

- [x] **CAN** — 3rd appearance (was "2nd", corrected — missed 2022); Marsch American, appointed May 2024; Davies at Bayern, ACL Mar 2025; David hat trick = first ever CAN WC win; first WC goal was Davies vs BEL 2022
- [x] **BIH** — 2nd appearance correct; Barbarez 47 caps (was 46), 65 HSV goals, zero coaching exp before appointment; Dzeko 73 goals 148 caps all-time scorer at Schalke age 40; qualified by beating Italy on pens
- [x] **QAT** — 2nd appearance (was "3rd", corrected — 1986 withdrawal doesn't count); worst-performing host 2022 (not "first" host eliminated — that was RSA 2010); Lopetegui appointed May 2025; Afif 2x Asian Footballer of Year
- [x] **SUI** — 13th appearance (was 12th, corrected); "Petkovic reached QF in 2022" was wrong (Yakin coached 2022, lost to POR 1-6 in R16; Petkovic's success was Euro 2020 beat FRA); Xhaka 146 caps record, now at Sunderland from Leverkusen Jul 2025

### Group C

- [x] **BRA** — 23rd appearance (was 22nd, corrected); every edition since 1930, only nation; 5 titles; Ancelotti appointed May 2025 first foreign BRA coach; Neymar 34 "last dance" at Santos; Cunha now at Man Utd £62.5M from Wolves
- [x] **MAR** — 7th appearance (was missing 2018, corrected to include it); 2022 semi-finalists 4th place confirmed; Ouahbi replaced Regragui 5 Mar 2026, won U-20 WC 2025; Saibari at PSV 15G 8A Eredivisie POTY
- [x] **HAI** — 2nd appearance (1974, 2026) correct; 52-year gap; Sanon broke Zoff's 1142-min record vs Italy; Migne never set foot in Haiti during qualifying — managed remotely (added to file)
- [x] **SCO** — 9th appearance correct; first since 1998 (28 years); Clarke appointed May 2019; Craig Gordon 43 oldest player at entire 2026 WC (added), £9m record GK transfer 2007, survived double leg-break

### Group D

- [x] **USA** — 12th appearance; "semi-finalists 1994" was wrong (R16), corrected; Pochettino appointed Sep 2024 (was "Nov 2023", corrected); Balogun at Monaco; Reyna at Monchengladbach
- [x] **PAR** — 9th appearance (was 10th, corrected); QF 2010 (was "4th place", corrected — no 3rd place match); Alfaro took Ecuador to 2022 group stage (was "QF", corrected)
- [x] **AUS** — 7th appearance (was 6th, corrected); 2022 was R16 (was "QF", corrected); Popovic appointed Sep 2024; Irankunda born 2006-02-09 refugee camp backstory; Jedinak 79 caps joined staff Jan 2026
- [x] **TUR** — 3rd actual tournament (was "5th", corrected — 1950 withdrew); Sukur 10.8 seconds (was "11"); Montella appointed Sep 2023; Guler born Feb 2005 at Real Madrid

### Group E

- [x] **GER** — 21st appearance (was 20th, corrected); 4x winners confirmed; Nagelsmann youngest at 38 confirmed; Neuer 5th WC (was "8th tournament", corrected) — 2nd German after Matthaus to reach 5
- [x] **CIV** — 4th appearance (2006,2010,2014,2026) confirmed; AFCON 2023 champs, Fae first mid-tournament replacement to win; Demel 35 caps (was 72, corrected), 199 Hamburg apps, 69 West Ham PL apps
- [x] **ECU** — 5th appearance correct; R16 2006 confirmed; Beccacece appointed 1 Aug 2024; Valencia all-time scorer 42 goals; Paez born 4 May 2007 youngest ECU international ever; Caicedo at Chelsea
- [x] **CUW** — 1st ever WC confirmed; population ~158k (was ~150k, corrected); Advocaat 78, oldest WC coach confirmed; resigned Feb 2026, returned 11 May 2026; qualified unbeaten 10 matches

### Group F

- [x] **NED** — 12th appearance (was 11th, corrected); 3 finals — most without a title; Koeman 2nd spell Jan 2023; van Nistelrooy 284 career goals (was 150, corrected); Gakpo 3 in 2022 confirmed; Hato born 7 Mar 2006
- [x] **JPN** — 8th consecutive appearance (1998-2026); R16 four times (2002, 2010, 2018, 2022); Moriyasu since 2018 longest-serving; Nagatomo 5th WC; Kubo 2nd WC
- [x] **SWE** — 13th appearance correct; runners-up 1958, 3rd 1950+1994; Potter appointed Oct 2025 (was 2024, corrected); Larsson 133 caps confirmed; Gyokeres 97 goals at Sporting, £64m Arsenal; Isak £125m to Liverpool
- [x] **TUN** — 7th appearance (1978-2026); Lamouchi sacked June 15; Renard appointed; first team to qualify conceding 0 goals; beat Panama 2018, France 2022

### Group G

- [x] **BEL** — 15th appearance correct; 3rd place 2018; Garcia appointed Jan 2025; De Bruyne joined Napoli June 2025 free transfer; Lukaku at Napoli confirmed
- [x] **EGY** — 4th appearance (1934,1990,2018,2026); Hassan 69 goals correct; Marmoush to Man City Jan 2025 €70m; Abdelkarim at Barca born 2008-01-01 confirmed
- [x] **IRN** — 7th appearance correct; Beiranvand saved Ronaldo pen 2018 confirmed; Ghalenoei 2nd spell (not 3rd); Taremi: Porto→Inter→Olympiacos confirmed; beat Wales 2022 (not drew England)
- [x] **NZL** — 3rd appearance (1982,2010,2026); drew Italy 1-1 in 2010 correct; Wood all-time top scorer 45 goals; Bazeley first coach at all 4 FIFA tournament levels

### Group H

- [x] **ESP** — 17th appearance correct; winners 2010; Euro 2024 champs; Yamal born 13 Jul 2007 confirmed youngest Euro finalist; Cubarsi born 22 Jan 2007; Rodri Ballon d'Or 28 Oct 2024; de la Fuente appointed Dec 2022
- [x] **CPV** — 1st ever WC; population ~530k (was 600k, corrected); Vozinha born 3 Jun 1986 confirmed oldest in debut WC match; Logan Costa at Villarreal; coach Bubista since 2020, CAF Coach of Year 2025
- [x] **KSA** — 7th appearance (was missing 2018, corrected); best R16 1994 (was omitted, added); beat Argentina 2-1 in 2022 confirmed; Donis appointed April 2026 (not "after 2022", corrected)
- [x] **URU** — 15th appearance (was 14th, corrected); 2x winners (1930,1950); Bielsa: Argentina 1998-2004, Chile 2007-2011 confirmed; Muslera 5th WC squad (was "6th", corrected), turned 40 on June 15

### Group I

- [x] **FRA** — 17th appearance (was 16th, corrected); 2 titles + 4 finals; Deschamps already one of three player+coach winners (was "if he wins", corrected); farewell tournament confirmed; Mbappe broke Giroud's all-time FRA record; Barcola at PSG, UCL winner 2025
- [x] **SEN** — 4th appearance (was 5th, corrected); QF 2002 beat France in group not QF; Thiaw age 45 (was 48, corrected), appointed Dec 2024, won AFCON 2025; Mane 53 goals all-time scorer at Al-Nassr; Koulibaly 35 captain Al-Hilal
- [x] **IRQ** — 2nd appearance (1986, 2026) correct; 40-year gap correct; Arnold appointed May 2025 after resigning AUS Sep 2024; took AUS to R16 (was "QF", corrected); Meulensteen left Man Utd when Moyes replaced Ferguson; Iqbal first player of Pakistani heritage at WC
- [x] **NOR** — 4th appearance correct; 1994 was group stage (was "R16", corrected); 1998 R16 correct, beat Brazil 2-1 in group (didn't "knock them out"); Haaland 55 NOR goals 349 career; Solbakken appointed Dec 2020; Hangeland 91 caps

### Group J

- [x] **ARG** — 19th appearance (was 18th, corrected); 3x winners, 6 finals total; ~17 of 2022 winners confirmed; Scaloni staff all 1997 U-20 teammates; Messi turns 39 on June 24 (was "39", corrected to 38 at tournament start); hat trick = 16 WC goals tying Klose
- [x] **ALG** — 5th appearance correct; R16 2014 lost to GER ET confirmed; Petkovic beat France in Euro 2020 R16 not QF (corrected); Luca Zidane confirmed — nationality switch approved Sep 2025; Ait-Nouri to Man City June 2025 £36.3m
- [x] **AUT** — 8th appearance correct; 3rd 1954 confirmed (beat SUI 7-5 in QF — highest-scoring WC match ever!); "Alaba 2-0 vs Messi" unverified, softened to club encounters; Schmid 28-year drought goal confirmed; Cordoba anniversary June 21 = day before ARG match (added!)
- [x] **JOR** — 1st ever WC confirmed; 2023 Asian Cup final lost to Qatar 3-1; Sellami Moroccan, played 1998 WC for Morocco, granted Jordanian citizenship by royal decree; Al-Taamari at Rennes Feb 2025

### Group K

- [x] **POR** — 9th appearance correct; best 3rd 1966 confirmed (Eusebio 9 goals); "Winners in 2006?" self-correction removed; Ronaldo 41, 6th WC record, 226 caps 143 goals; Martinez appointed Jan 2023; Neves to PSG Aug 2024 ~€70M, scored on WC debut
- [x] **COD** — 2nd appearance (was "4th", corrected); 1974 as Zaire 14-0 aggregate (was "17-0", corrected); 52-year gap longest of any 2026 qualifier; Wissa at Newcastle £55m from Brentford Sep 2025, scored first ever COD WC goal; qualified via playoff vs Jamaica Mar 2026
- [x] **UZB** — 1st ever WC confirmed; first Central Asian nation; Cannavaro appointed Oct 2025 with brother Paolo; Fayzullaev born Oct 2003 at Basaksehir, scored first UZB WC goal
- [x] **COL** — 7th appearance correct; best QF 2014 (was "1990", corrected — 1990 was R16); Lorenzo appointed June 2022; Copa Am 2024 lost to ARG 1-0 AET; Luis Diaz now at Bayern Munich; James at Minnesota United

### Group L

- [x] **ENG** — 17th appearance correct; winners 1966; Tuchel appointed Oct 2024 started Jan 2025; Kane 500 career goals Feb 2026 at Bayern; Bellingham 22 at Real Madrid; Rashford on loan at Barcelona from Man Utd
- [x] **CRO** — 7th as independent Croatia (was "8th", corrected); 2018 finalists lost to FRA 4-2; 3rd 1998 + 2022; Dalic since Oct 2017; Modric 40 at AC Milan; Baturina 23 at Como
- [x] **GHA** — 5th appearance (was 4th, corrected — missed 2022 inclusion); QF 2010 Suarez handball confirmed; Queiroz appointed Apr 2026 age 73 oldest WC match-winning manager; Paintsil 89 caps confirmed
- [x] **PAN** — 2nd appearance (2018, 2026) correct; Christiansen Danish-born, signed by Cruyff at Barca as teenager, Bundesliga top scorer 2002-03 with Bochum; appointed Jul 2020

---

## Process

For each team, run `/search` or `/qsearch` to verify the 3 unknowns.
Mark `[x]` when verified, `[!]` when error found and corrected.
Note corrections inline.

Priority: teams playing next get verified first.
