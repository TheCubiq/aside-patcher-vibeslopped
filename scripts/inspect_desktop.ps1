param([Parameter(Mandatory=$true)][int]$AppProcessId,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
$expected=Join-Path (Split-Path $PSScriptRoot -Parent) 'desktop\target\debug\aside-patcher-desktop.exe'
if((Get-Process -Id $AppProcessId).Path -ne $expected){throw 'Unexpected desktop process'}
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;using System.Runtime.InteropServices;using System.Text;
public class PatcherWindowProbe {
 public delegate bool EnumProc(IntPtr h,IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int Left,Top,Right,Bottom;}
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb,IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
}
'@
$script:windowHandle=[IntPtr]::Zero
[void][PatcherWindowProbe]::EnumWindows({param($h,$l)
 [uint32]$owner=0;[void][PatcherWindowProbe]::GetWindowThreadProcessId($h,[ref]$owner)
 if($owner -eq $AppProcessId){
  $title=[Text.StringBuilder]::new(256);[void][PatcherWindowProbe]::GetWindowText($h,$title,256)
  if($title.ToString() -eq 'Aside Patcher'){$script:windowHandle=$h}
 }
 return $true
},[IntPtr]0)
if($script:windowHandle -eq [IntPtr]::Zero){throw 'Patcher main window missing'}
[void][PatcherWindowProbe]::ShowWindow($script:windowHandle,9)
Start-Sleep -Milliseconds 350
$rect=[PatcherWindowProbe+Rect]::new();[void][PatcherWindowProbe]::GetWindowRect($script:windowHandle,[ref]$rect)
$bitmap=[Drawing.Bitmap]::new($rect.Right-$rect.Left,$rect.Bottom-$rect.Top)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
try{
 $device=$graphics.GetHdc()
 try{if(-not [PatcherWindowProbe]::PrintWindow($script:windowHandle,$device,2)){throw 'Own-window capture failed'}}finally{$graphics.ReleaseHdc($device)}
 $bitmap.Save($Output)
}finally{$graphics.Dispose();$bitmap.Dispose()}
@{process=$AppProcessId;handle=$script:windowHandle.ToInt64();width=$rect.Right-$rect.Left;height=$rect.Bottom-$rect.Top}|ConvertTo-Json -Compress
