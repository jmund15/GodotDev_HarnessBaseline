# C# source-text helpers shared by regression_gate.ps1 (test-attribute deletion evidence) and RuntimeSlots.ps1
# (recompile snapshot). Dot-source only; nothing runs on load.

# Blanks C# comments and string/char literals to spaces, keeping newlines and every offset, so a match on the result
# still indexes the original text and attribute- or class-like text inside a literal never matches.
function Remove-CSharpLiterals {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    $pattern = '(?s)/\*.*?\*/|//[^\r\n]*|@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|''(?:\\.|[^''\\])*'''
    [regex]::Replace($Text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{
        param($m) [regex]::Replace($m.Value, '[^\r\n]', ' ')
    })
}

function Get-TestAttributeDelta {
    param([string[]] $Patch)
    $files = [System.Collections.Generic.List[object]]::new()
    $file = $null
    $inHunk = $false
    foreach ($line in $Patch) {
        if ($line.StartsWith('diff --git ')) {
            $file = @{ old = [System.Text.StringBuilder]::new(); new = [System.Text.StringBuilder]::new()
                       removed = [System.Collections.Generic.List[object]]::new(); added = [System.Collections.Generic.List[object]]::new() }
            $files.Add($file)
            $inHunk = $false
        } elseif ($line.StartsWith('@@ ')) {
            $inHunk = $true
        } elseif ($line -match '^Binary files |^GIT binary patch') {
            return @{ readable = $false; removed = 0; added = 0; reason = 'binary-test-diff' }
        } elseif ($inHunk -and $file -and $line.Length -gt 0 -and $line[0] -in @(' ', '-', '+')) {
            $text = $line.Substring(1) + "`n"
            if ($line[0] -ne '+') {
                if ($line[0] -eq '-') { $file.removed.Add(@{ start = $file.old.Length; end = $file.old.Length + $text.Length }) }
                [void]$file.old.Append($text)
            }
            if ($line[0] -ne '-') {
                if ($line[0] -eq '+') { $file.added.Add(@{ start = $file.new.Length; end = $file.new.Length + $text.Length }) }
                [void]$file.new.Append($text)
            }
        }
    }
    $counts = @{ readable = $true; removed = 0; added = 0; reason = '' }
    # Full-context patches keep unchanged comments and DataPoint declarations visible. Mask strings/comments
    # at the same length, so changed-line offsets still refer to source, not to attribute-like text in a literal.
    $attributePattern = '\[(?>[^\[\]]+|\[(?<nested>)|\](?<-nested>))*(?(nested)(?!))\]'
    $argumentPattern = '\((?>[^()]+|\((?<nested>)|\)(?<-nested>))*(?(nested)(?!))\)'
    foreach ($f in $files) {
        foreach ($side in @(@{ source = 'old'; change = 'removed' }, @{ source = 'new'; change = 'added' })) {
            $source = $f[$side.source].ToString()
            # Raw literals and preprocessor branches need a C# parser to establish which declarations execute. A verbatim
            # string's escaped quote ("" before its closing ") is not a raw literal, and a branch holding no test
            # attribute decides no test, so only a branch from its first #if to its last #endif that holds one refuses.
            $noVerbatim = [regex]::Replace($source, '(?:\$@|@\$?)"(?:""|[^"])*"', '')
            $opens = [regex]::Matches($source, '(?m)^\s*#(?:if|elif|else)\b')
            $closes = [regex]::Matches($source, '(?m)^\s*#endif\b')
            $branch = ''
            if ($opens.Count -gt 0) {
                $end = if ($closes.Count -gt 0 -and $closes[$closes.Count - 1].Index -gt $opens[0].Index) { $closes[$closes.Count - 1].Index } else { $source.Length }
                $branch = $source.Substring($opens[0].Index, $end - $opens[0].Index)
            }
            $testAttribute = '\[\s*(?:global::)?(?:\w+\.)*(?:Test|TestCase|DataPoint|TestCaseSource|ValueSource)(?:Attribute)?\b'
            if ($noVerbatim -match '"{3,}' -or $branch -match $testAttribute) {
                return @{ readable = $false; removed = 0; added = 0; reason = 'uncountable-test-source' }
            }
            $masked = Remove-CSharpLiterals -Text $source
            foreach ($attr in [regex]::Matches($masked, $attributePattern)) {
                $names = [regex]::Replace($attr.Value, $argumentPattern, '')
                if ($names -match '(?:^\[|,)\s*(?:global::)?(?:\w+\.)*(?:DataPoint|TestCaseSource|ValueSource)(?:Attribute)?\b') {
                    return @{ readable = $false; removed = 0; added = 0; reason = 'data-driven-tests' }
                }
                $changed = @($f[$side.change] | Where-Object { $_.start -lt ($attr.Index + $attr.Length) -and $_.end -gt $attr.Index })
                if ($changed.Count -eq 0) { continue }
                $counts[$side.change] += [regex]::Matches($names,
                    '(?:^\[|,)\s*(?:global::)?(?:\w+\.)*(?:Test|TestCase)(?:Attribute)?(?=\s*(?:,|\]))').Count
            }
        }
    }
    return $counts
}
