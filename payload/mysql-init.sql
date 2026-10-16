CREATE DATABASE IF NOT EXISTS luminax CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS underfoot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE luminax;

CREATE TABLE IF NOT EXISTS memory_chat (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(64) NOT NULL,
  role_name VARCHAR(64) NOT NULL,
  content LONGTEXT NOT NULL,
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS memory_summary (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  summary_type VARCHAR(64) NOT NULL,
  content LONGTEXT NOT NULL,
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS daemon_state (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  state_key VARCHAR(128) NOT NULL,
  state_value LONGTEXT NOT NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_state_key (state_key)
);

CREATE TABLE IF NOT EXISTS telemetry_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  cpu_percent DECIMAL(6,2) NULL,
  memory_percent DECIMAL(6,2) NULL,
  disk_percent DECIMAL(6,2) NULL,
  docker_json JSON NULL,
  services_json JSON NULL
);

CREATE TABLE IF NOT EXISTS accounts_requested (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  provider VARCHAR(128) NOT NULL,
  purpose TEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  integration_scope VARCHAR(32) NOT NULL DEFAULT 'project',
  project_key VARCHAR(128) NULL,
  details_json JSON NULL
);

CREATE TABLE IF NOT EXISTS managed_tester_accounts (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  account_key VARCHAR(128) NOT NULL,
  provider VARCHAR(64) NOT NULL DEFAULT 'discord',
  account_type VARCHAR(32) NOT NULL DEFAULT 'tester',
  display_name VARCHAR(255) NULL,
  project_key VARCHAR(128) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  token_ciphertext LONGTEXT NULL,
  refresh_ciphertext LONGTEXT NULL,
  token_hint VARCHAR(64) NULL,
  token_updated_at TIMESTAMP NULL DEFAULT NULL,
  expires_at TIMESTAMP NULL DEFAULT NULL,
  last_active_at TIMESTAMP NULL DEFAULT NULL,
  tags_json JSON NULL,
  scopes_json JSON NULL,
  persona_json JSON NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_managed_tester_account_key (account_key),
  KEY idx_managed_tester_provider_status (provider, status),
  KEY idx_managed_tester_project (project_key)
);

CREATE TABLE IF NOT EXISTS site_projects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  site_name VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  root_path VARCHAR(512) NOT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'created',
  details_json JSON NULL
);

CREATE TABLE IF NOT EXISTS project_tasks (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  task_uid VARCHAR(64) NOT NULL,
  project_key VARCHAR(128) NULL,
  goal TEXT NOT NULL,
  entails LONGTEXT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  source VARCHAR(64) NOT NULL DEFAULT 'assistant',
  requested_by VARCHAR(128) NULL,
  due_date VARCHAR(64) NULL,
  created_at_unix BIGINT NULL,
  completed_at TIMESTAMP NULL DEFAULT NULL,
  completion_note TEXT NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_task_uid (task_uid),
  KEY idx_project_key (project_key),
  KEY idx_status (status)
);

CREATE TABLE IF NOT EXISTS house_rooms (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  room_key VARCHAR(128) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  x DECIMAL(10,2) NULL,
  y DECIMAL(10,2) NULL,
  z DECIMAL(10,2) NULL,
  rotation_deg DECIMAL(10,2) NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_room_key (room_key)
);

CREATE TABLE IF NOT EXISTS device_groups (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  group_key VARCHAR(128) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  group_type VARCHAR(64) NOT NULL DEFAULT 'custom',
  meta_json JSON NULL,
  UNIQUE KEY uq_group_key (group_key)
);

CREATE TABLE IF NOT EXISTS device_inventory (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  device_key VARCHAR(255) NOT NULL,
  hostname VARCHAR(255) NULL,
  ip_address VARCHAR(64) NULL,
  mac_address VARCHAR(64) NULL,
  vendor VARCHAR(255) NULL,
  device_type VARCHAR(128) NOT NULL DEFAULT 'unknown',
  source VARCHAR(64) NOT NULL DEFAULT 'scan',
  room_key VARCHAR(128) NULL,
  signal_strength INT NULL,
  capabilities_json JSON NULL,
  location_json JSON NULL,
  state_json JSON NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_device_key (device_key)
);

CREATE TABLE IF NOT EXISTS device_group_members (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  group_key VARCHAR(128) NOT NULL,
  device_key VARCHAR(255) NOT NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_group_device (group_key, device_key)
);

