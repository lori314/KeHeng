# Data and source notes

## Synthetic material

- `data/examples/public_test_company_technology_profile.pdf` is authored for this project and describes a fictional company. It is intended for the no-key demonstration and must not be used as a real-company assessment.
- `data/evaluation_cases/` contains synthetic fixtures used by offline regression tests. These fixtures are not expert labels for real companies.

## Public-company source files kept local

The repository retains source notes and download links, but does not include the PDFs in its publication candidate. A download URL and public availability do not grant redistribution rights. Retrieve the original from the cited issuer/exchange/disclosure host, record retrieval date and SHA-256 locally, and do not modify the source file before reproducing an experiment.

| Company | Document / source | Local note |
| --- | --- | --- |
| Estun | 2024 annual report, CNINFO: <https://static.cninfo.com.cn/finalpage/2025-04-29/1223370517.PDF> | `data/real_cases/estun/README.md` and `manifest.json` |
| NIO | 2024 Form 20-F, HKEX: <https://www.hkexnews.hk/listedco/listconews/sehk/2025/0409/2025040900011.pdf> | `data/real_cases/nio/README.md` and `manifest.json` |
| SMIC, CATL, Cambricon, Siasun | See each local README/manifest for source link and collection metadata | Their original PDFs remain local and excluded from the candidate snapshot |

The source links and dates must be checked again at publication time. Corporate report content remains the respective publisher's material; the project does not claim ownership of it.

## Local-only records

`data/real_cases/human_review*.csv`, evidence-confirmation files, original PDFs, `runtime/` responses and request contexts are local review data. They are not required by the public synthetic demo and must not be included in a public release without separate authorization and privacy review. AI-authored review notes are not human gold labels.
