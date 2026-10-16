using System;
using System.Collections.Generic;
using System.IO;

namespace Underfoot.Unity.EditorTools
{
    internal sealed class EpochPortalProjectConfig
    {
        public string EnvFilePath { get; set; } = string.Empty;
        public string LuminaxBridgeHost { get; set; } = "127.0.0.1";
        public int LuminaxBridgePort { get; set; } = 8462;
        public string LuminaxBridgeToken { get; set; } = string.Empty;
        public string LuminaxLocalOperatorId { get; set; } = string.Empty;
        public string EpochPortalDatabaseAssetPath { get; set; } = "Assets/Data/EpochPortalDatabase.json";
        public string EpochPortalQueueFilePath { get; set; } = "server/runtime/epoch_portal_requests.jsonl";
        public string UnityAssetStoreCachePath { get; set; } = string.Empty;
        public string UnityAssetStoreOwnershipCachePath { get; set; } = string.Empty;

        public static EpochPortalProjectConfig CreateDefault()
        {
            return new EpochPortalProjectConfig();
        }

        public static EpochPortalProjectConfig LoadFromSearchRoots(IEnumerable<string> searchRoots)
        {
            var envPath = FindEnvFile(searchRoots);
            if (string.IsNullOrWhiteSpace(envPath) || !File.Exists(envPath))
            {
                return CreateDefault();
            }

            return FromDictionary(ParseFile(envPath), envPath);
        }

        private static EpochPortalProjectConfig FromDictionary(Dictionary<string, string> values, string envPath)
        {
            var config = CreateDefault();
            config.EnvFilePath = envPath;
            config.LuminaxBridgeHost = GetValue(values, "UNDERFOOT_LUMINAX_BRIDGE_HOST", config.LuminaxBridgeHost);
            config.LuminaxBridgePort = GetIntValue(values, "UNDERFOOT_LUMINAX_BRIDGE_PORT", config.LuminaxBridgePort);
            config.LuminaxBridgeToken = GetValue(values, "UNDERFOOT_LUMINAX_BRIDGE_TOKEN", config.LuminaxBridgeToken);
            config.LuminaxLocalOperatorId = GetValue(values, "UNDERFOOT_LUMINAX_LOCAL_OPERATOR_ID", config.LuminaxLocalOperatorId);
            config.EpochPortalDatabaseAssetPath = GetValue(values, "UNDERFOOT_EPOCH_PORTAL_DATABASE_ASSET_PATH", config.EpochPortalDatabaseAssetPath);
            config.EpochPortalQueueFilePath = GetValue(values, "UNDERFOOT_EPOCH_PORTAL_QUEUE_PATH", config.EpochPortalQueueFilePath);
            config.UnityAssetStoreCachePath = GetValue(values, "UNDERFOOT_UNITY_ASSET_STORE_CACHE_PATH", config.UnityAssetStoreCachePath);
            config.UnityAssetStoreOwnershipCachePath = GetValue(values, "UNDERFOOT_UNITY_ASSET_STORE_OWNERSHIP_CACHE_PATH", config.UnityAssetStoreOwnershipCachePath);
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

        private static string GetValue(Dictionary<string, string> values, string key, string fallback)
        {
            return values.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value) ? value : fallback;
        }

        private static int GetIntValue(Dictionary<string, string> values, string key, int fallback)
        {
            return values.TryGetValue(key, out var value) && int.TryParse(value, out var parsed) ? parsed : fallback;
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
