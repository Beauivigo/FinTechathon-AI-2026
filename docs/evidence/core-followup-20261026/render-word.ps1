$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$renderRoot = Join-Path $projectRoot '.cache/core-followup-20261026-render-r2'
New-Item -ItemType Directory -Path $renderRoot -Force | Out-Null
$wordApp = $null
$records = @()
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    foreach ($item in @(@('02_言行_技术文档_v0.3.docx','tech'),@('06_言行_安全自评报告_v0.3.docx','security'))) {
        $document = $null
        $sourcePath = Join-Path $projectRoot ('参赛材料_Word_2026-10-24/' + $item[0])
        $outputFolder = Join-Path $renderRoot $item[1]
        New-Item -ItemType Directory -Path $outputFolder -Force | Out-Null
        $pdfPath = Join-Path $outputFolder 'document.pdf'
        try {
            $document = $wordApp.Documents.Open($sourcePath, $false, $true, $false)
            $document.Fields.Update() | Out-Null
            $document.Repaginate()
            $document.ExportAsFixedFormat($pdfPath, 17)
            $records += [pscustomobject]@{ file=$item[0]; word_reported_pages=$document.ComputeStatistics(2); pdf=$pdfPath; renderer=('Microsoft Word ' + $wordApp.Version) }
        } finally {
            if ($null -ne $document) { $document.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
        }
    }
} finally {
    if ($null -ne $wordApp) { $wordApp.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) }
}
$records | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'word-render.json') -Encoding utf8
$records | ConvertTo-Json -Depth 3
