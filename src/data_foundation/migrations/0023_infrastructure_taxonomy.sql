ALTER TABLE infrastructure_entities ADD COLUMN subtype TEXT NOT NULL DEFAULT 'unknown'
    CHECK (
        subtype = 'unknown'
        OR (
            entity_type = 'device'
            AND subtype IN (
                'workstation', 'laptop', 'desktop', 'server', 'vps',
                'virtual_machine', 'container_host', 'nas', 'router', 'firewall',
                'gateway', 'mobile', 'iot', 'network_appliance'
            )
        )
        OR (
            entity_type = 'service'
            AND subtype IN (
                'web_application', 'api', 'dashboard', 'database', 'cache',
                'message_queue', 'scheduler', 'background_worker', 'model_service',
                'embedding_service', 'reverse_proxy', 'vpn', 'dns', 'monitoring',
                'storage', 'container', 'system_service', 'runtime'
            )
        )
    );
ALTER TABLE infrastructure_entities ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (lifecycle_status IN ('unknown', 'planned', 'provisioning', 'active', 'suspended', 'retired'));
ALTER TABLE infrastructure_entities ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (health_status IN ('unknown', 'healthy', 'degraded', 'unhealthy', 'offline'));
ALTER TABLE infrastructure_entities ADD COLUMN environment TEXT NOT NULL DEFAULT 'unknown'
    CHECK (environment IN ('unknown', 'local', 'development', 'test', 'staging', 'production', 'disaster_recovery'));
ALTER TABLE infrastructure_entities ADD COLUMN exposure_scope TEXT NOT NULL DEFAULT 'unknown'
    CHECK (exposure_scope IN ('unknown', 'loopback', 'private_network', 'overlay_network', 'public_internet', 'offline'));

ALTER TABLE infrastructure_events ADD COLUMN event_category TEXT NOT NULL DEFAULT 'other'
    CHECK (event_category IN (
        'lifecycle', 'configuration', 'deployment', 'health', 'network',
        'security', 'ownership', 'capacity', 'other'
    ));
ALTER TABLE infrastructure_events ADD COLUMN normalized_event_type TEXT NOT NULL DEFAULT 'updated'
    CHECK (
        (event_category = 'lifecycle' AND normalized_event_type IN ('created', 'activated', 'suspended', 'retired'))
        OR (event_category = 'configuration' AND normalized_event_type IN ('configuration_changed', 'port_changed', 'path_changed', 'setting_changed'))
        OR (event_category = 'deployment' AND normalized_event_type IN ('deployed', 'upgraded', 'restarted'))
        OR (event_category = 'health' AND normalized_event_type IN ('health_changed', 'started', 'stopped', 'recovered', 'degraded'))
        OR (event_category = 'network' AND normalized_event_type IN ('endpoint_changed', 'exposure_changed', 'route_changed'))
        OR (event_category = 'security' AND normalized_event_type IN ('credential_rotated', 'access_policy_changed'))
        OR (event_category = 'ownership' AND normalized_event_type IN ('host_changed', 'owner_changed'))
        OR (event_category = 'capacity' AND normalized_event_type = 'capacity_changed')
        OR (event_category = 'other' AND normalized_event_type = 'updated')
    );
ALTER TABLE infrastructure_events ADD COLUMN evidence_contract_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_infrastructure_entities_taxonomy
    ON infrastructure_entities(entity_type, subtype, lifecycle_status, health_status, environment, exposure_scope);

CREATE INDEX IF NOT EXISTS idx_infrastructure_events_taxonomy_date
    ON infrastructure_events(event_category, normalized_event_type, business_date DESC, created_at DESC);

-- Resolve a legacy service host only when the legacy host label identifies
-- exactly one device.  Ambiguous and missing labels deliberately stay NULL.
UPDATE infrastructure_entities AS service
SET host_entity_id = (
    SELECT device.entity_id
    FROM infrastructure_entities AS device
    WHERE device.entity_type = 'device'
      AND lower(trim(device.name)) = lower(trim(json_extract(service.metadata_json, '$.host')))
    LIMIT 1
)
WHERE service.entity_type = 'service'
  AND service.host_entity_id IS NULL
  AND json_valid(service.metadata_json)
  AND trim(COALESCE(json_extract(service.metadata_json, '$.host'), '')) <> ''
  AND (
      SELECT COUNT(*)
      FROM infrastructure_entities AS candidate
      WHERE candidate.entity_type = 'device'
        AND lower(trim(candidate.name)) = lower(trim(json_extract(service.metadata_json, '$.host')))
  ) = 1;

