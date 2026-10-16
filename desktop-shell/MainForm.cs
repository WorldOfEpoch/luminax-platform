using System.Drawing;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Luminax.DesktopShell;

internal sealed class MainForm : Form
{
    private const int DiagnosticsPanelMinWidth = 280;

    private readonly ShellConfig _config;
    private readonly WebView2 _webView;
    private readonly GroupBox _configGroup;
    private readonly SplitContainer _mainSplit;
    private readonly TextBox _targetUrlTextBox;
    private readonly Label _envOverrideValueLabel;
    private readonly Label _bridgeStateValueLabel;
    private readonly Label _speakingStateValueLabel;
    private readonly ListBox _diagnosticsList;
    private readonly ToolStripStatusLabel _targetUrlStatusLabel;
    private readonly ToolStripStatusLabel _speakingStateStatusLabel;
    private readonly ToolStripStatusLabel _navigationStatusLabel;
    private readonly string _diagnosticsLogPath;

    private bool _webViewReady;
    private bool _operatorViewPrimed;

    public MainForm(ShellConfig config)
    {
        _config = config;

        Text = _config.WindowTitle;
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(960, 640);
        ClientSize = new Size(_config.InitialWidth, _config.InitialHeight);
        WindowState = FormWindowState.Maximized;

        _webView = new WebView2
        {
            Dock = DockStyle.Fill
        };

        _targetUrlTextBox = new TextBox
        {
            Dock = DockStyle.Fill,
            Text = _config.ResolvedUrl
        };

        _envOverrideValueLabel = new Label
        {
            AutoSize = true,
            Text = _config.UsedEnvironmentOverride
                ? $"{_config.UrlEnvironmentVariable} active"
                : $"{_config.UrlEnvironmentVariable} inactive"
        };

        _bridgeStateValueLabel = new Label
        {
            AutoSize = true,
            Text = "Browser bridge: placeholder only"
        };

        _speakingStateValueLabel = new Label
        {
            AutoSize = true,
            Text = "Speaking state: " + _config.SpeakingStatePlaceholder
        };

        _diagnosticsList = new ListBox
        {
            Dock = DockStyle.Fill,
            HorizontalScrollbar = true
        };

        _targetUrlStatusLabel = new ToolStripStatusLabel();
        _speakingStateStatusLabel = new ToolStripStatusLabel();
        _navigationStatusLabel = new ToolStripStatusLabel();
        _diagnosticsLogPath = Path.Combine(AppContext.BaseDirectory, "desktop-shell.runtime.log");
        _configGroup = CreateConfigGroup();
        _configGroup.Visible = true;
        _mainSplit = CreateMainContent();
        _mainSplit.Panel2Collapsed = true;

        BuildLayout();
        WireEvents();
    }

