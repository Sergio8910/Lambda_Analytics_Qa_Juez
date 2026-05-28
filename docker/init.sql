-- ============================================================================
-- CONTROL PLANE SCHEMA
-- Inicialización de base de datos para Juez v2
-- ============================================================================

-- Crear extensiones
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================================
-- TABLA: jobs
-- Descripción: Registro maestro de cada job de evaluación
-- ============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    run_id VARCHAR(255),
    kind VARCHAR(50) NOT NULL,  -- quality, rag, security, perf, smoke
    status VARCHAR(50) NOT NULL DEFAULT 'queued',  -- queued, running, succeeded, failed, cancelled
    phase VARCHAR(100),  -- sub-fase de ejecución (e.g., retrieval, model_call, scoring)
    
    -- Target
    app_id VARCHAR(255),
    environment VARCHAR(50),  -- staging, prod, etc
    base_url TEXT,
    
    -- Specification versions (para reproducibilidad)
    spec_version VARCHAR(50),
    model_version VARCHAR(100),
    rag_index_version VARCHAR(100),
    dataset_version VARCHAR(100),
    suite_version VARCHAR(100),
    
    -- Execution metadata
    max_parallelism INT DEFAULT 4,
    deadline_s INT DEFAULT 1800,  -- 30 min default
    
    -- Counters
    total_cases INT DEFAULT 0,
    completed_cases INT DEFAULT 0,
    failed_cases INT DEFAULT 0,
    
    -- Costs
    cost_usd DECIMAL(10, 4) DEFAULT 0,
    token_usage INT DEFAULT 0,
    
    -- Webhooks
    webhook_url TEXT,
    webhook_secret VARCHAR(255),
    webhook_events TEXT[],  -- array de eventos a notificar
    
    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_heartbeat_at TIMESTAMP,
    
    -- Full spec JSON (para auditoría)
    spec_json JSONB,
    
    -- Results
    summary_json JSONB,
    
    -- Índices para queries comunes
    CONSTRAINT jobs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_jobs_tenant_id ON jobs(tenant_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_kind ON jobs(kind);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX idx_jobs_tenant_status ON jobs(tenant_id, status);

-- ============================================================================
-- TABLA: tenants
-- Descripción: Registro de tenants (clientes multi-tenant)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'active',  -- active, suspended, deleted
    
    -- Rate limits
    rate_limit_jobs_per_minute INT DEFAULT 60,
    rate_limit_api_per_minute INT DEFAULT 600,
    
    -- Quotas
    monthly_job_limit INT DEFAULT 10000,
    monthly_cost_limit DECIMAL(10, 2),
    
    -- Metadata
    metadata JSONB,
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT tenants_pkey PRIMARY KEY (tenant_id)
);

CREATE INDEX idx_tenants_status ON tenants(status);

