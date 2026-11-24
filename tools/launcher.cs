using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

/// <summary>
/// Tiny native launcher for the portable 随舞剪辑台 application.
/// It starts the bundled pythonw.exe without showing a console window and
/// forwards an optional project-folder argument.
/// </summary>
internal static class SuibuLauncher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    private static void Main(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(baseDir, "runtime", "pythonw.exe");
        string script = Path.Combine(baseDir, "app.py");

        if (!File.Exists(python) || !File.Exists(script))
        {
            MessageBoxW(
                IntPtr.Zero,
                "程序文件不完整：找不到 runtime\\pythonw.exe 或 app.py。\n请重新解压完整文件夹后再运行。",
                "随舞剪辑台",
                0x10
            );
            return;
        }

        var command = new StringBuilder();
        command.Append(Quote(script));
        foreach (string arg in args)
        {
            command.Append(' ').Append(Quote(arg));
        }

        try
        {
            var info = new ProcessStartInfo(python, command.ToString())
            {
                WorkingDirectory = baseDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            Process.Start(info);
        }
        catch (Exception ex)
        {
            MessageBoxW(
                IntPtr.Zero,
                "无法启动随舞剪辑台：\n" + ex.Message,
                "随舞剪辑台",
                0x10
            );
        }
    }

    /// <summary>Quote one argument using the Windows command-line rules.</summary>
    private static string Quote(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return "\"\"";
        }
        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char c in value)
        {
            if (c == '\\')
            {
                backslashes++;
            }
            else if (c == '"')
            {
                result.Append('\\', backslashes * 2 + 1);
                result.Append('"');
                backslashes = 0;
            }
            else
            {
                result.Append('\\', backslashes);
                result.Append(c);
                backslashes = 0;
            }
        }
        result.Append('\\', backslashes * 2);
        result.Append('"');
        return result.ToString();
    }
}