    private void BuildLayout()
    {
        var root = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 2,
            Padding = new Padding(8)
        };
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));

        root.Controls.Add(_configGroup, 0, 0);
        root.Controls.Add(_mainSplit, 0, 1);

        var statusStrip = new StatusStrip();
        statusStrip.Items.Add(_targetUrlStatusLabel);
        statusStrip.Items.Add(new ToolStripStatusLabel { Spring = true });
        statusStrip.Items.Add(_speakingStateStatusLabel);
        statusStrip.Items.Add(new ToolStripSeparator());
        statusStrip.Items.Add(_navigationStatusLabel);

        Controls.Add(root);
        Controls.Add(statusStrip);

        UpdateTargetUrlStatus(_config.ResolvedUrl);
        UpdateSpeakingState(_config.SpeakingStatePlaceholder);
        UpdateNavigationStatus("Shell starting");
        AddDiagnostic("Focused companion mode enabled.");
        AddDiagnostic("Shell configuration loaded from " + Path.GetFileName(_config.ConfigFilePath));
        AddDiagnostic("Configured target is " + (string.IsNullOrWhiteSpace(_config.DefaultUrl) ? "empty" : "available"));
    }

    private GroupBox CreateConfigGroup()
    {
        var group = new GroupBox
        {
            Dock = DockStyle.Top,
            Text = "Shell Config",
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink
        };

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 4,
            RowCount = 4,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Padding = new Padding(8)
        };

        layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

        layout.Controls.Add(CreateHeaderLabel("Window Title"), 0, 0);
        layout.Controls.Add(CreateValueLabel(_config.WindowTitle), 1, 0);
        layout.Controls.Add(CreateHeaderLabel("Env Override"), 2, 0);
        layout.Controls.Add(_envOverrideValueLabel, 3, 0);

        layout.Controls.Add(CreateHeaderLabel("Default URL"), 0, 1);
        layout.Controls.Add(CreateValueLabel(_config.DefaultUrl), 1, 1);
        layout.SetColumnSpan(layout.GetControlFromPosition(1, 1)!, 3);

        layout.Controls.Add(CreateHeaderLabel("Config File"), 0, 2);
        layout.Controls.Add(CreateValueLabel(_config.ConfigFilePath), 1, 2);
        layout.SetColumnSpan(layout.GetControlFromPosition(1, 2)!, 3);

        layout.Controls.Add(CreateHeaderLabel("Target URL"), 0, 3);
        layout.Controls.Add(_targetUrlTextBox, 1, 3);

        var goButton = new Button
        {
            AutoSize = true,
            Text = "Connect"
        };
        goButton.Click += (_, _) => NavigateFromInput();
        layout.Controls.Add(goButton, 2, 3);

        var resetButton = new Button
        {
            AutoSize = true,
            Text = "Reset"
        };
        resetButton.Click += (_, _) => ResetToConfiguredUrl();
        layout.Controls.Add(resetButton, 3, 3);

        group.Controls.Add(layout);
        return group;
    }

    private SplitContainer CreateMainContent()
    {
        var split = new SplitContainer
        {
            Dock = DockStyle.Fill,
            FixedPanel = FixedPanel.Panel2,
            Orientation = Orientation.Vertical
        };

        split.Panel1.Padding = new Padding(0, 8, 8, 0);
        split.Panel2.Padding = new Padding(8, 8, 0, 0);
        split.Panel1.Controls.Add(_webView);
        split.Panel2.Controls.Add(CreateDiagnosticsGroup());
        split.HandleCreated += (_, _) => BeginInvoke(new Action(() => ApplyPreferredSplitterDistance(split)));
        split.SizeChanged += (_, _) => ApplyPreferredSplitterDistance(split);

        return split;
    }

    private static void ApplyPreferredSplitterDistance(SplitContainer split)
    {
        if (!split.IsHandleCreated || split.Width <= 0)
        {
            return;
        }

        if (split.Panel2MinSize != DiagnosticsPanelMinWidth && split.Width > DiagnosticsPanelMinWidth + 320)
        {
            split.Panel2MinSize = DiagnosticsPanelMinWidth;
        }

        var minimum = Math.Max(320, split.Panel1MinSize);
        var panel2Minimum = split.Panel2MinSize > 0 ? split.Panel2MinSize : DiagnosticsPanelMinWidth;
        var maximum = split.Width - panel2Minimum - split.SplitterWidth;
        if (maximum <= minimum)
        {
            return;
        }

        var preferred = Math.Min(960, maximum);
        split.SplitterDistance = Math.Max(minimum, preferred);
    }

    private Control CreateDiagnosticsGroup()
    {
        var group = new GroupBox
        {
            Dock = DockStyle.Fill,
            Text = "Diagnostics"
        };

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 3,
            Padding = new Padding(8)
        };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));

        layout.Controls.Add(_bridgeStateValueLabel, 0, 0);
        layout.Controls.Add(_speakingStateValueLabel, 0, 1);
        layout.Controls.Add(_diagnosticsList, 0, 2);

        group.Controls.Add(layout);
        return group;
    }

    private static Label CreateHeaderLabel(string text)
    {
        return new Label
        {
            AutoSize = true,
            Font = new Font(SystemFonts.DefaultFont, FontStyle.Bold),
            Margin = new Padding(0, 6, 10, 6),
            Text = text
        };
    }

    private static Label CreateValueLabel(string text)
    {
        return new Label
        {
            AutoEllipsis = true,
            AutoSize = true,
            Margin = new Padding(0, 6, 10, 6),
            MaximumSize = new Size(900, 0),
            Text = text
        };
    }

    private void WireEvents()
    {
        Shown += async (_, _) => await InitializeWebViewAsync();
        _targetUrlTextBox.KeyDown += TargetUrlTextBoxOnKeyDown;
        _webView.NavigationStarting += WebViewOnNavigationStarting;
        _webView.NavigationCompleted += WebViewOnNavigationCompleted;
        _webView.SourceChanged += WebViewOnSourceChanged;
    }

    private async Task InitializeWebViewAsync()
    {
        AddDiagnostic("Initializing WebView2 host.");

        try
        {
            var userDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Luminax",
                "DesktopShell",
                "WebView2");
            Directory.CreateDirectory(userDataFolder);
            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: userDataFolder,
                options: new CoreWebView2EnvironmentOptions("--disable-gpu --disable-gpu-compositing"));

            await _webView.EnsureCoreWebView2Async(environment);
            _webViewReady = true;

            if (_webView.CoreWebView2 is not null)
            {
                _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                _webView.CoreWebView2.Settings.AreDevToolsEnabled = true;
                _webView.CoreWebView2.Settings.IsStatusBarEnabled = true;
                _webView.CoreWebView2.Settings.IsSwipeNavigationEnabled = true;
                _webView.CoreWebView2.PermissionRequested += (_, args) =>
                {
                    // The browser's permission prompt remains authoritative.
                    args.State = CoreWebView2PermissionState.Default;
                };
            }

            _bridgeStateValueLabel.Text = "Browser bridge: future hooks pending";
            AddDiagnostic("WebView2 ready.");
            _webView.CoreWebView2?.NavigateToString("<html><body style='font-family:Segoe UI;padding:32px'><h1>Luminax</h1><p>Enter your configured local or HTTPS service address and choose Connect.</p><p>No service connection starts automatically.</p></body></html>");
        }
        catch (Exception ex)
        {
            _webViewReady = false;
            _bridgeStateValueLabel.Text = "Browser bridge: WebView2 unavailable";
            UpdateNavigationStatus("WebView2 unavailable");
            AddDiagnostic("WebView2 initialization failed: " + ex.GetType().Name);
        }
    }

    private void TargetUrlTextBoxOnKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.KeyCode != Keys.Enter)
        {
            return;
        }

        e.SuppressKeyPress = true;
        NavigateFromInput();
    }

    private void NavigateFromInput()
    {
        NavigateTo(_targetUrlTextBox.Text);
    }

    private void ResetToConfiguredUrl()
    {
        _targetUrlTextBox.Text = _config.ResolvedUrl;
        NavigateTo(_config.ResolvedUrl);
    }

    private void NavigateTo(string rawTarget)
    {
        if (!TryCreateTargetUri(rawTarget, out var targetUri))
        {
            UpdateNavigationStatus("Invalid target URL");
            AddDiagnostic("Rejected target URL; use loopback HTTP or HTTPS without embedded credentials.");
            return;
        }

        var absoluteUrl = targetUri.AbsoluteUri;
        _targetUrlTextBox.Text = absoluteUrl;
        UpdateTargetUrlStatus(absoluteUrl);
        AddDiagnostic("Target address selected.");

        if (!_webViewReady)
        {
            UpdateNavigationStatus("WebView2 not ready");
            return;
        }

        _webView.Source = targetUri;
    }

    private void WebViewOnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        UpdateNavigationStatus("Loading");
        UpdateTargetUrlStatus(e.Uri);
        AddDiagnostic("Navigation starting.");
    }

    private void WebViewOnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (e.IsSuccess)
        {
            UpdateNavigationStatus("Ready");
            AddDiagnostic("Navigation completed.");
            // Keep welcome acknowledgments and microphone consent user-controlled.
            return;
        }

        UpdateNavigationStatus("Navigation failed");
        AddDiagnostic("Navigation failed with WebView2 status " + e.WebErrorStatus);
    }

    private void WebViewOnSourceChanged(object? sender, CoreWebView2SourceChangedEventArgs e)
    {
        if (_webView.Source is null)
        {
            return;
        }

        var absoluteUrl = _webView.Source.AbsoluteUri;
        _targetUrlTextBox.Text = absoluteUrl;
        UpdateTargetUrlStatus(absoluteUrl);
    }

    private void UpdateTargetUrlStatus(string targetUrl)
    {
        _targetUrlStatusLabel.Text = "Target URL: " + targetUrl;
    }

    private void UpdateSpeakingState(string stateText)
    {
        var text = string.IsNullOrWhiteSpace(stateText) ? "Idle (placeholder)" : stateText.Trim();
        _speakingStateValueLabel.Text = "Speaking state: " + text;
        _speakingStateStatusLabel.Text = "Speaking: " + text;
    }

    private void UpdateNavigationStatus(string statusText)
    {
        _navigationStatusLabel.Text = "Status: " + statusText;
    }

    private void AddDiagnostic(string message)
    {
        var entry = $"{DateTime.Now:HH:mm:ss}  {message}";
        _diagnosticsList.Items.Insert(0, entry);
        try
        {
            File.AppendAllText(_diagnosticsLogPath, entry + Environment.NewLine);
        }
        catch
        {
        }

        while (_diagnosticsList.Items.Count > 40)
        {
            _diagnosticsList.Items.RemoveAt(_diagnosticsList.Items.Count - 1);
        }
    }

    private async Task PrimeOperatorViewAsync()
    {
        if (_operatorViewPrimed || !_webViewReady || _webView.CoreWebView2 is null)
        {
            return;
        }

        if (!IsLocalCompanionUri(_webView.Source))
        {
            return;
        }

        const string script = """
(() => {
  try {
    localStorage.setItem('luminax_acknowledged', 'true');
    if (typeof dismissWelcome === 'function') {
      dismissWelcome(true);
    }
    if (typeof openViewQuick === 'function') {
      openViewQuick('chatView');
    }
    const input = document.getElementById('text-input');
    if (input && typeof input.focus === 'function') {
      input.focus();
    }
    return 'chat-ready';
  } catch (error) {
    return 'prime-failed:' + (error && error.message ? error.message : String(error));
  }
})();
""";

        try
        {
            var result = await _webView.CoreWebView2.ExecuteScriptAsync(script);
            _operatorViewPrimed = true;
            AddDiagnostic("Operator view primed: " + result);
        }
        catch (Exception ex)
        {
            AddDiagnostic("Operator view priming failed: " + ex.GetType().Name);
        }
    }

    private static bool IsLocalCompanionUri(Uri? uri)
    {
        if (uri is null)
        {
            return false;
        }

        return uri.IsLoopback ||
               string.Equals(uri.Host, "127.0.0.1", StringComparison.OrdinalIgnoreCase) ||
               string.Equals(uri.Host, "localhost", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsLocalCompanionUri(string? uriText)
    {
        return Uri.TryCreate(uriText, UriKind.Absolute, out var uri) && IsLocalCompanionUri(uri);
    }

    private static bool TryCreateTargetUri(string? rawTarget, out Uri targetUri)
    {
        targetUri = null!;

        if (string.IsNullOrWhiteSpace(rawTarget))
        {
            return false;
        }

        var candidate = rawTarget.Trim();
        if (!candidate.Contains("://", StringComparison.Ordinal))
        {
            candidate = "http://" + candidate;
        }

        if (!Uri.TryCreate(candidate, UriKind.Absolute, out var parsed))
        {
            return false;
        }

        if (!string.Equals(parsed.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(parsed.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        if (!string.IsNullOrEmpty(parsed.UserInfo) ||
            (parsed.Scheme == Uri.UriSchemeHttp && !parsed.IsLoopback))
        {
            return false;
        }

        targetUri = parsed;
        return true;
    }
}
