# Python Application Structure

Planned package boundaries: `api/`, `application/`, `domain/`, `infrastructure/`, `workers/`, and `config/`. Keep controllers thin; ledger invariants belong in domain/application code and persistence transactions in infrastructure.
