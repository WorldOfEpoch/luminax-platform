using System;
using System.Collections.Generic;
using System.IO;

namespace Underfoot.Unity.EditorTools
{
    internal sealed class DariusProjectConfig
    {
        public string EnvFilePath { get; set; } = string.Empty;
        public string LocalBridgeHost { get; set; } = "127.0.0.1";
        public int LocalBridgePort { get; set; } = 8462;
        public string LocalBridgeToken { get; set; } = string.Empty;
        public string LocalOperatorId { get; set; } = string.Empty;
        public string DariusProjectTitle { get; set; } = "Project";
        public string DariusProjectCategory { get; set; } = "generic";
        public string DariusDatabaseAssetPath { get; set; } = "Assets/Data/DariusDatabase.json";
        public string DariusLegacyDatabaseAssetPath { get; set; } = "Assets/Data/EpochPortalDatabase.json";
        public string DariusQueueFilePath { get; set; } = "server/runtime/darius_requests.jsonl";
        public string DariusLegacyQueueFilePath { get; set; } = "server/runtime/epoch_portal_requests.jsonl";
        public string DariusProjectMemoryPath { get; set; } = "shared/darius_project_memory.json";
        public string DariusRepoRulesPath { get; set; } = "shared/darius_repo_rules.md";
        public string DariusServerRefsPath { get; set; } = "shared/darius_server_refs.json";
        public string DariusWorkflowPath { get; set; } = "shared/darius_workflow.json";
        public string DariusThreadRegistryPath { get; set; } = "server/runtime/darius_thread_registry.json";
        public string DariusAssignmentsPath { get; set; } = "server/runtime/darius_assignments.json";
        public string DariusProjectKey { get; set; } = "project";
        public string DariusAgentId { get; set; } = string.Empty;
        public string DariusMachineLabel { get; set; } = Environment.MachineName;
        public string DariusRelayUrl { get; set; } = string.Empty;
        public string DariusRelayToken { get; set; } = string.Empty;
        public int DariusLocalAgentPort { get; set; } = 8462;
        public string DariusCodexCommand { get; set; } = "codex";
        public string UnityAssetStoreCachePath { get; set; } = string.Empty;
        public string UnityAssetStoreOwnershipCachePath { get; set; } = string.Empty;

        public static DariusProjectConfig CreateDefault()
        {
            return new DariusProjectConfig();
        }

        public static DariusProjectConfig LoadFromSearchRoots(IEnumerable<string> searchRoots)
        {
            var envPath = FindEnvFile(searchRoots);
            if (string.IsNullOrWhiteSpace(envPath) || !File.Exists(envPath))
            {
                return CreateDefault();
            }

            return FromDictionary(ParseFile(envPath), envPath);
        }

