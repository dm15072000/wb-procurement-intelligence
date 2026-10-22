# Jobs

`refresh_job.json` is the weekly continuous-learning job (docs/03_model_rag_design.md §7): ingest → silver →
documents → suppliers → **data-quality gate** (notebook 12; graph and both models wait for it, so a failed check stops
them) → graph → extraction → embeddings + index sync → model retrain (champion/challenger) →
re-bid rates → early warnings → RAG regression evaluation (fails the run if recall@5 drops by more than 5 points).

- Replace `<GIT_FOLDER>` with the workspace path of this repo's Git folder.
- It is created **paused**. Create it (Jobs UI → JSON import, or `databricks jobs create --json @jobs/refresh_job.json`)
  only when you decide to; CLAUDE.md says no job runs without your go-ahead.
- Serverless compute: no cluster block is needed.
