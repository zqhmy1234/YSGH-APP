# OCR a PNG screenshot via Windows.Media.Ocr (Windows PowerShell 5.1).
#
# Why this exists: uni-app x Vapor renders text on custom views, so `uiautomator dump`
# returns no text nodes; and the agent driving 真机复验 cannot view images directly.
# This turns a screencap into plain text so on-screen content can be asserted.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ocr_screenshot.ps1 -Path <png> [-Lang zh-Hans-CN]

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Lang = 'zh-Hans-CN'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Path)) { Write-Error "png not found: $Path"; exit 2 }

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$engine = $null
if ($Lang -ne '') {
    try {
        $language = New-Object Windows.Globalization.Language $Lang
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    } catch { $engine = $null }
}
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($engine -eq $null) {
    Write-Output ("[ocr] no engine. available languages: " + (([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) -join ','))
    exit 3
}
Write-Output ("[ocr] engine=" + $engine.RecognizerLanguage.LanguageTag)

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path -LiteralPath $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

foreach ($line in $result.Lines) { Write-Output $line.Text }
