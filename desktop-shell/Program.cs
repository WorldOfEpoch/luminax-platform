using System.Windows.Forms;

namespace Luminax.DesktopShell;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        var config = ShellConfigLoader.Load();
        Application.Run(new MainForm(config));
    }
}
