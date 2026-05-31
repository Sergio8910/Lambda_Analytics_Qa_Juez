# Autogen Evaluation (RAG sintético)

## Endpoint
```bash
curl -X POST http://localhost:8000/v1/autogen/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "demo-agent",
    "prompt_base": "Responde en español y sin inventar.",
    "n_cases": 30,
    "metrics": ["task_success_deterministic", "format_compliance"],
    "audit_mode": "balanced",
    "seed": 7,
    "agent_http": {
      "url": "http://localhost:9000/agent",
      "headers": {},
      "timeout_ms": 8000
    }
  }'
```

## Windows (Invoke-WebRequest)
```powershell
$body = @{
  agent_name = "demo-agent"
  prompt_base = "Responde en español y sin inventar."
  n_cases = 30
  metrics = @("task_success_deterministic","format_compliance")
  audit_mode = "balanced"
  seed = 7
  agent_http = @{
    url = "http://localhost:9000/agent"
    headers = @{}
    timeout_ms = 8000
  }
} | ConvertTo-Json -Depth 5

Invoke-WebRequest -Uri "http://localhost:8000/v1/autogen/evaluate" -Method POST -ContentType "application/json" -Body $body
```

## Auto-evaluate (sin agente HTTP, usa run_agent interno)
```bash
curl -X POST http://localhost:8000/v1/auto-evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt_base": "Responde en español y sin inventar.",
    "metrics": [{"name":"task_success_deterministic","threshold":0.7,"enabled":true}],
    "n_cases": 30,
    "seed": 7,
    "run_id": "auto-eval-demo"
  }'
```
