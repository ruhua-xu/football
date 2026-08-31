# Web GPT Offline Review Acceptance

Upload only `analysis_packet_v2.json` to the web GPT. Do not upload
`local_acceptance_report.md` or the SQLite database.

Save the returned formal review as `exchange/llm_review_v2.json`. Do not create
or substitute a fixture review.

## 1. Validate

```powershell
.venv\Scripts\football-system.exe llm-review validate `
  --packet "exchange/analysis_packet_v2.json" `
  --review "exchange/llm_review_v2.json"
```

Record the validated AnalysisPacket `packet_id` shown in the output. This step
does not create a new database ID.

## 2. Import

```powershell
.venv\Scripts\football-system.exe llm-review import `
  --database-url "sqlite:///data/manual_review_acceptance.db" `
  --packet "exchange/analysis_packet_v2.json" `
  --review "exchange/llm_review_v2.json"
```

Record the `review_artifact_id` printed as `LLMReviewArtifact imported`.

## 3. Create FusionRun

```powershell
.venv\Scripts\football-system.exe fusion-run create `
  --database-url "sqlite:///data/manual_review_acceptance.db" `
  --review-artifact-id "<review_artifact_id>"
```

Record the `fusion_run_id` printed as `FusionRun created`.

## 4. Create PortfolioRevision

```powershell
.venv\Scripts\football-system.exe portfolio-revision create `
  --database-url "sqlite:///data/manual_review_acceptance.db" `
  --fusion-run-id "<fusion_run_id>"
```

Record the `portfolio_revision_id` printed as `PortfolioRevision created`.

## 5. Generate The Local Comparison

```powershell
.venv\Scripts\python.exe "exchange/acceptance_report.py" compare `
  --database "data/manual_review_acceptance.db" `
  --packet "exchange/analysis_packet_v2.json" `
  --analysis-run-id "manual-review-acceptance-001" `
  --fusion-run-id "<fusion_run_id>" `
  --portfolio-revision-id "<portfolio_revision_id>" `
  --output "exchange/post_review_comparison.md"
```

The comparison reports probability corrections, clipping, fallbacks,
SelectionCandidate changes, added/deleted tickets, allocation and CashPosition
changes, Match Exposure changes, Stress Test changes, and concentration risk.
