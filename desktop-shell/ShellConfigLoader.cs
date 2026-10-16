using System.Text.Json;

namespace Luminax.DesktopShell;

internal static class ShellConfigLoader
{
    private const string DefaultWindowTitle = "Luminax Desktop Shell";
    private const string DefaultUrl = "";
    private const string DefaultUrlEnvironmentVariable = "LUMINAX_SHELL_URL";
    private const string DefaultSpeakingState = "Idle (placeholder)";
    private const int DefaultWidth = 1440;
    private const int DefaultHeight = 920;
    private const string ConfigFileName = "shellsettings.json";

    public static ShellConfig Load()
    {
        var configPath = Path.Combine(AppContext.BaseDirectory, ConfigFileName);
        var fileConfig = LoadFromDisk(configPath);

        var urlEnvironmentVariable = NormalizeText(fileConfig.UrlEnvironmentVariable, DefaultUrlEnvironmentVariable);
        var configuredDefaultUrl = NormalizeText(fileConfig.DefaultUrl, DefaultUrl);
        var environmentOverride = Environment.GetEnvironmentVariable(urlEnvironmentVariable);
        var effectiveUrl = NormalizeUrl(environmentOverride, configuredDefaultUrl);

        return new ShellConfig
        {
            WindowTitle = NormalizeText(fileConfig.WindowTitle, DefaultWindowTitle),
            DefaultUrl = configuredDefaultUrl,
            UrlEnvironmentVariable = urlEnvironmentVariable,
            SpeakingStatePlaceholder = NormalizeText(fileConfig.SpeakingStatePlaceholder, DefaultSpeakingState),
            InitialWidth = NormalizeSize(fileConfig.InitialWidth, DefaultWidth),
            InitialHeight = NormalizeSize(fileConfig.InitialHeight, DefaultHeight),
            ResolvedUrl = effectiveUrl,
            ConfigFilePath = configPath,
            UsedEnvironmentOverride = !string.IsNullOrWhiteSpace(environmentOverride)
        };
    }

    private static ShellConfig LoadFromDisk(string configPath)
    {
        if (!File.Exists(configPath))
        {
            return new ShellConfig();
        }

        try
        {
            var json = File.ReadAllText(configPath);
            return JsonSerializer.Deserialize<ShellConfig>(
                       json,
                       new JsonSerializerOptions
                       {
                           AllowTrailingCommas = true,
                           PropertyNameCaseInsensitive = true,
                           ReadCommentHandling = JsonCommentHandling.Skip
                       })
                   ?? new ShellConfig();
        }
        catch
        {
            return new ShellConfig();
        }
    }

    private static string NormalizeText(string? value, string fallback)
    {
        return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
    }

    private static int NormalizeSize(int value, int fallback)
    {
        return value < 640 ? fallback : value;
    }

    private static string NormalizeUrl(string? preferredValue, string fallbackValue)
    {
        if (TryCreateHttpUri(preferredValue, out var preferredUri))
        {
            return preferredUri.AbsoluteUri;
        }

        if (TryCreateHttpUri(fallbackValue, out var fallbackUri))
        {
            return fallbackUri.AbsoluteUri;
        }

        return DefaultUrl;
    }

    private static bool TryCreateHttpUri(string? value, out Uri uri)
    {
        uri = null!;

        if (string.IsNullOrWhiteSpace(value))
        {
            return false;
        }

        var candidate = value.Trim();
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

        uri = parsed;
        return true;
    }
}
