param(
    [Parameter(Mandatory = $true)]
    [string]$InputDocx,
    [Parameter(Mandatory = $true)]
    [string]$OutputPdf
)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0

try {
    $openAndRepair = $true
    $document = $word.Documents.Open(
        $InputDocx,
        $false,
        $true,
        $false,
        "",
        "",
        $false,
        "",
        "",
        0,
        $false,
        $false,
        $openAndRepair
    )
    try {
        $document.ExportAsFixedFormat($OutputPdf, 17)
    }
    finally {
        $document.Close($false)
    }
}
finally {
    $word.Quit()
}
