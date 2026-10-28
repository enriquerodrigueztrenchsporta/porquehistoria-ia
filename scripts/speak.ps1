param(
    [Parameter(Mandatory = $true)][string]$InputFile,
    [Parameter(Mandatory = $true)][string]$OutputFile,
    [int]$Rate = 1
)
$ErrorActionPreference = 'Stop'
$text = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($text)) { throw 'El texto de narración está vacío.' }
$speaker = New-Object -ComObject SAPI.SpVoice
$spanishVoice = $speaker.GetVoices() | Where-Object {
    $_.GetAttribute('Language') -match '(^|;)(c0a|40a|[0-9a-f]*0a)(;|$)'
} | Select-Object -First 1
if ($null -eq $spanishVoice) {
    throw 'No hay voz española SAPI instalada. Configura un modelo Piper o instala una voz española de Windows.'
}
$stream = New-Object -ComObject SAPI.SpFileStream
try {
    $stream.Format.Type = 22 # PCM de 22.050 Hz, 16 bits, mono.
    $stream.Open($OutputFile, 3, $false)
    $speaker.Voice = $spanishVoice
    $speaker.Rate = $Rate
    $speaker.AudioOutputStream = $stream
    [void]$speaker.Speak($text)
} finally {
    $stream.Close()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($speaker)
}