        private static DariusProjectConfig FromDictionary(Dictionary<string, string> values, string envPath)
        {
            var config = CreateDefault();
            config.EnvFilePath = envPath;
            config.LocalBridgeHost = GetValue(values, config.LocalBridgeHost, "DARIUS_LOCAL_AGENT_HOST", "UNDERFOOT_LUMINAX_BRIDGE_HOST");
            config.LocalBridgePort = GetIntValue(values, config.LocalBridgePort, "DARIUS_LOCAL_AGENT_PORT", "UNDERFOOT_LUMINAX_BRIDGE_PORT");
            config.LocalBridgeToken = GetValue(values, config.LocalBridgeToken, "DARIUS_LOCAL_AGENT_TOKEN", "UNDERFOOT_LUMINAX_BRIDGE_TOKEN");
            config.LocalOperatorId = GetValue(values, config.LocalOperatorId, "DARIUS_LOCAL_OPERATOR_ID", "UNDERFOOT_LUMINAX_LOCAL_OPERATOR_ID");
            config.DariusProjectTitle = GetValue(values, config.DariusProjectTitle, "DARIUS_PROJECT_TITLE", "UNDERFOOT_PROJECT_TITLE");
            config.DariusProjectCategory = GetValue(values, config.DariusProjectCategory, "DARIUS_PROJECT_CATEGORY", "UNDERFOOT_PROJECT_CATEGORY");
            config.DariusDatabaseAssetPath = GetValue(
                values,
                config.DariusDatabaseAssetPath,
                "DARIUS_DATABASE_ASSET_PATH",
                "UNDERFOOT_DARIUS_DATABASE_ASSET_PATH",
                "UNDERFOOT_EPOCH_PORTAL_DATABASE_ASSET_PATH");
            config.DariusLegacyDatabaseAssetPath = GetValue(values, config.DariusLegacyDatabaseAssetPath, "UNDERFOOT_EPOCH_PORTAL_DATABASE_ASSET_PATH");
            config.DariusQueueFilePath = GetValue(
                values,
                config.DariusQueueFilePath,
                "DARIUS_QUEUE_PATH",
                "UNDERFOOT_DARIUS_QUEUE_PATH",
                "UNDERFOOT_EPOCH_PORTAL_QUEUE_PATH");
            config.DariusLegacyQueueFilePath = GetValue(values, config.DariusLegacyQueueFilePath, "UNDERFOOT_EPOCH_PORTAL_QUEUE_PATH");
            config.DariusProjectMemoryPath = GetValue(values, config.DariusProjectMemoryPath, "DARIUS_PROJECT_MEMORY_PATH", "UNDERFOOT_DARIUS_PROJECT_MEMORY_PATH");
            config.DariusRepoRulesPath = GetValue(values, config.DariusRepoRulesPath, "DARIUS_REPO_RULES_PATH", "UNDERFOOT_DARIUS_REPO_RULES_PATH");
            config.DariusServerRefsPath = GetValue(values, config.DariusServerRefsPath, "DARIUS_SERVER_REFS_PATH", "UNDERFOOT_DARIUS_SERVER_REFS_PATH");
            config.DariusWorkflowPath = GetValue(values, config.DariusWorkflowPath, "DARIUS_WORKFLOW_PATH", "UNDERFOOT_DARIUS_WORKFLOW_PATH");
            config.DariusThreadRegistryPath = GetValue(values, config.DariusThreadRegistryPath, "DARIUS_THREAD_REGISTRY_PATH", "UNDERFOOT_DARIUS_THREAD_REGISTRY_PATH");
            config.DariusAssignmentsPath = GetValue(values, config.DariusAssignmentsPath, "DARIUS_ASSIGNMENTS_PATH", "UNDERFOOT_DARIUS_ASSIGNMENTS_PATH");
            config.DariusProjectKey = GetValue(values, config.DariusProjectKey, "DARIUS_PROJECT_KEY", "UNDERFOOT_DARIUS_PROJECT_KEY", "UNDERFOOT_PROJECT_NAME");
            config.DariusAgentId = GetValue(values, config.DariusAgentId, "DARIUS_AGENT_ID", "UNDERFOOT_DARIUS_AGENT_ID");
            config.DariusMachineLabel = GetValue(values, config.DariusMachineLabel, "DARIUS_MACHINE_LABEL", "UNDERFOOT_DARIUS_MACHINE_LABEL");
            config.DariusRelayUrl = GetValue(values, config.DariusRelayUrl, "DARIUS_RELAY_URL", "UNDERFOOT_DARIUS_RELAY_URL");
            config.DariusRelayToken = GetValue(values, config.DariusRelayToken, "DARIUS_RELAY_TOKEN", "UNDERFOOT_DARIUS_RELAY_TOKEN");
            config.DariusLocalAgentPort = GetIntValue(values, config.DariusLocalAgentPort, "DARIUS_LOCAL_AGENT_PORT", "UNDERFOOT_DARIUS_LOCAL_AGENT_PORT", "UNDERFOOT_LUMINAX_BRIDGE_PORT");
            config.DariusCodexCommand = GetValue(values, config.DariusCodexCommand, "DARIUS_CODEX_COMMAND", "UNDERFOOT_DARIUS_CODEX_COMMAND");
            config.UnityAssetStoreCachePath = GetValue(values, config.UnityAssetStoreCachePath, "UNDERFOOT_UNITY_ASSET_STORE_CACHE_PATH");
            config.UnityAssetStoreOwnershipCachePath = GetValue(values, config.UnityAssetStoreOwnershipCachePath, "UNDERFOOT_UNITY_ASSET_STORE_OWNERSHIP_CACHE_PATH");
            return config;
        }

        private static Dictionary<string, string> ParseFile(string envPath)
        {
            var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var rawLine in File.ReadAllLines(envPath))
            {
                var line = rawLine.Trim();
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                {
                    continue;
                }

                var separatorIndex = line.IndexOf('=');
                if (separatorIndex <= 0)
                {
                    continue;
                }

                var key = line.Substring(0, separatorIndex).Trim();
                var value = line.Substring(separatorIndex + 1).Trim();
                values[key] = TrimQuotes(value);
            }

            return values;
        }

        private static string FindEnvFile(IEnumerable<string> searchRoots)
        {
            // Same explicit configuration owner as the Python CLI; no parent-directory scan.
            var configured = Environment.GetEnvironmentVariable("DARIUS_ENV_FILE");
            if (string.IsNullOrWhiteSpace(configured)) configured = Environment.GetEnvironmentVariable("LUMINAX_TOOLS_ENV_FILE");
            return !string.IsNullOrWhiteSpace(configured) && File.Exists(configured)
                ? Path.GetFullPath(configured) : string.Empty;
        }

        private static string GetValue(Dictionary<string, string> values, string fallback, params string[] keys)
        {
            foreach (var key in keys)
            {
                if (values.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }

            return fallback;
        }

        private static int GetIntValue(Dictionary<string, string> values, int fallback, params string[] keys)
        {
            foreach (var key in keys)
            {
                if (values.TryGetValue(key, out var value) && int.TryParse(value, out var parsed))
                {
                    return parsed;
                }
            }

            return fallback;
        }

        private static string TrimQuotes(string value)
        {
            if (value.Length >= 2)
            {
                if ((value[0] == '"' && value[value.Length - 1] == '"') ||
                    (value[0] == '\'' && value[value.Length - 1] == '\''))
                {
                    return value.Substring(1, value.Length - 2);
                }
            }

            return value;
        }
    }
}
