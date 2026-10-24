# Azure Production Evolution

```mermaid
flowchart LR
  Internet --> FrontDoor[Front Door + WAF]
  FrontDoor --> APIM[API Management]
  APIM --> Apps[Container Apps or App Service]
  Apps --> PG[Azure Database for PostgreSQL]
  Apps --> Redis[Azure Managed Redis]
  Apps --> SB[Azure Service Bus]
  Apps --> Blob[Private Blob Storage]
  Apps --> KV[Key Vault + managed identity]
  Apps --> AI[Application Insights / OpenTelemetry]
  SB --> Workers[Container Apps worker apps]
```

Container Apps is a lower-operations option for APIs/workers; App Service is managed web hosting; AKS adds orchestration control and operational cost and is not needed initially. PostgreSQL provides managed relational storage; Service Bus provides durable queues/topics and DLQs; Blob Storage holds private documents. Use private networking, least privilege, encryption, monitored backups, and tested restore/failover.

A paired-region plan states RPO/RTO and validates database replication and queue/blob recovery. This is a target, not deployed infrastructure.
