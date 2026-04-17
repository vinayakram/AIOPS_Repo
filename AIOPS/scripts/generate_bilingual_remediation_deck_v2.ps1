$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$markdownPath = Join-Path $root "REMEDIATION_POC_BILINGUAL_DECK_V2.md"
$output = Join-Path $root "Remediation-POC-Bilingual-Deck-v2.pptx"

function Parse-Slides {
    param([string[]]$Lines)

    $slides = New-Object System.Collections.ArrayList
    $current = $null

    foreach ($line in $Lines) {
        if ($line -match '^##\s+Slide\s+\d+:\s+(.*)$') {
            if ($null -ne $current) { [void]$slides.Add($current) }
            $current = [pscustomobject]@{
                Title = $matches[1].Trim()
                Subtitle = ""
                Bullets = New-Object System.Collections.ArrayList
            }
            continue
        }

        if ($null -eq $current) { continue }

        if ($line -match '^\s*-\s+(.*)$') {
            [void]$current.Bullets.Add($matches[1].Trim())
            continue
        }

        if (-not [string]::IsNullOrWhiteSpace($line) -and [string]::IsNullOrWhiteSpace($current.Subtitle)) {
            $current.Subtitle = $line.Trim()
        }
    }

    if ($null -ne $current) { [void]$slides.Add($current) }
    return $slides
}

$slides = Parse-Slides -Lines (Get-Content -LiteralPath $markdownPath -Encoding UTF8)

$ppLayoutText = 2
$msoTrue = -1
$titleColor = 0x1F2937
$accentColor = 0x0F766E
$bodyColor = 0x334155

$app = New-Object -ComObject PowerPoint.Application
$app.Visible = $msoTrue
$presentation = $app.Presentations.Add()

try {
    foreach ($slideDef in $slides) {
        $slide = $presentation.Slides.Add($presentation.Slides.Count + 1, $ppLayoutText)
        $slide.FollowMasterBackground = $msoTrue

        $bar = $slide.Shapes.AddShape(1, 0, 0, 960, 5)
        $bar.Fill.ForeColor.RGB = $accentColor
        $bar.Line.Visible = 0

        $title = $slide.Shapes.Title
        $title.TextFrame.TextRange.Text = $slideDef.Title
        $title.TextFrame.TextRange.Font.Name = "Aptos Display"
        $title.TextFrame.TextRange.Font.Size = 28
        $title.TextFrame.TextRange.Font.Bold = $msoTrue
        $title.TextFrame.TextRange.Font.Color.RGB = $titleColor

        $subtitleBox = $slide.Shapes.AddTextbox(1, 42, 70, 860, 28)
        $subtitleBox.TextFrame.TextRange.Text = if ($slideDef.Subtitle) { $slideDef.Subtitle } else { "Remediation POC / リメディエーション POC" }
        $subtitleBox.TextFrame.TextRange.Font.Name = "Aptos"
        $subtitleBox.TextFrame.TextRange.Font.Size = 17
        $subtitleBox.TextFrame.TextRange.Font.Bold = $msoTrue
        $subtitleBox.TextFrame.TextRange.Font.Color.RGB = $accentColor

        $body = $slide.Shapes.Placeholders.Item(2)
        $body.Left = 42
        $body.Top = 116
        $body.Width = 860
        $body.Height = 360
        $body.TextFrame.TextRange.Text = ""

        for ($i = 0; $i -lt $slideDef.Bullets.Count; $i++) {
            $prefix = if ($i -eq 0) { "" } else { "`r" }
            $body.TextFrame.TextRange.Text += ($prefix + [string]$slideDef.Bullets[$i])
            $paragraph = $body.TextFrame.TextRange.Paragraphs($i + 1)
            $paragraph.ParagraphFormat.Bullet.Visible = $msoTrue
            $paragraph.Font.Name = "Aptos"
            $paragraph.Font.Size = 19
            $paragraph.Font.Color.RGB = $bodyColor
            $paragraph.ParagraphFormat.SpaceAfter = 9
        }

        $footer = $slide.Shapes.AddTextbox(1, 42, 505, 860, 18)
        $footer.TextFrame.TextRange.Text = "Diagram assets: agentic_remediation_poc_corrected.html / agentic_remediation_poc_japanese.html"
        $footer.TextFrame.TextRange.Font.Name = "Aptos"
        $footer.TextFrame.TextRange.Font.Size = 10
        $footer.TextFrame.TextRange.Font.Color.RGB = 0x94A3B8
    }

    $presentation.SaveAs($output)
}
finally {
    if ($presentation) { $presentation.Close() }
    if ($app) { $app.Quit() }
}

Write-Output ("Created: " + $output)
