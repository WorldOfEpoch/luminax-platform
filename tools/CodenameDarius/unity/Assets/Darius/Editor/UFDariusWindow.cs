#nullable enable
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Underfoot.Unity.EditorTools
{
    public sealed class UFDariusWindow : EditorWindow
    {
        private const string WindowTitle = "Codename Darius";
        private const string BackendScriptRelativePath = "tools/darius_agent_cli.py";

        private enum DariusTab
        {
            Models = 0,
            Images = 1,
            StoreAssets = 2,
            Chat = 3,
        }

        [Serializable]
        private sealed class DariusDatabase
        {
            public int version = 2;
            public long updatedAtEpoch;
            public int retryLimit = 8;
            public List<ModelJobRecord> modelJobs = new List<ModelJobRecord>();
            public List<ImageJobRecord> imageJobs = new List<ImageJobRecord>();
            public List<StoreAssetRecord> storeAssets = new List<StoreAssetRecord>();
            public List<OwnedAssetHintRecord> ownedAssetHints = new List<OwnedAssetHintRecord>();
            public List<DariusMessageRecord> dariusMessages = new List<DariusMessageRecord>();
            public DariusRouteRecord route = new DariusRouteRecord();
            public List<RelayMessageRecord> relayMessages = new List<RelayMessageRecord>();
            public ProjectMemoryRecord projectMemory = new ProjectMemoryRecord();
            public ProjectPackRecord projectPack = new ProjectPackRecord();
            public ThreadRegistryRecord thread = new ThreadRegistryRecord();
        }

        [Serializable]
        private sealed class ModelJobRecord
        {
            public string id = string.Empty;
            public long createdAtEpoch;
            public long updatedAtEpoch;
            public string displayName = string.Empty;
            public string assetKind = string.Empty;
            public string requestMode = string.Empty;
            public string status = string.Empty;
            public string taskId = string.Empty;
            public string previewTaskId = string.Empty;
            public int retryCount;
            public string prompt = string.Empty;
            public string negativePrompt = string.Empty;
            public string texturePrompt = string.Empty;
            public string referenceImagePath = string.Empty;
            public string expectedAssetName = string.Empty;
            public string outputFolder = string.Empty;
            public List<string> downloadedFiles = new List<string>();
            public string thumbnailPath = string.Empty;
            public string sourceTaskFile = string.Empty;
            public string lastError = string.Empty;
            public int lastProgress;
            public string historyTaskIdsCsv = string.Empty;
        }

        [Serializable]
        private sealed class ImageJobRecord
        {
            public string id = string.Empty;
            public long createdAtEpoch;
            public long updatedAtEpoch;
            public string displayName = string.Empty;
            public string prompt = string.Empty;
            public string negativePrompt = string.Empty;
            public string aspectRatio = string.Empty;
            public string taskId = string.Empty;
            public string status = string.Empty;
            public string outputFolder = string.Empty;
            public List<string> downloadedFiles = new List<string>();
            public string sourceTaskFile = string.Empty;
            public string lastError = string.Empty;
        }

        [Serializable]
        private sealed class StoreAssetRecord
        {
            public string id = string.Empty;
            public string displayName = string.Empty;
            public string publisher = string.Empty;
            public string localPackagePath = string.Empty;
            public long fileSizeBytes;
            public long lastModifiedEpoch;
            public string status = string.Empty;
            public string sourceRoot = string.Empty;
        }

        [Serializable]
        private sealed class OwnedAssetHintRecord
        {
            public string id = string.Empty;
            public string assetVersionId = string.Empty;
            public bool isAssetProject;
        }

        [Serializable]
        private sealed class DariusMessageRecord
        {
            public string id = string.Empty;
            public long createdAtEpoch;
            public long updatedAtEpoch;
            public string role = string.Empty;
            public string text = string.Empty;
            public string status = string.Empty;
            public string projectKey = string.Empty;
            public string activeSessionId = string.Empty;
            public string agentId = string.Empty;
            public string machineLabel = string.Empty;
            public string source = string.Empty;
            public string verificationSummary = string.Empty;
            public string error = string.Empty;
            public List<string> attachments = new List<string>();
            public List<string> artifactPaths = new List<string>();
        }

        [Serializable]
        private sealed class DariusRouteRecord
        {
            public string projectKey = string.Empty;
            public string projectTitle = string.Empty;
            public string projectCategory = string.Empty;
            public string routeStatus = string.Empty;
            public string agentId = string.Empty;
            public string machineLabel = string.Empty;
            public string workspaceRoot = string.Empty;
            public string unityProjectPath = string.Empty;
            public string activeCodexSessionId = string.Empty;
            public string activeLocalThreadId = string.Empty;
            public string bootstrapStatus = string.Empty;
            public string threadRegistryPath = string.Empty;
            public long lastBootstrapEpoch;
            public long lastHeartbeatEpoch;
            public List<string> capabilities = new List<string>();
            public string detail = string.Empty;
        }

        [Serializable]
        private sealed class RelayMessageRecord
        {
            public string id = string.Empty;
            public long createdAtEpoch;
            public string subject = string.Empty;
            public string body = string.Empty;
            public List<string> filePaths = new List<string>();
            public string status = string.Empty;
            public string queueFilePath = string.Empty;
        }

        [Serializable]
        private sealed class ProjectMemoryRecord
        {
            public string projectKey = string.Empty;
            public string gameTitle = string.Empty;
            public string conceptSummary = string.Empty;
            public string updatedBy = string.Empty;
            public long updatedAtEpoch;
        }

        [Serializable]
        private sealed class ProjectPackRecord
        {
            public string projectKey = string.Empty;
            public string projectTitle = string.Empty;
            public string projectCategory = string.Empty;
            public string bootstrapStatus = string.Empty;
            public string projectMemoryPath = string.Empty;
            public string repoRulesPath = string.Empty;
            public string serverRefsPath = string.Empty;
            public string workflowPath = string.Empty;
            public string threadRegistryPath = string.Empty;
            public string repoRulesPreview = string.Empty;
            public long lastBootstrapEpoch;
            public int workflowTaskCount;
            public List<string> requiredContextFiles = new List<string>();
            public List<string> verificationCommands = new List<string>();
            public List<string> restartServices = new List<string>();
        }

        [Serializable]
        private sealed class ThreadRegistryRecord
        {
            public string projectKey = string.Empty;
            public string projectTitle = string.Empty;
            public string projectCategory = string.Empty;
            public string localThreadId = string.Empty;
            public string localCodexSessionId = string.Empty;
            public string status = string.Empty;
            public string bootstrapStatus = string.Empty;
            public string launcherCommand = string.Empty;
            public string launcherCwd = string.Empty;
            public string launcherNote = string.Empty;
            public string routeBindingProjectKey = string.Empty;
            public string routeBindingAgentId = string.Empty;
            public string routeBindingSessionId = string.Empty;
            public long lastOpenedEpoch;
            public long lastBootstrapEpoch;
        }

        [Serializable]
        private sealed class BridgeResponse
        {
            public string status = string.Empty;
            public string detail = string.Empty;
            public DariusMessageRecord message = new DariusMessageRecord();
            public DariusRouteRecord route = new DariusRouteRecord();
            public ProjectPackRecord projectPack = new ProjectPackRecord();
            public ThreadRegistryRecord thread = new ThreadRegistryRecord();
            public string queue_file = string.Empty;
            public string status_file = string.Empty;
        }

        [Serializable]
        private sealed class BridgeRouteResponse
        {
            public string status = string.Empty;
            public string detail = string.Empty;
            public DariusRouteRecord route = new DariusRouteRecord();
            public ProjectPackRecord projectPack = new ProjectPackRecord();
            public ThreadRegistryRecord thread = new ThreadRegistryRecord();
        }

        [Serializable]
        private sealed class ModelRequestPayload
        {
            public string displayName = string.Empty;
            public string assetKind = "props";
            public string requestMode = "image-to-3d";
            public string prompt = string.Empty;
            public string negativePrompt = string.Empty;
            public string texturePrompt = string.Empty;
            public string referenceImagePath = string.Empty;
            public string expectedAssetName = string.Empty;
        }

        [Serializable]
        private sealed class ImageRequestPayload
        {
            public string displayName = string.Empty;
            public string prompt = string.Empty;
            public string negativePrompt = string.Empty;
            public string aspectRatio = "16:9";
        }

        [Serializable]
        private sealed class DariusChatPayload
        {
            public string message = string.Empty;
            public List<string> attachments = new List<string>();
            public string requestedAction = "implement";
            public string source = "unity_darius";
            public bool expectVerification = true;
        }

        private static readonly string[] ModelAssetKinds = { "warble", "biggun", "props" };
        private static readonly string[] ModelRequestModes = { "image-to-3d", "text-to-3d" };
        private static readonly string[] ImageAspectRatios = { "1:1", "4:3", "16:9", "9:16" };

        private DariusTab _activeTab;
        private DariusProjectConfig _envConfig = DariusProjectConfig.CreateDefault();
        private DariusDatabase _database = new DariusDatabase();
        private Vector2 _modelsScroll;
        private Vector2 _imagesScroll;
        private Vector2 _storeScroll;
        private Vector2 _chatScroll;
        private string _lastCommandOutput = string.Empty;
        private readonly Dictionary<string, Texture2D> _externalTextureCache = new Dictionary<string, Texture2D>();
        private bool _previewRepaintQueued;

        private string _newModelDisplayName = "Underfoot Warble Variant";
        private int _newModelAssetKindIndex;
        private int _newModelRequestModeIndex;
        private string _newModelExpectedAssetName = string.Empty;
        private string _newModelPrompt = string.Empty;
        private string _newModelNegativePrompt = string.Empty;
        private string _newModelTexturePrompt = string.Empty;
        private string _newModelReferenceImagePath = string.Empty;

        private string _newImageDisplayName = "Underfoot UI Concept";
        private string _newImagePrompt = string.Empty;
        private string _newImageNegativePrompt = string.Empty;
        private int _newImageAspectRatioIndex = 2;

        private string _chatDraft = "Move the camera back and up for a better view.";
        private string _chatAttachmentsCsv = string.Empty;
        private string _chatRequestedAction = "implement";
        private bool _chatExpectVerification = true;

        [MenuItem("Codename Darius/Open")]
        public static void OpenWindow()
        {
            var window = GetWindow<UFDariusWindow>();
            window.titleContent = new GUIContent(WindowTitle);
            window.minSize = new Vector2(980f, 680f);
            window.Show();
        }

        private bool _allowBackendOperations;

        private void OnEnable()
        {
            _envConfig = LoadEnvConfig();
            RefreshDatabaseFromDisk();
        }

        private void OnInspectorUpdate()
        {
            if (_previewRepaintQueued)
            {
                _previewRepaintQueued = false;
                Repaint();
            }
        }

        private static DariusProjectConfig LoadEnvConfig()
        {
            var searchRoots = new[] { Path.GetDirectoryName(Application.dataPath) ?? string.Empty };
            return DariusProjectConfig.LoadFromSearchRoots(searchRoots);
        }

        private void OnGUI()
        {
            _allowBackendOperations = EditorGUILayout.Toggle("Enable tool actions this session", _allowBackendOperations);
            if (_allowBackendOperations && GUILayout.Button("Initialize project database"))
            {
                EnsureDatabaseExists();
                RefreshDatabaseFromDisk();
            }
            DrawToolbar();
            EditorGUILayout.Space(6f);

            switch (_activeTab)
            {
                case DariusTab.Models:
                    DrawModelsTab();
                    break;
                case DariusTab.Images:
                    DrawImagesTab();
                    break;
                case DariusTab.StoreAssets:
                    DrawStoreAssetsTab();
                    break;
                default:
                    DrawChatTab();
                    break;
            }

            EditorGUILayout.Space(10f);
            DrawCommandLog();
        }

        private void DrawToolbar()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Codename Darius", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Shared local-first Darius console for model work, project bootstrap, routing, and persistent local thread tracking.", EditorStyles.wordWrappedMiniLabel);
            EditorGUILayout.Space(4f);

            EditorGUILayout.BeginHorizontal();
            _activeTab = (DariusTab)GUILayout.Toolbar((int)_activeTab, new[] { "Models", "Images", "Store Assets", "Darius Chat" }, GUILayout.Height(26f));
            if (GUILayout.Button("Sync Darius Data", GUILayout.Width(128f)))
            {
                RunBackendCommand("sync", null);
            }
            if (GUILayout.Button("Refresh Route", GUILayout.Width(108f)))
            {
                RefreshRouteFromBridge();
            }
            if (GUILayout.Button("Reveal Database", GUILayout.Width(128f)))
            {
                RevealExternalPath(GetDatabaseAbsolutePath());
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.LabelField(
                string.Format(
                    "DB: {0} | Models: {1} | Images: {2} | Store Assets: {3} | Messages: {4} | Retry Limit: {5}",
                    _envConfig.DariusDatabaseAssetPath,
                    _database.modelJobs.Count,
                    _database.imageJobs.Count,
                    _database.storeAssets.Count,
                    _database.dariusMessages.Count,
                    Math.Max(1, _database.retryLimit)),
                EditorStyles.miniLabel);
            if (_database.route != null && !string.IsNullOrWhiteSpace(_database.route.projectKey))
            {
                EditorGUILayout.LabelField(
                    string.Format(
                        "Route: {0} | Local Thread: {1} | Session: {2} | Machine: {3} | Status: {4}",
                        SafeValue(!string.IsNullOrWhiteSpace(_database.route.projectTitle) ? _database.route.projectTitle : _database.route.projectKey),
                        SafeValue(_database.route.activeLocalThreadId),
                        SafeValue(_database.route.activeCodexSessionId),
                        SafeValue(_database.route.machineLabel),
                        SafeValue(_database.route.routeStatus)),
                    EditorStyles.miniLabel);
            }
            if (_database.projectMemory != null && !string.IsNullOrWhiteSpace(_database.projectMemory.gameTitle))
            {
                EditorGUILayout.LabelField(
                    string.Format(
                        "Memory: {0} | Updated By: {1} | Updated: {2}",
                        SafeValue(_database.projectMemory.gameTitle),
                        SafeValue(_database.projectMemory.updatedBy),
                        FormatEpoch(_database.projectMemory.updatedAtEpoch)),
                    EditorStyles.miniLabel);
            }
            EditorGUILayout.EndVertical();
        }

        private void DrawModelsTab()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("New Model Request", EditorStyles.boldLabel);
            _newModelDisplayName = EditorGUILayout.TextField("Display Name", _newModelDisplayName);
            _newModelAssetKindIndex = EditorGUILayout.Popup("Asset Kind", _newModelAssetKindIndex, ModelAssetKinds);
            _newModelRequestModeIndex = EditorGUILayout.Popup("Request Mode", _newModelRequestModeIndex, ModelRequestModes);
            _newModelExpectedAssetName = EditorGUILayout.TextField("Output Asset Name", _newModelExpectedAssetName);

            EditorGUILayout.BeginHorizontal();
            _newModelReferenceImagePath = EditorGUILayout.TextField("Reference Image", _newModelReferenceImagePath);
            if (GUILayout.Button("Browse", GUILayout.Width(80f)))
            {
                var imagePath = EditorUtility.OpenFilePanel("Choose Reference Image", ResolveRepoRoot(), "png,jpg,jpeg,webp");
                if (!string.IsNullOrWhiteSpace(imagePath))
                {
                    _newModelReferenceImagePath = imagePath;
                }
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.LabelField("Prompt");
            _newModelPrompt = EditorGUILayout.TextArea(_newModelPrompt, GUILayout.MinHeight(72f));
            EditorGUILayout.LabelField("Negative Prompt");
            _newModelNegativePrompt = EditorGUILayout.TextArea(_newModelNegativePrompt, GUILayout.MinHeight(48f));
            EditorGUILayout.LabelField("Texture Prompt");
            _newModelTexturePrompt = EditorGUILayout.TextArea(_newModelTexturePrompt, GUILayout.MinHeight(48f));

            var requestMode = ModelRequestModes[Mathf.Clamp(_newModelRequestModeIndex, 0, ModelRequestModes.Length - 1)];
            if (requestMode == "image-to-3d" && string.IsNullOrWhiteSpace(_newModelReferenceImagePath))
            {
                EditorGUILayout.HelpBox("Image-to-3d requests need a reference image path. Prompt-only requests use text-to-3d.", MessageType.Warning);
            }
            EditorGUILayout.HelpBox(BuildModelPromptPreview(), MessageType.None);

            if (GUILayout.Button("Create Model Job", GUILayout.Height(28f)))
            {
                CreateModelJob();
            }
            EditorGUILayout.EndVertical();

            EditorGUILayout.Space(8f);
            EditorGUILayout.LabelField("Model Catalogue", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Darius prefers Unityâ€™s imported asset previews, so the catalogue matches the project-window mesh previews whenever possible.", EditorStyles.wordWrappedMiniLabel);
            _modelsScroll = EditorGUILayout.BeginScrollView(_modelsScroll);
            foreach (var job in _database.modelJobs)
            {
                DrawModelJob(job);
            }
            EditorGUILayout.EndScrollView();
        }

        private void DrawImagesTab()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("New Image Request", EditorStyles.boldLabel);
            _newImageDisplayName = EditorGUILayout.TextField("Display Name", _newImageDisplayName);
            _newImageAspectRatioIndex = EditorGUILayout.Popup("Aspect Ratio", _newImageAspectRatioIndex, ImageAspectRatios);
            EditorGUILayout.LabelField("Prompt");
            _newImagePrompt = EditorGUILayout.TextArea(_newImagePrompt, GUILayout.MinHeight(80f));
            EditorGUILayout.LabelField("Negative Prompt");
            _newImageNegativePrompt = EditorGUILayout.TextArea(_newImageNegativePrompt, GUILayout.MinHeight(48f));
            EditorGUILayout.HelpBox(BuildImagePromptPreview(), MessageType.None);

            if (GUILayout.Button("Create Image Job", GUILayout.Height(28f)))
            {
                CreateImageJob();
            }
            EditorGUILayout.EndVertical();

            EditorGUILayout.Space(8f);
            EditorGUILayout.LabelField("Tracked Image Jobs", EditorStyles.boldLabel);
            _imagesScroll = EditorGUILayout.BeginScrollView(_imagesScroll);
            foreach (var job in _database.imageJobs)
            {
                DrawImageJob(job);
            }
            EditorGUILayout.EndScrollView();
        }

        private void DrawStoreAssetsTab()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Local Asset Store Intake", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                string.Format(
                    "Darius can scan already-downloaded Unity packages, import them into Underfoot for modification, and track local ownership hints. Unity still owns the authenticated download step. Local ownership-cache hints found: {0}.",
                    _database.ownedAssetHints.Count),
                MessageType.Info);

            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Scan Local Downloads", GUILayout.Width(160f)))
            {
                RunBackendCommand("scan-store", null);
            }
            if (GUILayout.Button("Open Package Manager", GUILayout.Width(160f)))
            {
                EditorApplication.ExecuteMenuItem("Window/Package Manager");
            }
            if (GUILayout.Button("Reveal Cache Root", GUILayout.Width(140f)))
            {
                RevealStoreCacheRoot();
            }
            if (GUILayout.Button("Reveal Ownership Cache", GUILayout.Width(160f)))
            {
                RevealExternalPath(GetOwnershipCacheAbsolutePath());
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();

            if (_database.ownedAssetHints.Count > 0)
            {
                EditorGUILayout.BeginVertical(EditorStyles.helpBox);
                EditorGUILayout.LabelField("Owned Asset Cache Hints", EditorStyles.boldLabel);
                EditorGUILayout.LabelField(
                    "These are Unity's locally cached ownership markers. They help confirm owned asset ids, but package downloading still needs Unity's Package Manager / My Assets flow.",
                    EditorStyles.wordWrappedMiniLabel);
                for (var index = 0; index < Mathf.Min(24, _database.ownedAssetHints.Count); index++)
                {
                    var hint = _database.ownedAssetHints[index];
                    EditorGUILayout.LabelField(
                        string.Format("Asset Version {0}{1}", hint.assetVersionId, hint.isAssetProject ? " (project asset)" : string.Empty),
                        EditorStyles.miniLabel);
                }
                EditorGUILayout.EndVertical();
            }

            _storeScroll = EditorGUILayout.BeginScrollView(_storeScroll);
            foreach (var asset in _database.storeAssets)
            {
                DrawStoreAsset(asset);
            }
            EditorGUILayout.EndScrollView();
        }

        private void DrawChatTab()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Darius Chat", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                HasBridgeConfiguration()
                    ? "Darius keeps this project local-first. Hermes routes work through Mount Olympus, but the assigned project machine owns the persistent local thread, code work, and verification."
                    : "Bridge auth is not configured yet. Darius can still queue local requests and sync catalogue data, but direct route/session dispatch is unavailable.",
                HasBridgeConfiguration() ? MessageType.Info : MessageType.Warning);
            if (_database.projectMemory != null && !string.IsNullOrWhiteSpace(_database.projectMemory.conceptSummary))
            {
                EditorGUILayout.HelpBox(
                    string.Format(
                        "{0}: {1}",
                        SafeValue(_database.projectMemory.gameTitle),
                        _database.projectMemory.conceptSummary),
                    MessageType.None);
            }
            DrawRouteSummary();

            _chatScroll = EditorGUILayout.BeginScrollView(_chatScroll, GUILayout.MinHeight(280f));
            if (_database.dariusMessages.Count == 0)
            {
                EditorGUILayout.HelpBox("No Darius chat history is stored yet. Sync or send a message to start the project timeline.", MessageType.None);
            }
            foreach (var message in _database.dariusMessages)
            {
                DrawDariusMessage(message);
            }
            EditorGUILayout.EndScrollView();

            EditorGUILayout.LabelField("Message");
            _chatDraft = EditorGUILayout.TextArea(_chatDraft, GUILayout.MinHeight(88f));
            _chatAttachmentsCsv = EditorGUILayout.TextField("Attachments (csv)", _chatAttachmentsCsv);
            _chatRequestedAction = EditorGUILayout.TextField("Requested Action", _chatRequestedAction);
            _chatExpectVerification = EditorGUILayout.Toggle("Expect Verification", _chatExpectVerification);

            EditorGUILayout.BeginHorizontal();
            EditorGUI.BeginDisabledGroup(!HasBridgeConfiguration());
            if (GUILayout.Button("Send To Darius", GUILayout.Width(140f)))
            {
                SendDariusMessageToBridge();
            }
            EditorGUI.EndDisabledGroup();
            EditorGUI.BeginDisabledGroup(!HasBridgeConfiguration());
            if (GUILayout.Button("Open / Bootstrap Project", GUILayout.Width(160f)))
            {
                SendProjectOpenToBridge();
            }
            EditorGUI.EndDisabledGroup();
            if (GUILayout.Button("Queue Local", GUILayout.Width(120f)))
            {
                QueueLocalDariusMessage();
            }
            if (GUILayout.Button("Sync Status", GUILayout.Width(120f)))
            {
                RunBackendCommand("sync", null);
                RefreshRouteFromBridge();
            }
            if (GUILayout.Button("Reveal Queue", GUILayout.Width(120f)))
            {
                RevealExternalPath(GetQueueAbsolutePath());
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();
        }

        private void DrawModelJob(ModelJobRecord job)
        {
            EditorGUILayout.BeginHorizontal(EditorStyles.helpBox);
            DrawModelPreview(job);

            EditorGUILayout.BeginVertical();
            EditorGUILayout.LabelField(string.Format("{0} [{1}]", job.displayName, job.assetKind), EditorStyles.boldLabel);
            EditorGUILayout.LabelField(string.Format("Status: {0} | Task: {1} | Retry: {2}/{3}", job.status, job.taskId, job.retryCount, Math.Max(1, _database.retryLimit)), EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(job.lastError))
            {
                EditorGUILayout.HelpBox(job.lastError, MessageType.Warning);
            }
            if (!string.IsNullOrWhiteSpace(job.prompt))
            {
                EditorGUILayout.LabelField(job.prompt, EditorStyles.wordWrappedMiniLabel);
            }

            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Refresh Status", GUILayout.Width(110f)))
            {
                RunBackendCommand("refresh-model", null, "--job-id", job.id);
            }
            EditorGUI.BeginDisabledGroup(job.retryCount >= Math.Max(1, _database.retryLimit));
            if (GUILayout.Button("Retry", GUILayout.Width(80f)))
            {
                RunBackendCommand("retry-model", null, "--job-id", job.id);
            }
            EditorGUI.EndDisabledGroup();
            if (GUILayout.Button("Reveal Output", GUILayout.Width(110f)))
            {
                RevealJobOutput(job.downloadedFiles, job.outputFolder);
            }
            if (GUILayout.Button("Ping Imported", GUILayout.Width(110f)))
            {
                PingDownloadedAsset(job.downloadedFiles);
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.BeginHorizontal();
            if (!string.IsNullOrWhiteSpace(job.referenceImagePath) && GUILayout.Button("Reveal Reference", GUILayout.Width(130f)))
            {
                RevealExternalPath(job.referenceImagePath);
            }
            if (!string.IsNullOrWhiteSpace(job.thumbnailPath) && GUILayout.Button("Reveal Thumbnail", GUILayout.Width(130f)))
            {
                RevealExternalPath(job.thumbnailPath);
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();
            EditorGUILayout.EndHorizontal();
        }

        private void DrawImageJob(ImageJobRecord job)
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField(job.displayName, EditorStyles.boldLabel);
            EditorGUILayout.LabelField(string.Format("Status: {0} | Task: {1} | Aspect: {2}", job.status, job.taskId, job.aspectRatio), EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(job.prompt))
            {
                EditorGUILayout.LabelField(job.prompt, EditorStyles.wordWrappedMiniLabel);
            }
            if (!string.IsNullOrWhiteSpace(job.lastError))
            {
                EditorGUILayout.HelpBox(job.lastError, MessageType.Warning);
            }
            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Refresh Status", GUILayout.Width(110f)))
            {
                RunBackendCommand("refresh-image", null, "--job-id", job.id);
            }
            if (GUILayout.Button("Reveal Output", GUILayout.Width(110f)))
            {
                RevealJobOutput(job.downloadedFiles, job.outputFolder);
            }
            if (GUILayout.Button("Ping Imported", GUILayout.Width(110f)))
            {
                PingDownloadedAsset(job.downloadedFiles);
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();
        }

        private void DrawStoreAsset(StoreAssetRecord asset)
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField(asset.displayName, EditorStyles.boldLabel);
            EditorGUILayout.LabelField(string.Format("Publisher: {0} | Size: {1:0.0} MB", asset.publisher, asset.fileSizeBytes / (1024f * 1024f)), EditorStyles.miniLabel);
            EditorGUILayout.LabelField(asset.localPackagePath, EditorStyles.wordWrappedMiniLabel);
            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Reveal Package", GUILayout.Width(120f)))
            {
                RevealExternalPath(asset.localPackagePath);
            }
            if (GUILayout.Button("Import Package", GUILayout.Width(120f)))
            {
                AssetDatabase.ImportPackage(asset.localPackagePath, true);
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();
        }

        private void DrawRouteSummary()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            var route = _database.route ?? new DariusRouteRecord();
            var pack = _database.projectPack ?? new ProjectPackRecord();
            var thread = _database.thread ?? new ThreadRegistryRecord();
            EditorGUILayout.LabelField("Assigned Route", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Project", SafeValue(!string.IsNullOrWhiteSpace(route.projectTitle) ? route.projectTitle : route.projectKey), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Category", SafeValue(!string.IsNullOrWhiteSpace(route.projectCategory) ? route.projectCategory : pack.projectCategory), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Agent", SafeValue(route.agentId), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Machine", SafeValue(route.machineLabel), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Session", SafeValue(route.activeCodexSessionId), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Local Thread", SafeValue(!string.IsNullOrWhiteSpace(route.activeLocalThreadId) ? route.activeLocalThreadId : thread.localThreadId), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Status", SafeValue(route.routeStatus), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Bootstrap", SafeValue(!string.IsNullOrWhiteSpace(route.bootstrapStatus) ? route.bootstrapStatus : pack.bootstrapStatus), EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(route.detail))
            {
                EditorGUILayout.HelpBox(route.detail, MessageType.None);
            }
            EditorGUILayout.Space(4f);
            EditorGUILayout.LabelField("Project Pack", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Workflow Tasks", pack.workflowTaskCount.ToString(), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Last Bootstrap", FormatEpoch(Math.Max(pack.lastBootstrapEpoch, route.lastBootstrapEpoch)), EditorStyles.miniLabel);
            DrawRevealLine("Thread Registry", pack.threadRegistryPath);
            DrawRevealLine("Workflow", pack.workflowPath);
            DrawRevealLine("Repo Rules", pack.repoRulesPath);
            DrawRevealLine("Server Refs", pack.serverRefsPath);
            if (pack.requiredContextFiles != null && pack.requiredContextFiles.Count > 0)
            {
                EditorGUILayout.LabelField("Required Context", EditorStyles.boldLabel);
                foreach (var contextPath in pack.requiredContextFiles)
                {
                    EditorGUILayout.LabelField(contextPath, EditorStyles.wordWrappedMiniLabel);
                }
            }
            if (pack.verificationCommands != null && pack.verificationCommands.Count > 0)
            {
                EditorGUILayout.LabelField("Verification Commands", EditorStyles.boldLabel);
                foreach (var command in pack.verificationCommands)
                {
                    EditorGUILayout.LabelField(command, EditorStyles.wordWrappedMiniLabel);
                }
            }
            if (pack.restartServices != null && pack.restartServices.Count > 0)
            {
                EditorGUILayout.LabelField("Restart Services", string.Join(", ", pack.restartServices.ToArray()), EditorStyles.wordWrappedMiniLabel);
            }
            EditorGUILayout.Space(4f);
            EditorGUILayout.LabelField("Local Thread Binding", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Thread Id", SafeValue(thread.localThreadId), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Bound Codex Session", SafeValue(thread.localCodexSessionId), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Thread Status", SafeValue(thread.status), EditorStyles.miniLabel);
            EditorGUILayout.LabelField("Last Opened", FormatEpoch(thread.lastOpenedEpoch), EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(thread.launcherNote))
            {
                EditorGUILayout.HelpBox(thread.launcherNote, MessageType.None);
            }
            EditorGUILayout.EndVertical();
        }

        private void DrawRevealLine(string label, string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            EditorGUILayout.BeginHorizontal();
            EditorGUILayout.LabelField(label, path, EditorStyles.wordWrappedMiniLabel);
            if (GUILayout.Button("Reveal", GUILayout.Width(60f)))
            {
                RevealExternalPath(path);
            }
            EditorGUILayout.EndHorizontal();
        }

        private void DrawDariusMessage(DariusMessageRecord message)
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField(string.Format("{0} â€¢ {1}", SafeValue(message.role), SafeValue(message.status)), EditorStyles.boldLabel);
            EditorGUILayout.LabelField(
                string.Format(
                    "{0} | session {1} | machine {2}",
                    EpochToDisplay(message.createdAtEpoch),
                    SafeValue(message.activeSessionId),
                    SafeValue(message.machineLabel)),
                EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(message.text))
            {
                EditorGUILayout.LabelField(message.text, EditorStyles.wordWrappedMiniLabel);
            }
            if (!string.IsNullOrWhiteSpace(message.verificationSummary))
            {
                EditorGUILayout.HelpBox(message.verificationSummary, MessageType.Info);
            }
            if (!string.IsNullOrWhiteSpace(message.error))
            {
                EditorGUILayout.HelpBox(message.error, MessageType.Error);
            }
            DrawPathList("Attachments", message.attachments);
            DrawPathList("Artifacts", message.artifactPaths);
            EditorGUILayout.EndVertical();
        }

        private void DrawCommandLog()
        {
            if (string.IsNullOrWhiteSpace(_lastCommandOutput))
            {
                return;
            }

            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Last Backend Output", EditorStyles.boldLabel);
            EditorGUILayout.TextArea(_lastCommandOutput, GUILayout.MinHeight(92f));
            EditorGUILayout.EndVertical();
        }

        private void DrawModelPreview(ModelJobRecord job)
        {
            var texture = GetModelPreviewTexture(job);
            var rect = GUILayoutUtility.GetRect(96f, 96f, GUILayout.Width(96f), GUILayout.Height(96f));
            if (texture != null)
            {
                GUI.DrawTexture(rect, texture, ScaleMode.ScaleToFit);
                return;
            }

            EditorGUI.HelpBox(rect, "No Preview", MessageType.None);
        }

        private void CreateModelJob()
        {
            var payload = new ModelRequestPayload
            {
                displayName = _newModelDisplayName,
                assetKind = ModelAssetKinds[Mathf.Clamp(_newModelAssetKindIndex, 0, ModelAssetKinds.Length - 1)],
                requestMode = ModelRequestModes[Mathf.Clamp(_newModelRequestModeIndex, 0, ModelRequestModes.Length - 1)],
                prompt = _newModelPrompt,
                negativePrompt = _newModelNegativePrompt,
                texturePrompt = _newModelTexturePrompt,
                referenceImagePath = _newModelReferenceImagePath,
                expectedAssetName = _newModelExpectedAssetName,
            };
            RunBackendCommand("create-model", JsonUtility.ToJson(payload, true));
        }

        private void CreateImageJob()
        {
            var payload = new ImageRequestPayload
            {
                displayName = _newImageDisplayName,
                prompt = _newImagePrompt,
                negativePrompt = _newImageNegativePrompt,
                aspectRatio = ImageAspectRatios[Mathf.Clamp(_newImageAspectRatioIndex, 0, ImageAspectRatios.Length - 1)],
            };
            RunBackendCommand("create-image", JsonUtility.ToJson(payload, true));
        }

        private void QueueLocalDariusMessage()
        {
            var payload = new DariusChatPayload
            {
                message = _chatDraft,
                attachments = SplitCsv(_chatAttachmentsCsv),
                requestedAction = _chatRequestedAction,
                expectVerification = _chatExpectVerification,
            };
            RunBackendCommand("queue-darius", JsonUtility.ToJson(payload, true));
        }

        private void InsertOrReplaceMessage(DariusMessageRecord message)
        {
            if (message == null || string.IsNullOrWhiteSpace(message.id))
            {
                return;
            }

            _database.dariusMessages.RemoveAll(item => item != null && item.id == message.id);
            _database.dariusMessages.Insert(0, message);
        }

        private void EnsureDatabaseExists()
        {
            var path = GetDatabaseAbsolutePath();
            if (File.Exists(path))
            {
                return;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ResolveUnityProjectRoot());
            var legacyPath = GetLegacyDatabaseAbsolutePath();
            if (File.Exists(legacyPath))
            {
                File.Copy(legacyPath, path, true);
            }
            else
            {
                File.WriteAllText(path, JsonUtility.ToJson(new DariusDatabase(), true), Encoding.UTF8);
            }
            AssetDatabase.Refresh();
        }

        private void RefreshDatabaseFromDisk()
        {
            var path = GetDatabaseAbsolutePath();
            if (!File.Exists(path))
            {
                _database = new DariusDatabase();
                return;
            }

            var json = File.ReadAllText(path, Encoding.UTF8);
            json = json.Replace("\"luminaxMessages\":", "\"relayMessages\":");
            _database = JsonUtility.FromJson<DariusDatabase>(json) ?? new DariusDatabase();
            _database.modelJobs = _database.modelJobs ?? new List<ModelJobRecord>();
            _database.imageJobs = _database.imageJobs ?? new List<ImageJobRecord>();
            _database.storeAssets = _database.storeAssets ?? new List<StoreAssetRecord>();
            _database.ownedAssetHints = _database.ownedAssetHints ?? new List<OwnedAssetHintRecord>();
            _database.dariusMessages = _database.dariusMessages ?? new List<DariusMessageRecord>();
            _database.route = _database.route ?? new DariusRouteRecord();
            _database.relayMessages = _database.relayMessages ?? new List<RelayMessageRecord>();
            _database.projectMemory = _database.projectMemory ?? new ProjectMemoryRecord();
            _database.projectPack = _database.projectPack ?? new ProjectPackRecord();
            _database.thread = _database.thread ?? new ThreadRegistryRecord();
        }

        private void SaveDatabase()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(GetDatabaseAbsolutePath()) ?? ResolveUnityProjectRoot());
            _database.updatedAtEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            File.WriteAllText(GetDatabaseAbsolutePath(), JsonUtility.ToJson(_database, true), Encoding.UTF8);
            AssetDatabase.Refresh();
        }

        private void RunBackendCommand(string command, string? payloadJson, params string[] extraArguments)
        {
            var backendPath = Path.Combine(ResolveRepoRoot(), BackendScriptRelativePath);
            if (!File.Exists(backendPath))
            {
                _lastCommandOutput = string.Format("Darius backend was not found at {0}", backendPath);
                return;
            }

            string? payloadPath = null;
            try
            {
                if (!string.IsNullOrWhiteSpace(payloadJson))
                {
                    payloadPath = Path.GetTempFileName();
                    File.WriteAllText(payloadPath, payloadJson, Encoding.UTF8);
                }

                var builder = new StringBuilder();
                builder.Append('"').Append(backendPath).Append("\" ");
                if (_allowBackendOperations) builder.Append("--apply ");
                builder.Append(command);
                if (!string.IsNullOrWhiteSpace(payloadPath))
                {
                    builder.Append(" --payload-file \"").Append(payloadPath).Append('"');
                }
                for (var index = 0; index < extraArguments.Length; index++)
                {
                    builder.Append(' ').Append(extraArguments[index].Contains(" ") ? string.Format("\"{0}\"", extraArguments[index]) : extraArguments[index]);
                }

                var startInfo = new ProcessStartInfo
                {
                    FileName = "python",
                    Arguments = builder.ToString(),
                    WorkingDirectory = ResolveRepoRoot(),
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };

                using (var process = Process.Start(startInfo))
                {
                    if (process == null)
                    {
                        _lastCommandOutput = "Darius could not start the Python backend.";
                        return;
                    }

                    var stdout = process.StandardOutput.ReadToEnd();
                    var stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    _lastCommandOutput = string.IsNullOrWhiteSpace(stderr) ? stdout : stdout + Environment.NewLine + stderr;
                }
            }
            catch (Exception error)
            {
                _lastCommandOutput = error.ToString();
            }
            finally
            {
                if (!string.IsNullOrWhiteSpace(payloadPath) && File.Exists(payloadPath))
                {
                    File.Delete(payloadPath);
                }
            }

            AssetDatabase.Refresh();
            RefreshDatabaseFromDisk();
            Repaint();
        }

        private string BuildModelPromptPreview()
        {
            var builder = new StringBuilder();
            builder.Append("Mode: ").Append(ModelRequestModes[Mathf.Clamp(_newModelRequestModeIndex, 0, ModelRequestModes.Length - 1)]);
            builder.Append(" | Kind: ").Append(ModelAssetKinds[Mathf.Clamp(_newModelAssetKindIndex, 0, ModelAssetKinds.Length - 1)]);
            builder.AppendLine();
            builder.Append("Name: ").Append(_newModelDisplayName);
            builder.Append(" | Asset: ").Append(string.IsNullOrWhiteSpace(_newModelExpectedAssetName) ? SlugifyForDisplay(_newModelDisplayName) : _newModelExpectedAssetName);
            builder.AppendLine().Append("Darius review: Unity preview first, Meshy thumbnail fallback.");
            if (!string.IsNullOrWhiteSpace(_newModelReferenceImagePath))
            {
                builder.AppendLine();
                builder.Append("Reference: ").Append(_newModelReferenceImagePath);
            }
            if (!string.IsNullOrWhiteSpace(_newModelPrompt))
            {
                builder.AppendLine().AppendLine().Append("Prompt: ").Append(_newModelPrompt);
            }
            if (!string.IsNullOrWhiteSpace(_newModelNegativePrompt))
            {
                builder.AppendLine().Append("Negative: ").Append(_newModelNegativePrompt);
            }
            if (!string.IsNullOrWhiteSpace(_newModelTexturePrompt))
            {
                builder.AppendLine().Append("Texture: ").Append(_newModelTexturePrompt);
            }
            return builder.ToString();
        }

        private string BuildImagePromptPreview()
        {
            return string.Format(
                "Darius image review\nName: {0}\nAspect: {1}\nPrompt: {2}\nNegative: {3}",
                _newImageDisplayName,
                ImageAspectRatios[Mathf.Clamp(_newImageAspectRatioIndex, 0, ImageAspectRatios.Length - 1)],
                _newImagePrompt,
                _newImageNegativePrompt);
        }

        private string GetDatabaseAbsolutePath()
        {
            return ResolveConfiguredPath(_envConfig.DariusDatabaseAssetPath, ResolveUnityProjectRoot());
        }

        private string GetLegacyDatabaseAbsolutePath()
        {
            return ResolveConfiguredPath(_envConfig.DariusLegacyDatabaseAssetPath, ResolveUnityProjectRoot());
        }

        private string GetQueueAbsolutePath()
        {
            return ResolveConfiguredPath(_envConfig.DariusQueueFilePath, ResolveRepoRoot());
        }

        private string GetOwnershipCacheAbsolutePath()
        {
            if (!string.IsNullOrWhiteSpace(_envConfig.UnityAssetStoreOwnershipCachePath))
            {
                return ResolveConfiguredPath(_envConfig.UnityAssetStoreOwnershipCachePath, ResolveRepoRoot());
            }

            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            return Path.Combine(appData, "Unity", "Asset Store", "Cache", "AssetStoreCache.json");
        }

        private static string ResolveConfiguredPath(string configured, string projectRoot)
        {
            if (Path.IsPathRooted(configured))
            {
                return configured;
            }

            return Path.GetFullPath(Path.Combine(projectRoot, configured));
        }

        private static string ResolveUnityProjectRoot()
        {
            return Path.GetDirectoryName(Application.dataPath) ?? Directory.GetCurrentDirectory();
        }

        private static string ResolveRepoRoot()
        {
            var unityProjectRoot = ResolveUnityProjectRoot();
            return Path.GetFullPath(Path.Combine(unityProjectRoot, "..", ".."));
        }

        private void RevealJobOutput(List<string> downloadedFiles, string outputFolder)
        {
            if (downloadedFiles != null && downloadedFiles.Count > 0)
            {
                RevealExternalPath(downloadedFiles[0]);
                return;
            }

            if (!string.IsNullOrWhiteSpace(outputFolder))
            {
                RevealExternalPath(ResolveConfiguredPath(outputFolder, ResolveUnityProjectRoot()));
            }
        }

        private void PingDownloadedAsset(List<string> downloadedFiles)
        {
            var asset = FindImportedAsset(downloadedFiles);
            if (asset != null)
            {
                Selection.activeObject = asset;
                EditorGUIUtility.PingObject(asset);
            }
        }

        private UnityEngine.Object? FindImportedAsset(List<string> downloadedFiles)
        {
            if (downloadedFiles == null || downloadedFiles.Count == 0)
            {
                return null;
            }

            foreach (var filePath in downloadedFiles)
            {
                if (string.IsNullOrWhiteSpace(filePath))
                {
                    continue;
                }

                var assetPath = ToUnityAssetPath(filePath);
                if (string.IsNullOrWhiteSpace(assetPath))
                {
                    continue;
                }

                var asset = AssetDatabase.LoadMainAssetAtPath(assetPath);
                if (asset != null)
                {
                    return asset;
                }
            }

            return null;
        }

        private Texture? GetModelPreviewTexture(ModelJobRecord job)
        {
            var asset = FindImportedAsset(job.downloadedFiles);
            if (asset != null)
            {
                var preview = AssetPreview.GetAssetPreview(asset);
                if (preview != null)
                {
                    return preview;
                }

                if (AssetPreview.IsLoadingAssetPreview(asset.GetInstanceID()))
                {
                    _previewRepaintQueued = true;
                }

                var mini = AssetPreview.GetMiniThumbnail(asset);
                if (mini != null)
                {
                    return mini;
                }
            }

            if (!string.IsNullOrWhiteSpace(job.thumbnailPath))
            {
                var thumbnail = LoadExternalTexture(job.thumbnailPath);
                if (thumbnail != null)
                {
                    return thumbnail;
                }
            }

            return null;
        }

        private Texture2D? LoadExternalTexture(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return null;
            }

            var fullPath = Path.GetFullPath(path);
            if (_externalTextureCache.TryGetValue(fullPath, out var cached) && cached != null)
            {
                return cached;
            }

            if (!File.Exists(fullPath))
            {
                return null;
            }

            try
            {
                var bytes = File.ReadAllBytes(fullPath);
                var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false)
                {
                    name = Path.GetFileNameWithoutExtension(fullPath),
                    hideFlags = HideFlags.HideAndDontSave,
                };
                texture.LoadImage(bytes, true);
                _externalTextureCache[fullPath] = texture;
                return texture;
            }
            catch
            {
                return null;
            }
        }

        private static string ToUnityAssetPath(string filePath)
        {
            var fullPath = Path.GetFullPath(filePath);
            var assetsRoot = Application.dataPath;
            if (!fullPath.StartsWith(assetsRoot, StringComparison.OrdinalIgnoreCase))
            {
                return string.Empty;
            }

            var suffix = fullPath.Substring(assetsRoot.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return "Assets/" + suffix.Replace(Path.DirectorySeparatorChar, '/');
        }

        private void RevealStoreCacheRoot()
        {
            if (!string.IsNullOrWhiteSpace(_envConfig.UnityAssetStoreCachePath))
            {
                RevealExternalPath(_envConfig.UnityAssetStoreCachePath);
                return;
            }

            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            if (!string.IsNullOrWhiteSpace(appData))
            {
                RevealExternalPath(Path.Combine(appData, "Unity", "Asset Store-5.x"));
            }
        }

        private bool HasBridgeConfiguration()
        {
            return !string.IsNullOrWhiteSpace(_envConfig.LocalBridgeToken) &&
                   !string.IsNullOrWhiteSpace(_envConfig.LocalOperatorId) &&
                   !string.IsNullOrWhiteSpace(_envConfig.LocalBridgeHost) &&
                   ResolveAgentPort() > 0;
        }

        private void RefreshRouteFromBridge()
        {
            try
            {
                var url = string.Format(
                    "http://{0}:{1}/api/bridge/darius/route",
                    _envConfig.LocalBridgeHost,
                    ResolveAgentPort());
                var response = SendBridgeGet(url);
                var parsed = JsonUtility.FromJson<BridgeRouteResponse>(response);
                if (parsed != null && parsed.route != null)
                {
                    _database.route = parsed.route;
                }
                if (parsed != null && parsed.projectPack != null)
                {
                    _database.projectPack = parsed.projectPack;
                }
                if (parsed != null && parsed.thread != null)
                {
                    _database.thread = parsed.thread;
                }
                SaveDatabase();
                _lastCommandOutput = response;
                Repaint();
            }
            catch (Exception error)
            {
                _lastCommandOutput = error.ToString();
                Repaint();
            }
        }

        private void SendDariusMessageToBridge()
        {
            var payload = new DariusChatPayload
            {
                message = _chatDraft,
                attachments = SplitCsv(_chatAttachmentsCsv),
                requestedAction = _chatRequestedAction,
                expectVerification = _chatExpectVerification,
            };

            try
            {
                var url = string.Format(
                    "http://{0}:{1}/api/bridge/darius/message",
                    _envConfig.LocalBridgeHost,
                    ResolveAgentPort());
                var response = SendBridgePost(url, JsonUtility.ToJson(payload, true));
                var parsed = JsonUtility.FromJson<BridgeResponse>(response);
                if (parsed != null && parsed.message != null && !string.IsNullOrWhiteSpace(parsed.message.id))
                {
                    InsertOrReplaceMessage(parsed.message);
                }
                if (parsed != null && parsed.route != null)
                {
                    _database.route = parsed.route;
                }
                if (parsed != null && parsed.projectPack != null)
                {
                    _database.projectPack = parsed.projectPack;
                }
                if (parsed != null && parsed.thread != null)
                {
                    _database.thread = parsed.thread;
                }
                SaveDatabase();
                _lastCommandOutput = response;
                _chatDraft = string.Empty;
                Repaint();
            }
            catch (Exception error)
            {
                _lastCommandOutput = error.ToString();
                Repaint();
            }
        }

        private void SendProjectOpenToBridge()
        {
            try
            {
                var url = string.Format(
                    "http://{0}:{1}/api/bridge/darius/project/open",
                    _envConfig.LocalBridgeHost,
                    ResolveAgentPort());
                var response = SendBridgePost(url, "{\"forceRefresh\":true,\"note\":\"Opened from Codename Darius UI.\"}");
                var parsed = JsonUtility.FromJson<BridgeRouteResponse>(response);
                if (parsed != null && parsed.route != null)
                {
                    _database.route = parsed.route;
                }
                if (parsed != null && parsed.projectPack != null)
                {
                    _database.projectPack = parsed.projectPack;
                }
                if (parsed != null && parsed.thread != null)
                {
                    _database.thread = parsed.thread;
                }
                SaveDatabase();
                _lastCommandOutput = response;
                Repaint();
            }
            catch (Exception error)
            {
                _lastCommandOutput = error.ToString();
                Repaint();
            }
        }

        private string SendBridgePost(string url, string body)
        {
            if (!_allowBackendOperations) throw new InvalidOperationException("Enable tool actions for this session before connecting.");
            var target = new Uri(url);
            if (target.Scheme != Uri.UriSchemeHttps && !(target.Scheme == Uri.UriSchemeHttp && target.IsLoopback))
                throw new InvalidOperationException("Bridge requests require HTTPS or local loopback HTTP.");
            var request = (HttpWebRequest)WebRequest.Create(target);
            request.AllowAutoRedirect = false;
            request.Timeout = 15000;
            request.ReadWriteTimeout = 15000;
            request.Method = "POST";
            request.ContentType = "application/json";
            request.Headers["X-Underfoot-Bridge-Token"] = _envConfig.LocalBridgeToken;
            request.Headers["X-Darius-Bridge-Token"] = _envConfig.LocalBridgeToken;
            request.Headers["X-Discord-User-Id"] = _envConfig.LocalOperatorId;
            request.Headers["X-Darius-User-Id"] = _envConfig.LocalOperatorId;

            using (var stream = request.GetRequestStream())
            using (var writer = new StreamWriter(stream, Encoding.UTF8))
            {
                writer.Write(body);
            }

            using (var response = (HttpWebResponse)request.GetResponse())
            using (var stream = response.GetResponseStream())
            using (var reader = new StreamReader(stream ?? Stream.Null, Encoding.UTF8))
            {
                return reader.ReadToEnd();
            }
        }

        private string SendBridgeGet(string url)
        {
            if (!_allowBackendOperations) throw new InvalidOperationException("Enable tool actions for this session before connecting.");
            var target = new Uri(url);
            if (target.Scheme != Uri.UriSchemeHttps && !(target.Scheme == Uri.UriSchemeHttp && target.IsLoopback))
                throw new InvalidOperationException("Bridge requests require HTTPS or local loopback HTTP.");
            var request = (HttpWebRequest)WebRequest.Create(target);
            request.AllowAutoRedirect = false;
            request.Timeout = 15000;
            request.ReadWriteTimeout = 15000;
            request.Method = "GET";
            request.Headers["X-Underfoot-Bridge-Token"] = _envConfig.LocalBridgeToken;
            request.Headers["X-Darius-Bridge-Token"] = _envConfig.LocalBridgeToken;
            request.Headers["X-Discord-User-Id"] = _envConfig.LocalOperatorId;
            request.Headers["X-Darius-User-Id"] = _envConfig.LocalOperatorId;

            using (var response = (HttpWebResponse)request.GetResponse())
            using (var stream = response.GetResponseStream())
            using (var reader = new StreamReader(stream ?? Stream.Null, Encoding.UTF8))
            {
                return reader.ReadToEnd();
            }
        }

        private int ResolveAgentPort()
        {
            return _envConfig.DariusLocalAgentPort > 0 ? _envConfig.DariusLocalAgentPort : _envConfig.LocalBridgePort;
        }

        private static void RevealExternalPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            var fullPath = Path.GetFullPath(path);
            if (File.Exists(fullPath) || Directory.Exists(fullPath))
            {
                EditorUtility.RevealInFinder(fullPath);
            }
        }

        private static List<string> SplitCsv(string value)
        {
            var results = new List<string>();
            var split = value.Split(new[] { ',', ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries);
            for (var index = 0; index < split.Length; index++)
            {
                var item = split[index].Trim();
                if (!string.IsNullOrWhiteSpace(item))
                {
                    results.Add(item);
                }
            }
            return results;
        }

        private void DrawPathList(string label, List<string> paths)
        {
            if (paths == null || paths.Count == 0)
            {
                return;
            }

            EditorGUILayout.LabelField(label, EditorStyles.boldLabel);
            foreach (var path in paths)
            {
                EditorGUILayout.BeginHorizontal();
                EditorGUILayout.LabelField(path, EditorStyles.wordWrappedMiniLabel);
                if (GUILayout.Button("Reveal", GUILayout.Width(60f)))
                {
                    RevealExternalPath(path);
                }
                EditorGUILayout.EndHorizontal();
            }
        }

        private static string SafeValue(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? "(unassigned)" : value;
        }

        private static string SlugifyForDisplay(string value)
        {
            var builder = new StringBuilder();
            for (var index = 0; index < value.Length; index++)
            {
                var character = value[index];
                if (char.IsLetterOrDigit(character))
                {
                    builder.Append(character);
                }
                else if (builder.Length == 0 || builder[builder.Length - 1] != '_')
                {
                    builder.Append('_');
                }
            }
            return builder.ToString().Trim('_');
        }

        private static string EpochToDisplay(long epoch)
        {
            if (epoch <= 0)
            {
                return "unknown";
            }
            return DateTimeOffset.FromUnixTimeSeconds(epoch).ToLocalTime().ToString("g");
        }

        private static string FormatEpoch(long epoch)
        {
            return EpochToDisplay(epoch);
        }
    }
}
