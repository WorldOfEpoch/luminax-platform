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
    public sealed class UFEpochPortalWindow : EditorWindow
    {
        private const string WindowTitle = "Epoch Portal";
        private const string BackendScriptRelativePath = "tools/epoch_portal_cli.py";

        private enum EpochPortalTab
        {
            Models = 0,
            Images = 1,
            StoreAssets = 2,
            Luminax = 3,
        }

        [Serializable]
        private sealed class EpochPortalDatabase
        {
            public int version = 1;
            public long updatedAtEpoch;
            public int retryLimit = 8;
            public List<ModelJobRecord> modelJobs = new List<ModelJobRecord>();
            public List<ImageJobRecord> imageJobs = new List<ImageJobRecord>();
            public List<StoreAssetRecord> storeAssets = new List<StoreAssetRecord>();
            public List<OwnedAssetHintRecord> ownedAssetHints = new List<OwnedAssetHintRecord>();
            public List<LuminaxMessageRecord> luminaxMessages = new List<LuminaxMessageRecord>();
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
        private sealed class LuminaxMessageRecord
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
        private sealed class BridgeResponse
        {
            public string status = string.Empty;
            public BridgeMessageRecord message = new BridgeMessageRecord();
            public string queue_file = string.Empty;
        }

        [Serializable]
        private sealed class BridgeMessageRecord
        {
            public string id = string.Empty;
            public long created_at_epoch;
            public string subject = string.Empty;
            public string body = string.Empty;
            public List<string> file_paths = new List<string>();
            public string status = string.Empty;
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
        private sealed class LuminaxRequestPayload
        {
            public string subject = string.Empty;
            public string body = string.Empty;
            public List<string> filePaths = new List<string>();
        }

        private static readonly string[] ModelAssetKinds = { "warble", "biggun", "props" };
        private static readonly string[] ModelRequestModes = { "image-to-3d", "text-to-3d" };
        private static readonly string[] ImageAspectRatios = { "1:1", "4:3", "16:9", "9:16" };

        private EpochPortalTab _activeTab;
        private EpochPortalProjectConfig _envConfig = EpochPortalProjectConfig.CreateDefault();
        private EpochPortalDatabase _database = new EpochPortalDatabase();
        private Vector2 _modelsScroll;
        private Vector2 _imagesScroll;
        private Vector2 _storeScroll;
        private Vector2 _luminaxScroll;
        private string _lastCommandOutput = string.Empty;
        private readonly Dictionary<string, Texture2D> _externalTextureCache = new Dictionary<string, Texture2D>();

        private string _newModelDisplayName = "Epoch Warble Variant";
        private int _newModelAssetKindIndex;
        private int _newModelRequestModeIndex;
        private string _newModelExpectedAssetName = string.Empty;
        private string _newModelPrompt = string.Empty;
        private string _newModelNegativePrompt = string.Empty;
        private string _newModelTexturePrompt = string.Empty;
        private string _newModelReferenceImagePath = string.Empty;

        private string _newImageDisplayName = "Epoch UI Concept";
        private string _newImagePrompt = string.Empty;
        private string _newImageNegativePrompt = string.Empty;
        private int _newImageAspectRatioIndex = 2;

        private string _luminaxSubject = "Underfoot request";
        private string _luminaxBody = string.Empty;
        private string _luminaxFilesCsv = string.Empty;

        [MenuItem("Epoch Portal/Open")]
        public static void OpenWindow()
        {
            var window = GetWindow<UFEpochPortalWindow>();
            window.titleContent = new GUIContent(WindowTitle);
            window.minSize = new Vector2(920f, 640f);
            window.Show();
        }

        private bool _allowBackendOperations;

        private void OnEnable()
        {
            _envConfig = LoadEnvConfig();
            RefreshDatabaseFromDisk();
        }

        private static EpochPortalProjectConfig LoadEnvConfig()
        {
            var searchRoots = new[] { Path.GetDirectoryName(Application.dataPath) ?? string.Empty };
            return EpochPortalProjectConfig.LoadFromSearchRoots(searchRoots);
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
                case EpochPortalTab.Models:
                    DrawModelsTab();
                    break;
                case EpochPortalTab.Images:
                    DrawImagesTab();
                    break;
                case EpochPortalTab.StoreAssets:
                    DrawStoreAssetsTab();
                    break;
                default:
                    DrawLuminaxTab();
                    break;
            }

            EditorGUILayout.Space(10f);
            DrawCommandLog();
        }

        private void DrawToolbar()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Epoch Portal", EditorStyles.boldLabel);
            EditorGUILayout.LabelField("Underfoot model, image, asset intake, and Luminax request console.", EditorStyles.wordWrappedMiniLabel);
            EditorGUILayout.Space(4f);

            EditorGUILayout.BeginHorizontal();
            _activeTab = (EpochPortalTab)GUILayout.Toolbar((int)_activeTab, new[] { "Models", "Images", "Store Assets", "Luminax" }, GUILayout.Height(26f));
            if (GUILayout.Button("Sync Portal Data", GUILayout.Width(128f)))
            {
                RunBackendCommand("sync", null);
            }
            if (GUILayout.Button("Reveal Database", GUILayout.Width(128f)))
            {
                RevealExternalPath(GetDatabaseAbsolutePath());
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.LabelField(
                string.Format(
                    "DB: {0} | Models: {1} | Images: {2} | Store Assets: {3} | Retry Limit: {4}",
                    _envConfig.EpochPortalDatabaseAssetPath,
                    _database.modelJobs.Count,
                    _database.imageJobs.Count,
                    _database.storeAssets.Count,
                    Math.Max(1, _database.retryLimit)),
                EditorStyles.miniLabel);
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
                var imagePath = EditorUtility.OpenFilePanel("Choose Reference Image", ResolveProjectRoot(), "png,jpg,jpeg,webp");
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
            EditorGUILayout.LabelField("Each created or synced model appears here as soon as the portal database updates.", EditorStyles.wordWrappedMiniLabel);
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
                    "Unity-owned Asset Store downloads still have to come through the Unity Editor Package Manager. Epoch Portal scans the local downloaded package cache, tracks what is already present, and can import those packages into Underfoot for modification. Local ownership-cache hints found: {0}.",
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

        private void DrawLuminaxTab()
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField("Luminax Portal", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                HasBridgeConfiguration()
                    ? "Bridge auth is configured, so Epoch Portal can send the request straight to the local Underfoot bridge for authorized Luminax/Codex pickup. Local queueing still works as a fallback."
                    : "Bridge auth is not configured in .env yet, so this will queue structured Underfoot work requests into the shared Epoch Portal message file for Luminax/Codex pickup.",
                HasBridgeConfiguration() ? MessageType.Info : MessageType.Warning);
            _luminaxSubject = EditorGUILayout.TextField("Subject", _luminaxSubject);
            EditorGUILayout.LabelField("Body");
            _luminaxBody = EditorGUILayout.TextArea(_luminaxBody, GUILayout.MinHeight(96f));
            _luminaxFilesCsv = EditorGUILayout.TextField("File Paths (csv)", _luminaxFilesCsv);

            EditorGUILayout.BeginHorizontal();
            EditorGUI.BeginDisabledGroup(!HasBridgeConfiguration());
            if (GUILayout.Button("Send To Bridge", GUILayout.Width(140f)))
            {
                SendLuminaxRequestToBridge();
            }
            EditorGUI.EndDisabledGroup();
            if (GUILayout.Button("Queue Local", GUILayout.Width(140f)))
            {
                QueueLuminaxRequest();
            }
            if (GUILayout.Button("Reveal Queue File", GUILayout.Width(140f)))
            {
                RevealExternalPath(GetQueueAbsolutePath());
            }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.EndVertical();

            _luminaxScroll = EditorGUILayout.BeginScrollView(_luminaxScroll);
            foreach (var message in _database.luminaxMessages)
            {
                DrawLuminaxMessage(message);
            }
            EditorGUILayout.EndScrollView();
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

        private void DrawLuminaxMessage(LuminaxMessageRecord message)
        {
            EditorGUILayout.BeginVertical(EditorStyles.helpBox);
            EditorGUILayout.LabelField(message.subject, EditorStyles.boldLabel);
            EditorGUILayout.LabelField(string.Format("Status: {0} | Created: {1}", message.status, EpochToDisplay(message.createdAtEpoch)), EditorStyles.miniLabel);
            if (!string.IsNullOrWhiteSpace(message.body))
            {
                EditorGUILayout.LabelField(message.body, EditorStyles.wordWrappedMiniLabel);
            }
            if (message.filePaths != null && message.filePaths.Count > 0)
            {
                EditorGUILayout.LabelField(string.Join(", ", message.filePaths.ToArray()), EditorStyles.wordWrappedMiniLabel);
            }
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

        private void QueueLuminaxRequest()
        {
            var payload = new LuminaxRequestPayload
            {
                subject = _luminaxSubject,
                body = _luminaxBody,
                filePaths = SplitCsv(_luminaxFilesCsv),
            };
            RunBackendCommand("queue-luminax", JsonUtility.ToJson(payload, true));
        }

        private void EnsureDatabaseExists()
        {
            var path = GetDatabaseAbsolutePath();
            if (File.Exists(path))
            {
                return;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ResolveProjectRoot());
            var seed = JsonUtility.ToJson(new EpochPortalDatabase(), true);
            File.WriteAllText(path, seed, Encoding.UTF8);
            AssetDatabase.Refresh();
        }

        private void RefreshDatabaseFromDisk()
        {
            var path = GetDatabaseAbsolutePath();
            if (!File.Exists(path))
            {
                _database = new EpochPortalDatabase();
                return;
            }

            var json = File.ReadAllText(path, Encoding.UTF8);
            _database = JsonUtility.FromJson<EpochPortalDatabase>(json) ?? new EpochPortalDatabase();
            _database.modelJobs = _database.modelJobs ?? new List<ModelJobRecord>();
            _database.imageJobs = _database.imageJobs ?? new List<ImageJobRecord>();
            _database.storeAssets = _database.storeAssets ?? new List<StoreAssetRecord>();
            _database.ownedAssetHints = _database.ownedAssetHints ?? new List<OwnedAssetHintRecord>();
            _database.luminaxMessages = _database.luminaxMessages ?? new List<LuminaxMessageRecord>();
        }

        private void RunBackendCommand(string command, string? payloadJson, params string[] extraArguments)
        {
            var backendPath = Path.Combine(ResolveProjectRoot(), BackendScriptRelativePath);
            if (!File.Exists(backendPath))
            {
                _lastCommandOutput = string.Format("Epoch Portal backend was not found at {0}", backendPath);
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
                    WorkingDirectory = ResolveProjectRoot(),
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };

                using (var process = Process.Start(startInfo))
                {
                    if (process == null)
                    {
                        _lastCommandOutput = "Epoch Portal could not start the Python backend.";
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
                "Name: {0}\nAspect: {1}\nPrompt: {2}\nNegative: {3}",
                _newImageDisplayName,
                ImageAspectRatios[Mathf.Clamp(_newImageAspectRatioIndex, 0, ImageAspectRatios.Length - 1)],
                _newImagePrompt,
                _newImageNegativePrompt);
        }

        private string GetDatabaseAbsolutePath()
        {
            return ResolveConfiguredPath(_envConfig.EpochPortalDatabaseAssetPath, ResolveProjectRoot());
        }

        private string GetQueueAbsolutePath()
        {
            return ResolveConfiguredPath(_envConfig.EpochPortalQueueFilePath, ResolveProjectRoot());
        }

        private string GetOwnershipCacheAbsolutePath()
        {
            if (!string.IsNullOrWhiteSpace(_envConfig.UnityAssetStoreOwnershipCachePath))
            {
                return ResolveConfiguredPath(_envConfig.UnityAssetStoreOwnershipCachePath, ResolveProjectRoot());
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

        private static string ResolveProjectRoot()
        {
            return Path.GetDirectoryName(Application.dataPath) ?? Directory.GetCurrentDirectory();
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
                RevealExternalPath(ResolveConfiguredPath(outputFolder, ResolveProjectRoot()));
            }
        }

        private void PingDownloadedAsset(List<string> downloadedFiles)
        {
            if (downloadedFiles == null || downloadedFiles.Count == 0)
            {
                return;
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

                var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath);
                if (asset != null)
                {
                    Selection.activeObject = asset;
                    EditorGUIUtility.PingObject(asset);
                    return;
                }
            }
        }

        private Texture GetModelPreviewTexture(ModelJobRecord job)
        {
            if (!string.IsNullOrWhiteSpace(job.thumbnailPath))
            {
                var thumbnail = LoadExternalTexture(job.thumbnailPath);
                if (thumbnail != null)
                {
                    return thumbnail;
                }
            }

            if (job.downloadedFiles == null)
            {
                return null;
            }

            foreach (var filePath in job.downloadedFiles)
            {
                var assetPath = ToUnityAssetPath(filePath);
                if (string.IsNullOrWhiteSpace(assetPath))
                {
                    continue;
                }

                var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath);
                if (asset == null)
                {
                    continue;
                }

                var preview = AssetPreview.GetAssetPreview(asset);
                if (preview != null)
                {
                    return preview;
                }

                var mini = AssetPreview.GetMiniThumbnail(asset);
                if (mini != null)
                {
                    return mini;
                }
            }

            return null;
        }

        private Texture2D LoadExternalTexture(string path)
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
            return !string.IsNullOrWhiteSpace(_envConfig.LuminaxBridgeToken) &&
                   !string.IsNullOrWhiteSpace(_envConfig.LuminaxLocalOperatorId) &&
                   !string.IsNullOrWhiteSpace(_envConfig.LuminaxBridgeHost) &&
                   _envConfig.LuminaxBridgePort > 0;
        }

        private void SendLuminaxRequestToBridge()
        {
            var payload = new LuminaxRequestPayload
            {
                subject = _luminaxSubject,
                body = _luminaxBody,
                filePaths = SplitCsv(_luminaxFilesCsv),
            };

            try
            {
                var url = string.Format(
                    "http://{0}:{1}/api/bridge/epoch-portal/message",
                    _envConfig.LuminaxBridgeHost,
                    _envConfig.LuminaxBridgePort);
                var body = JsonUtility.ToJson(payload, true);
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
                request.Headers["X-Underfoot-Bridge-Token"] = _envConfig.LuminaxBridgeToken;
                request.Headers["X-Discord-User-Id"] = _envConfig.LuminaxLocalOperatorId;

                using (var stream = request.GetRequestStream())
                using (var writer = new StreamWriter(stream, Encoding.UTF8))
                {
                    writer.Write(body);
                }

                string bridgeOutput;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = response.GetResponseStream())
                using (var reader = new StreamReader(stream ?? Stream.Null, Encoding.UTF8))
                {
                    bridgeOutput = reader.ReadToEnd();
                }

                var parsed = JsonUtility.FromJson<BridgeResponse>(bridgeOutput);
                if (parsed != null && parsed.message != null && !string.IsNullOrWhiteSpace(parsed.message.id))
                {
                    _database.luminaxMessages.Insert(
                        0,
                        new LuminaxMessageRecord
                        {
                            id = parsed.message.id,
                            createdAtEpoch = parsed.message.created_at_epoch,
                            subject = parsed.message.subject,
                            body = parsed.message.body,
                            filePaths = parsed.message.file_paths ?? new List<string>(),
                            status = parsed.message.status,
                            queueFilePath = GetQueueAbsolutePath(),
                        });
                }

                RunBackendCommand("sync", null);
                _lastCommandOutput = bridgeOutput + Environment.NewLine + Environment.NewLine + _lastCommandOutput;
            }
            catch (WebException error)
            {
                string details = error.ToString();
                if (error.Response != null)
                {
                    using (var stream = error.Response.GetResponseStream())
                    using (var reader = new StreamReader(stream ?? Stream.Null, Encoding.UTF8))
                    {
                        details = reader.ReadToEnd();
                    }
                }
                _lastCommandOutput = details;
                Repaint();
            }
            catch (Exception error)
            {
                _lastCommandOutput = error.ToString();
                Repaint();
            }
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
    }
}
