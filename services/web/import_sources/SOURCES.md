# Official timetable and transfer source discovery

Checked 2026-11-17. These are factual downloads, not synthetic service patterns.

## Korail XLSX

Official index: https://www.korail.com/com/userBoard.do?mode=list&schBcid=ticketTable

`korail_manifest.json` preserves the exact official download URLs, posting dates and titles. Download base is `https://www.korail.com/file/cubedata/COMMON/` followed by the index's `fileId`.

| Local file ID | Contents | Effective from |
|---|---|---|
| 25745 | Gyeongui-Jungang, Gyeongui branch, Gyeongchun, direct Gwangundae branch, ITX-Cheongchun, Seohae weekday | 2026-09-01 |
| 25746 | Same families, holiday | 2026-09-01 |
| 25702 | Suin-Bundang weekday/holiday | 2026-08-22 |
| 23962 | Gyeonggang weekday/holiday | 2026-03-23 |
| 25743,25744 | Line 1 weekday/holiday | 2026-09-01 |
| 25701 | Ansan-Gwacheon / line 4 | 2026-08-22 |
| 21101 | Ilsan / line 3 | 2022-08-01, still listed but verify current validity |
| 23708 | Gyeongchun | 2026-02-28; newer 25745/25746 should take precedence |
| 26603 | Temporary Gyeonggi bus strike services | 2026-10-30 until strike ends; do NOT merge blindly |

`xlsx_cells.py` reads the workbooks without external packages. It does not calculate formulas or execute macros. Values are raw official cell values; numeric times are Excel day fractions. Train numbers are column headers; most workbooks have station arrival/departure row pairs. Empty/whitespace/pass cells are not stops. Preserve train IDs, endpoint distinctions, branch paths and service-day rollover. Some sheet templates contain irrelevant named station rows with no numeric times; do not create stops from those rows.

## Official transfer CSV

Page: https://www.data.go.kr/data/15044419/fileData.do

File: `transfer_20260831.csv`, CP949, 145 rows. Downloaded through the ordinary public page's file download flow:

1. POST `/tcs/dss/selectFileDataDownload.do` with `publicDataPk=15044419`, `publicDataDetailPk=uddi:7008c675-928f-41d6-9a01-b3541f78466b`, `fileDetailSn=1`, `publicDataTyCode=PR0051`.
2. Response returned `atchFileId=FILE_000000003619893`, `fileDetailSn=1`.
3. GET `https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003619893&fileDetailSn=1`.

Current page and data name say 2026-08-31; download metadata retained an older original filename ending 20251231. Preserve that provenance discrepancy. The actual `환승소요시간` cells are MM:SS, despite the column description saying seconds. These are shortest distance divided by 1.2m/s, not measured crowd-dependent transfer durations. Source rows begin with lines 1–8; reversing a row into GTX-A → line 4 is a declared inference, not a directly published direction-specific measurement.

## Metro9

Official notice: https://www.metro9.co.kr/bbs/BBSMSTR_000000000001/view.do?nttId=B000000008610Di1xB1

Weekday timetable changed on **2026-11-16**. Older 2025 CSVs are not automatically current.

Official station timetable: https://www.metro9.co.kr/prog/subwayTm/kor/sub01_02/list.do

Its normal HTML AJAX uses POST `https://www.metro9.co.kr/prog/subwayTm/kor/sub01_02/subwayTmAjax.do`, form parameters `outsdStnCd=4102&tmTabSe=DT&adptnStrtDt=20261116`. Station codes are 4101–4138. `metro9-DT.html` is the authentic Kimpo Airport response and includes weekday and holiday tables; `span.rapid` is express. It gives minute-resolution station departures and direction, **not reliable train IDs or complete short-turn trip identities**. Do not join the nth departures across stations and claim they are one train.

## GTX-A

Official current public timetable URL: https://www.gtx-a.com/trainT.do

Older official URL: https://www.gtx-a.com/ptl/info/timetable/viewTimeTable.do

Both returned the site's explicit policy block in this environment. No current official full timetable was downloaded, and no block bypass was attempted. Secondary sites list timetables but were not adopted as primary factual input. The KRIC normal/express timetable APIs are another official source but need their own approved service key and verified GTX coverage:

https://data.kric.go.kr/rips/M_01_02/intro.do

Endpoints: `https://openapi.kric.go.kr/openapi/trainUseInfo/subwayTimetable` and `.../subwayTimetableExp`, parameters include serviceKey, railOprIsttCd, lnCd, stinCd, dayCd (8 weekday,7 Saturday,9 holiday). Do not assume the existing data.go.kr key works for this service.