-- Translate recognized legacy vocabulary.  Unrecognized free text becomes
-- unknown for readers and survives only as bounded legacy metadata.
UPDATE infrastructure_entities
SET subtype = CASE
        WHEN entity_type = 'device' AND lower(trim(kind)) IN (
            'workstation', 'laptop', 'desktop', 'server', 'vps', 'nas',
            'router', 'firewall', 'gateway', 'mobile', 'iot'
        ) THEN lower(trim(kind))
        WHEN entity_type = 'device' AND lower(trim(kind)) IN ('vm', 'virtual-machine', 'virtual_machine') THEN 'virtual_machine'
        WHEN entity_type = 'device' AND lower(trim(kind)) IN ('container-host', 'container_host') THEN 'container_host'
        WHEN entity_type = 'device' AND lower(trim(kind)) IN ('network-appliance', 'network_appliance') THEN 'network_appliance'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN (
            'api', 'dashboard', 'database', 'cache', 'scheduler', 'vpn', 'dns',
            'monitoring', 'storage', 'container', 'runtime'
        ) THEN lower(trim(kind))
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('web-app', 'web_application', 'web-application') THEN 'web_application'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('message-queue', 'message_queue', 'queue') THEN 'message_queue'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('background-worker', 'background_worker', 'worker') THEN 'background_worker'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('model-service', 'model_service') THEN 'model_service'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('embedding-service', 'embedding_service') THEN 'embedding_service'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('reverse-proxy', 'reverse_proxy', 'proxy') THEN 'reverse_proxy'
        WHEN entity_type = 'service' AND lower(trim(kind)) IN ('launchd-service', 'systemd-service', 'daemon', 'system_service') THEN 'system_service'
        ELSE 'unknown'
    END,
    lifecycle_status = CASE
        WHEN lower(trim(status)) IN ('planned') THEN 'planned'
        WHEN lower(trim(status)) IN ('provisioning', 'configured') THEN 'provisioning'
        WHEN lower(trim(status)) IN ('active', 'available', 'online', 'ready', 'running') THEN 'active'
        WHEN lower(trim(status)) IN ('suspended', 'paused', 'stopped', 'offline') THEN 'suspended'
        WHEN lower(trim(status)) IN ('retired', 'archived', 'removed') THEN 'retired'
        ELSE 'unknown'
    END,
    health_status = CASE
        WHEN lower(trim(status)) IN ('available', 'online', 'ready', 'running', 'healthy') THEN 'healthy'
        WHEN lower(trim(status)) = 'degraded' THEN 'degraded'
        WHEN lower(trim(status)) IN ('unhealthy', 'failed') THEN 'unhealthy'
        WHEN lower(trim(status)) IN ('offline', 'stopped') THEN 'offline'
        ELSE 'unknown'
    END,
    status = CASE
        WHEN lower(trim(status)) IN ('available', 'online', 'ready', 'running', 'healthy') THEN 'healthy'
        WHEN lower(trim(status)) IN ('degraded', 'unhealthy', 'failed', 'offline', 'stopped') THEN
            CASE lower(trim(status)) WHEN 'failed' THEN 'unhealthy' WHEN 'stopped' THEN 'offline' ELSE lower(trim(status)) END
        WHEN lower(trim(status)) IN ('planned', 'provisioning', 'active', 'suspended', 'retired') THEN lower(trim(status))
        WHEN lower(trim(status)) IN ('configured') THEN 'provisioning'
        WHEN lower(trim(status)) IN ('archived', 'removed') THEN 'retired'
        ELSE 'unknown'
    END,
    kind = CASE
        WHEN entity_type = 'device' AND lower(trim(kind)) IN (
            'workstation', 'laptop', 'desktop', 'server', 'vps', 'nas',
            'router', 'firewall', 'gateway', 'mobile', 'iot'
        ) THEN lower(trim(kind))
        WHEN entity_type = 'service' AND lower(trim(kind)) IN (
            'api', 'dashboard', 'database', 'cache', 'scheduler', 'vpn', 'dns',
            'monitoring', 'storage', 'container', 'runtime'
        ) THEN lower(trim(kind))
        ELSE 'unknown'
    END,
    endpoint = CASE WHEN trim(endpoint) = '' THEN '' ELSE '<network-endpoint>' END,
    path = CASE WHEN trim(path) = '' THEN '' ELSE '<local-config>' END,
    metadata_json = json_object(
        'legacyStatus', 'unknown',
        'legacyStatusPresent', CASE WHEN trim(status) = '' THEN 0 ELSE 1 END,
        'legacyKind', 'unknown',
        'legacyKindPresent', CASE WHEN trim(kind) = '' THEN 0 ELSE 1 END
    );

