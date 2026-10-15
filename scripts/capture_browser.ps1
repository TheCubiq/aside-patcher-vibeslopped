param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference='Stop'
$expected=(Resolve-Path -LiteralPath $Executable).Path
$owners=@(Get-CimInstance Win32_Process -Filter "Name='Aside.exe'" |
    Where-Object {$_.ExecutablePath -and $_.ExecutablePath.Equals($expected,[StringComparison]::OrdinalIgnoreCase)} |
    ForEach-Object {$_.ProcessId})
if (-not $owners.Count) {throw 'Fixture browser is not running'}
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class BrowserCapture {
 public delegate bool EnumProc(IntPtr h,IntPtr p);
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb,IntPtr p);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
}
'@
$script:selected=[IntPtr]::Zero
$script:area=0
[void][BrowserCapture]::EnumWindows({param($handle,$unused)
    [uint32]$owner=0
    [void][BrowserCapture]::GetWindowThreadProcessId($handle,[ref]$owner)
    if ($owner -in $owners -and [BrowserCapture]::IsWindowVisible($handle)) {
        $r=[BrowserCapture+Rect]::new()
        [void][BrowserCapture]::GetWindowRect($handle,[ref]$r)
        $area=($r.Right-$r.Left)*($r.Bottom-$r.Top)
        if ($area -gt $script:area) {$script:area=$area;$script:selected=$handle}
    }
    return $true
},[IntPtr]::Zero)
if ($script:selected -eq [IntPtr]::Zero) {throw 'No visible fixture window'}
$old=[BrowserCapture]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
$rect=[BrowserCapture+Rect]::new()
[void][BrowserCapture]::GetWindowRect($script:selected,[ref]$rect)
$bitmap=[Drawing.Bitmap]::new($rect.Right-$rect.Left,$rect.Bottom-$rect.Top)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
try {
    $dc=$graphics.GetHdc()
    try {$ok=[BrowserCapture]::PrintWindow($script:selected,$dc,2)} finally {$graphics.ReleaseHdc($dc)}
    if (-not $ok) {throw 'Browser window capture failed'}
    $bitmap.Save($Output,[Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose();$bitmap.Dispose()
    [void][BrowserCapture]::SetThreadDpiAwarenessContext($old)
}
