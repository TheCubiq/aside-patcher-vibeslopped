param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class AsideMenuWindows {
    public delegate bool Callback(IntPtr handle, IntPtr argument);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr argument);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr handle, out uint process);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr handle);
    public static List<IntPtr> ForProcesses(uint[] processes) {
        var result = new List<IntPtr>();
        EnumWindows((handle, argument) => {
            uint process;
            GetWindowThreadProcessId(handle, out process);
            if (IsWindowVisible(handle) && Array.IndexOf(processes, process) >= 0) result.Add(handle);
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
'@
$resolved = (Resolve-Path -LiteralPath $Executable).Path
$processes = @(Get-CimInstance Win32_Process -Filter "Name='Aside.exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals($resolved, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { [uint32]$_.ProcessId })
$names = @()
if ($processes.Count) {
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::MenuItem)
    foreach ($handle in [AsideMenuWindows]::ForProcesses([uint32[]]$processes)) {
        $root = [Windows.Automation.AutomationElement]::FromHandle($handle)
        if ($root) {
            foreach ($item in $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)) {
                if (-not $item.Current.IsOffscreen) { $names += $item.Current.Name }
            }
        }
    }
}
ConvertTo-Json -Compress -InputObject @($names | Sort-Object -Unique)