-- Legacy event prose and raw/evidence blobs predate the generalized evidence
-- contract and may contain credentials or concrete paths.  Keep only a safe,
-- typed historical marker; no raw value is promoted into the v2 authority.
UPDATE infrastructure_events
SET event_category = CASE
        WHEN lower(trim(event_type)) IN ('created', 'activated', 'suspended', 'retired') THEN 'lifecycle'
        WHEN lower(trim(event_type)) IN ('configuration_changed', 'port_changed', 'path_changed', 'setting_changed') THEN 'configuration'
        WHEN lower(trim(event_type)) IN ('deployed', 'upgraded', 'restarted') THEN 'deployment'
        WHEN lower(trim(event_type)) IN ('health_changed', 'started', 'stopped', 'recovered', 'degraded') THEN 'health'
        WHEN lower(trim(event_type)) IN ('endpoint_changed', 'exposure_changed', 'route_changed') THEN 'network'
        WHEN lower(trim(event_type)) IN ('credential_rotated', 'access_policy_changed') THEN 'security'
        WHEN lower(trim(event_type)) IN ('host_changed', 'owner_changed') THEN 'ownership'
        WHEN lower(trim(event_type)) = 'capacity_changed' THEN 'capacity'
        ELSE 'other'
    END,
    normalized_event_type = CASE
        WHEN lower(trim(event_type)) IN (
            'created', 'activated', 'suspended', 'retired',
            'configuration_changed', 'port_changed', 'path_changed', 'setting_changed',
            'deployed', 'upgraded', 'restarted',
            'health_changed', 'started', 'stopped', 'recovered', 'degraded',
            'endpoint_changed', 'exposure_changed', 'route_changed',
            'credential_rotated', 'access_policy_changed', 'host_changed',
            'owner_changed', 'capacity_changed', 'updated'
        ) THEN lower(trim(event_type))
        ELSE 'updated'
    END,
    event_type = CASE
        WHEN lower(trim(event_type)) IN (
            'created', 'activated', 'suspended', 'retired',
            'configuration_changed', 'port_changed', 'path_changed', 'setting_changed',
            'deployed', 'upgraded', 'restarted',
            'health_changed', 'started', 'stopped', 'recovered', 'degraded',
            'endpoint_changed', 'exposure_changed', 'route_changed',
            'credential_rotated', 'access_policy_changed', 'host_changed',
            'owner_changed', 'capacity_changed', 'updated'
        ) THEN lower(trim(event_type))
        ELSE 'updated'
    END,
    summary = 'Legacy infrastructure change (generalized during taxonomy migration).',
    field = CASE
        WHEN lower(trim(field)) IN (
            'subtype', 'lifecycle_status', 'health_status', 'environment',
            'exposure_scope', 'port', 'endpoint', 'path', 'protocol', 'location',
            'host', 'credential', 'configuration', 'version', 'capacity', 'other'
        ) THEN lower(trim(field))
        WHEN lower(trim(field)) = 'status' THEN 'health_status'
        ELSE 'other'
    END,
    previous_value = CASE
        WHEN lower(trim(field)) = 'credential' AND trim(previous_value) <> '' THEN '[redacted]'
        WHEN lower(trim(field)) = 'endpoint' AND trim(previous_value) <> '' THEN '<network-endpoint>'
        WHEN lower(trim(field)) = 'path' AND trim(previous_value) <> '' THEN '<local-config>'
        WHEN lower(trim(field)) = 'port' AND previous_value NOT GLOB '*[^0-9]*' THEN previous_value
        ELSE ''
    END,
    current_value = CASE
        WHEN lower(trim(field)) = 'credential' AND trim(current_value) <> '' THEN '[redacted]'
        WHEN lower(trim(field)) = 'endpoint' AND trim(current_value) <> '' THEN '<network-endpoint>'
        WHEN lower(trim(field)) = 'path' AND trim(current_value) <> '' THEN '<local-config>'
        WHEN lower(trim(field)) = 'port' AND current_value NOT GLOB '*[^0-9]*' THEN current_value
        ELSE ''
    END,
    evidence_json = '[]',
    raw_json = '{}',
    evidence_contract_json = '{}';