-- ============================================================================
-- TABLA: suites
-- Descripción: Definiciones de suites de pruebas
-- ============================================================================
CREATE TABLE IF NOT EXISTS suites (
    suite_id VARCHAR(255) PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL,
    kind VARCHAR(50) NOT NULL,  -- smoke, quality, rag
    version VARCHAR(50) NOT NULL,
    
    -- Definición
    datasets VARCHAR(255)[],
    scenarios VARCHAR(100)[],
    sample_size INT,
    
    -- Thresholds
    thresholds JSONB,  -- {"answer_relevancy": 0.80, "faithfulness": 0.90}
    
    -- Release gate
    release_gate_enabled BOOLEAN DEFAULT TRUE,
    release_gate_rule JSONB,
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT suites_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_suites_tenant_id ON suites(tenant_id);
CREATE INDEX idx_suites_kind ON suites(kind);

-- ============================================================================
-- TABLA: datasets
-- Descripción: Registro de datasets de prueba
-- ============================================================================
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id VARCHAR(255) PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    
    cases_count INT,
    hash VARCHAR(64),  -- SHA256 para reproducibilidad
    
    -- Descripción
    description TEXT,
    scenarios VARCHAR(100)[],
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT datasets_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_datasets_tenant_id ON datasets(tenant_id);
CREATE INDEX idx_datasets_hash ON datasets(hash);

-- ============================================================================
-- TABLA: evaluation_results
-- Descripción: Resultados por caso dentro de un job
-- ============================================================================
CREATE TABLE IF NOT EXISTS evaluation_results (
    result_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL,
    case_id VARCHAR(255) NOT NULL,
    
    -- Entrada
    input_text TEXT,
    expected_behavior TEXT,
    retrieval_context TEXT[],
    
    -- Salida del agente
    agent_response TEXT,
    response_latency_ms INT,
    
    -- Métricas (flat para queries)
    exact_match DECIMAL(3, 2),
    format_compliance DECIMAL(3, 2),
    answer_relevancy DECIMAL(3, 2),
    faithfulness DECIMAL(3, 2),
    task_success DECIMAL(3, 2),
    completeness DECIMAL(3, 2),
    consistency DECIMAL(3, 2),
    
    rag_precision_at_k DECIMAL(3, 2),
    rag_recall_at_k DECIMAL(3, 2),
    rag_document_safety DECIMAL(3, 2),
    
    injection_success BOOLEAN,
    exfiltration_success BOOLEAN,
    
    -- Análisis
    passed BOOLEAN,
    failure_reason TEXT,
    feedback TEXT,
    
    -- Telemetría
    trace_id VARCHAR(255),
    span_id VARCHAR(255),
    
    cost_usd DECIMAL(8, 4),
    tokens_used INT,
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT evaluation_results_job_fk FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX idx_eval_results_job_id ON evaluation_results(job_id);
CREATE INDEX idx_eval_results_case_id ON evaluation_results(case_id);
CREATE INDEX idx_eval_results_passed ON evaluation_results(passed);
CREATE INDEX idx_eval_results_trace_id ON evaluation_results(trace_id);

-- ============================================================================
-- TABLA: baselines
-- Descripción: Baselines para comparación de versiones
-- ============================================================================
CREATE TABLE IF NOT EXISTS baselines (
    baseline_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    kind VARCHAR(50) NOT NULL,  -- quality, rag, etc
    
    -- Qué comparamos contra
    model_version VARCHAR(100),
    rag_index_version VARCHAR(100),
    prompt_version VARCHAR(100),
    
    -- Métricas agregadas
    quality_score DECIMAL(3, 2),
    answer_relevancy DECIMAL(3, 2),
    faithfulness DECIMAL(3, 2),
    task_success DECIMAL(3, 2),
    
    latency_p50 INT,
    latency_p95 INT,
    latency_p99 INT,
    
    cost_usd DECIMAL(10, 4),
    pass_rate DECIMAL(3, 2),
    
    -- Metadata
    num_cases INT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT baselines_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_baselines_tenant_kind ON baselines(tenant_id, kind);

-- ============================================================================
-- TABLA: webhooks
-- Descripción: Registro de entregas de webhooks
-- ============================================================================
CREATE TABLE IF NOT EXISTS webhooks (
    webhook_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL,
    event_type VARCHAR(100),  -- job.completed, job.failed, etc
    
    status VARCHAR(50) DEFAULT 'pending',  -- pending, delivered, failed
    attempt INT DEFAULT 0,
    max_attempts INT DEFAULT 5,
    
    payload JSONB,
    response_code INT,
    response_body TEXT,
    
    scheduled_at TIMESTAMP,
    delivered_at TIMESTAMP,
    next_retry_at TIMESTAMP,
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT webhooks_job_fk FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX idx_webhooks_job_id ON webhooks(job_id);
CREATE INDEX idx_webhooks_status ON webhooks(status);
CREATE INDEX idx_webhooks_next_retry ON webhooks(next_retry_at) WHERE status = 'pending';

-- ============================================================================
-- TABLA: api_keys
-- Descripción: Gestión de API keys por tenant
-- ============================================================================
CREATE TABLE IF NOT EXISTS api_keys (
    key_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    
    -- Key (hashed, nunca almacenar plaintext)
    key_hash VARCHAR(255) NOT NULL UNIQUE,
    key_prefix VARCHAR(20),  -- primeros 20 chars para UI
    
    -- Scopes
    scopes VARCHAR(100)[],  -- ["read:jobs", "write:jobs", "read:results"]
    
    status VARCHAR(50) DEFAULT 'active',  -- active, revoked
    
    -- Rotación
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    rotated_at TIMESTAMP,
    
    CONSTRAINT api_keys_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_api_keys_tenant_id ON api_keys(tenant_id);
CREATE INDEX idx_api_keys_status ON api_keys(status);
CREATE UNIQUE INDEX idx_api_keys_hash ON api_keys(key_hash);

-- ============================================================================
-- TABLA: audit_logs
-- Descripción: Auditoría de cambios
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    
    action VARCHAR(100) NOT NULL,  -- job_created, job_completed, config_changed, etc
    resource_type VARCHAR(100),  -- job, dataset, suite, api_key
    resource_id VARCHAR(255),
    
    actor VARCHAR(255),  -- user email, service account, etc
    ip_address INET,
    
    changes JSONB,  -- before/after
    
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT audit_logs_tenant_fk FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- ============================================================================
-- Crear tenant por defecto para dev
-- ============================================================================
INSERT INTO tenants (tenant_id, name, status)
VALUES ('dev', 'Development Tenant', 'active')
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO tenants (tenant_id, name, status)
VALUES ('internal', 'Internal Testing', 'active')
ON CONFLICT (tenant_id) DO NOTHING;

-- ============================================================================
-- Permisos y roles (para future RBAC)
-- ============================================================================
-- CREATE ROLE juez_admin;
-- CREATE ROLE juez_api;
-- GRANT ALL ON ALL TABLES IN SCHEMA public TO juez_admin;
-- GRANT SELECT, INSERT, UPDATE ON jobs, evaluation_results, baselines TO juez_api;
