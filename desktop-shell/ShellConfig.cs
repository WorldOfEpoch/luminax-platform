namespace Luminax.DesktopShell;

internal sealed class ShellConfig
{
    public string WindowTitle { get; init; } = "Luminax Desktop Shell";

    public string DefaultUrl { get; init; } = "";

    public string UrlEnvironmentVariable { get; init; } = "LUMINAX_SHELL_URL";

    public string SpeakingStatePlaceholder { get; init; } = "Idle (placeholder)";

    public int InitialWidth { get; init; } = 1440;

    public int InitialHeight { get; init; } = 920;

    public string ResolvedUrl { get; init; } = "";

    public string ConfigFilePath { get; init; } = string.Empty;

    public bool UsedEnvironmentOverride { get; init; }
}