CREATE TABLE IF NOT EXISTS bug_reports (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(64) NOT NULL DEFAULT 'discord',
  reporter_name VARCHAR(255) NULL,
  reporter_id VARCHAR(64) NULL,
  channel_ref VARCHAR(255) NULL,
  severity VARCHAR(32) NOT NULL DEFAULT 'normal',
  title VARCHAR(255) NOT NULL,
  details LONGTEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS agent_registry (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  agent_key VARCHAR(128) NOT NULL,
  display_name VARCHAR(255) NOT NULL,
  role_type VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'idle',
  full_time TINYINT(1) NOT NULL DEFAULT 0,
  goals_text LONGTEXT NULL,
  config_json JSON NULL,
  last_seen_at TIMESTAMP NULL DEFAULT NULL,
  UNIQUE KEY uq_agent_key (agent_key)
);

CREATE TABLE IF NOT EXISTS agent_activity (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  agent_key VARCHAR(128) NOT NULL,
  activity_type VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'info',
  title VARCHAR(255) NOT NULL,
  details LONGTEXT NULL,
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS cognition_stream (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(128) NOT NULL,
  source_kind VARCHAR(64) NOT NULL DEFAULT 'system',
  thought_type VARCHAR(64) NOT NULL DEFAULT 'note',
  summary VARCHAR(255) NOT NULL,
  content LONGTEXT NOT NULL,
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS file_checkout_locks (
  lock_key VARCHAR(512) PRIMARY KEY,
  checkout_id VARCHAR(64) NOT NULL,
  root_name VARCHAR(32) NOT NULL,
  path TEXT NOT NULL,
  scope_kind VARCHAR(16) NOT NULL,
  holder VARCHAR(191) NOT NULL,
  session_id VARCHAR(191) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'checked_out',
  note TEXT NULL,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  lease_seconds INT NOT NULL,
  expires_at BIGINT NOT NULL,
  queue_json LONGTEXT NULL,
  nudges_json LONGTEXT NULL
);

CREATE TABLE IF NOT EXISTS windows_nodes (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  node_key VARCHAR(128) NOT NULL,
  hostname VARCHAR(255) NOT NULL,
  os_version VARCHAR(255) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'online',
  capabilities_json JSON NULL,
  services_json JSON NULL,
  last_seen_at TIMESTAMP NULL DEFAULT NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_windows_node_key (node_key)
);

CREATE TABLE IF NOT EXISTS windows_node_heartbeats (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  node_key VARCHAR(128) NOT NULL,
  hostname VARCHAR(255) NOT NULL,
  capabilities_json JSON NULL,
  services_json JSON NULL,
  metrics_json JSON NULL,
  meta_json JSON NULL
);

CREATE TABLE IF NOT EXISTS windows_node_jobs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  node_key VARCHAR(255) NOT NULL,
  requested_by VARCHAR(255) NULL,
  requested_via VARCHAR(64) NOT NULL DEFAULT 'ui',
  job_type VARCHAR(64) NOT NULL DEFAULT 'tool',
  action_key VARCHAR(128) NOT NULL,
  payload_json JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  result_json JSON NULL,
  error_text TEXT NULL,
  dispatched_at TIMESTAMP NULL DEFAULT NULL,
  completed_at TIMESTAMP NULL DEFAULT NULL,
  meta_json JSON NULL,
  INDEX idx_windows_node_jobs_node_status (node_key, status, id),
  INDEX idx_windows_node_jobs_status_created (status, created_at)
);

CREATE TABLE IF NOT EXISTS hive_debates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  debate_id VARCHAR(64) NOT NULL,
  prompt LONGTEXT NOT NULL,
  transcript_json JSON NULL,
  votes_json JSON NULL,
  winning_action VARCHAR(64) NOT NULL DEFAULT 'plan',
  consensus_count INT NOT NULL DEFAULT 0,
  project_task_uid VARCHAR(64) NULL,
  meta_json JSON NULL,
  UNIQUE KEY uq_hive_debate_id (debate_id)
);

CREATE TABLE IF NOT EXISTS finance_action_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  action_key VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'planned',
  amount_usd DECIMAL(12,2) NULL,
  consent_required TINYINT(1) NOT NULL DEFAULT 1,
  requested_by VARCHAR(128) NULL,
  summary TEXT NULL,
  details_json JSON NULL
);

CREATE TABLE IF NOT EXISTS game_projects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  project_key VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'planned',
  engine VARCHAR(64) NOT NULL DEFAULT 'unreal5',
  root_path VARCHAR(512) NOT NULL,
  details_json JSON NULL,
  UNIQUE KEY uq_game_project_key (project_key)
);
