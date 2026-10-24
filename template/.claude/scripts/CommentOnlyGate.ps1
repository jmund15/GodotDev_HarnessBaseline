# Dot-sourced by regression_gate.ps1 after TreeDigest.ps1. A full-green snapshot, not HEAD or the index, owns the base.
Set-StrictMode -Version Latest

function Get-CommentEntryDigest {
    param([string[]] $Entries)
    $sorted = [string[]]@($Entries)
    [Array]::Sort($sorted, [StringComparer]::Ordinal)
    [Convert]::ToHexString([Security.Cryptography.SHA1]::HashData([Text.Encoding]::UTF8.GetBytes(($sorted -join "`n")))).ToLowerInvariant()
}

function ConvertTo-CommentFileMap {
    param([string[]] $Entries)
    $map = [Collections.Generic.Dictionary[string, string]]::new([StringComparer]::Ordinal)
    foreach ($entry in $Entries) {
        $split = $entry.IndexOf('|')
        if ($split -lt 1) { throw 'unreadable-tree-entry' }
        $path = $entry.Substring(0, $split)
        if ($map.ContainsKey($path)) { throw 'duplicate-tree-entry' }
        $map.Add($path, $entry.Substring($split + 1))
    }
    return ,$map
}

function Get-CommentGateFiles {
    param([string] $Repo, $Tree)
    if (-not $Tree.digest -or (Get-CommentEntryDigest $Tree.entries) -ne $Tree.digest) { throw 'unreadable-tree' }
    $map = ConvertTo-CommentFileMap $Tree.entries
    $gitlinkPrefix = 'gitlink|'
    $links = @($map.Keys | Where-Object { $map[$_].StartsWith($gitlinkPrefix) })
    # TreeDigest uses one jm/ namespace for dirty submodule overrides; flatten from each actual checkout instead.
    $overrides = @($map.Keys | Where-Object { $_.StartsWith('jm/') })
    if ($overrides.Count -and $links.Count -ne 1) { throw 'ambiguous-submodule-overrides' }
    foreach ($p in $overrides) { [void]$map.Remove($p) }
    foreach ($link in $links) {
        $subRepo = Join-Path $Repo $link
        $subTree = Get-TreeDigest -Repo $subRepo
        if ($subTree.head -ne $map[$link].Substring($gitlinkPrefix.Length)) { throw 'submodule-tree-mismatch' }
        $subMap = Get-CommentGateFiles -Repo $subRepo -Tree $subTree
        $map[$link] = 'gitlink'
        foreach ($p in $subMap.Keys) { $map.Add("$link/$p", $subMap[$p]) }
    }
    return ,$map
}

function Initialize-CommentLexer {
    if (-not ('Microsoft.CodeAnalysis.CSharp.SyntaxFactory' -as [type])) {
        $dotnet = Split-Path (Get-Command dotnet -ErrorAction Stop).Source
        $candidates = @(Get-ChildItem -Path (Join-Path $dotnet 'sdk/*/Roslyn/bincore/Microsoft.CodeAnalysis.CSharp.dll') -ErrorAction Stop |
            Sort-Object { [version]$_.Directory.Parent.Parent.Name.Split('-')[0] } -Descending)
        if (-not $candidates.Count) { throw 'roslyn-unavailable' }
        $dir = $candidates[0].Directory.FullName
        Add-Type -Path (Join-Path $dir 'Microsoft.CodeAnalysis.dll') -ErrorAction Stop
        Add-Type -Path (Join-Path $dir 'Microsoft.CodeAnalysis.CSharp.dll') -ErrorAction Stop
    }
    if (-not ('CommentGateLexerV1' -as [type])) {
        $refs = @((Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -Filter '*.dll').FullName) +
            @([Microsoft.CodeAnalysis.SyntaxToken].Assembly.Location, [Microsoft.CodeAnalysis.CSharp.SyntaxFactory].Assembly.Location)
        Add-Type -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701,1702' -TypeDefinition @'
using System;
using System.Text;
using System.Security.Cryptography;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

public static class CommentGateLexerV1
{
    static void Trivia(StringBuilder result, SyntaxTriviaList trivia)
    {
        foreach (var item in trivia)
        {
            if (item.ContainsDiagnostics) throw new InvalidOperationException("tokenizer-error");
            switch ((SyntaxKind)item.RawKind)
            {
                case SyntaxKind.WhitespaceTrivia:
                case SyntaxKind.EndOfLineTrivia:
                case SyntaxKind.SingleLineCommentTrivia:
                case SyntaxKind.MultiLineCommentTrivia:
                case SyntaxKind.SingleLineDocumentationCommentTrivia:
                case SyntaxKind.MultiLineDocumentationCommentTrivia:
                    continue;
            }
            if (item.GetStructure() is DirectiveTriviaSyntax directive)
            {
                result.Append('D').Append(item.RawKind).Append(':');
                foreach (var token in directive.DescendantTokens()) Token(result, token);
                continue;
            }
            if ((SyntaxKind)item.RawKind == SyntaxKind.DisabledTextTrivia)
            {
                Tokens(result, item.ToFullString());
                continue;
            }
            var text = item.ToFullString();
            result.Append('T').Append(item.RawKind).Append(':').Append(text.Length).Append(':').Append(text);
        }
    }

    static void Token(StringBuilder result, SyntaxToken token)
    {
        if (token.IsMissing) throw new InvalidOperationException("tokenizer-error");
        foreach (var diagnostic in token.GetDiagnostics())
            if (diagnostic.Severity == DiagnosticSeverity.Error) throw new InvalidOperationException("tokenizer-error");
        Trivia(result, token.LeadingTrivia);
        if ((SyntaxKind)token.RawKind != SyntaxKind.EndOfFileToken)
            result.Append('K').Append(token.RawKind).Append(':').Append(token.Text.Length).Append(':').Append(token.Text);
        Trivia(result, token.TrailingTrivia);
    }

    static void Tokens(StringBuilder result, string text)
    {
        var options = CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Preview);
        foreach (var token in SyntaxFactory.ParseTokens(text, 0, 0, options)) Token(result, token);
    }

    public static string Hash(string text)
    {
        var result = new StringBuilder();
        Tokens(result, text);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(result.ToString()))).ToLowerInvariant();
    }
}
'@ -ErrorAction Stop
    }
    'roslyn-token-text-v1:' + [Microsoft.CodeAnalysis.CSharp.SyntaxFactory].Assembly.GetName().Version.ToString()
}

function Get-CommentTokenHash {
    param([string] $Text)
    $null = Initialize-CommentLexer
    [CommentGateLexerV1]::Hash($Text)
}

function Get-CommentSourceHash {
    param([string] $Repo, [string] $Path, [string] $ExpectedBlob)
    $bytes = [IO.File]::ReadAllBytes((Join-Path $Repo $Path))
    $prefix = [Text.Encoding]::UTF8.GetBytes("blob $($bytes.Length)`0")
    $blob = [Convert]::ToHexString([Security.Cryptography.SHA1]::HashData([byte[]]($prefix + $bytes))).ToLowerInvariant()
    if ($blob -ne $ExpectedBlob) {
        # A clean CRLF checkout can have an LF HEAD blob. No other clean-filter transformation is assumed.
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $lf = $utf8.GetBytes($utf8.GetString($bytes).Replace("`r`n", "`n"))
        $prefix = [Text.Encoding]::UTF8.GetBytes("blob $($lf.Length)`0")
        $normalized = [Convert]::ToHexString([Security.Cryptography.SHA1]::HashData([byte[]]($prefix + $lf))).ToLowerInvariant()
        if ($normalized -ne $ExpectedBlob) { throw "source-tree-mismatch:$Path" }
    }
    $stream = [IO.MemoryStream]::new($bytes, $false)
    $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false, $true), $true)
    try { $text = $reader.ReadToEnd() } finally { $reader.Dispose() }
    Get-CommentTokenHash -Text $text
}

function New-CommentGateSnapshot {
    param([string] $Repo, $Tree)
    try {
        $lexer = Initialize-CommentLexer
        $files = Get-CommentGateFiles -Repo $Repo -Tree $Tree
        $tokens = [ordered]@{}
        foreach ($p in @($files.Keys | Sort-Object -CaseSensitive)) {
            if ($p.EndsWith('.cs', [StringComparison]::OrdinalIgnoreCase)) {
                $tokens[$p] = Get-CommentSourceHash -Repo $Repo -Path $p -ExpectedBlob $files[$p]
            }
        }
        if (-not (Test-DigestMatch (Get-TreeDigest -Repo $Repo).digest $Tree.digest)) { return $null }
        [pscustomobject]@{ version = 1; lexer = $lexer; treeDigest = $Tree.digest; treeEntries = @($Tree.entries);
            files = $files; tokens = $tokens }
    } catch {
        Write-Verbose "COMMENT_SNAPSHOT_UNAVAILABLE reason=$($_.Exception.Message)"
        return $null
    }
}

function Get-CommentOnlyRoute {
    param([string] $Repo, $Tree, [string] $LedgerDir)
    $no = [pscustomobject]@{ eligible = $false; reason = 'no-prior-full-green'; base = ''; files = @() }
    try {
        $best = $null; $bestTime = [datetime]::MinValue
        foreach ($file in @(Get-ChildItem -LiteralPath $LedgerDir -Filter '*.result.json' -ErrorAction SilentlyContinue)) {
            $rec = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            if ($rec.status -ne 'PASS' -or $rec.exitCode -ne 0 -or -not $rec.PSObject.Properties['coverage'] -or $rec.coverage -ne 'full') { continue }
            if ($rec.PSObject.Properties['reusedFrom'] -and $rec.reusedFrom) { continue }
            $time = ([datetime]$rec.finishedAt).ToUniversalTime()
            if ($time -ge $bestTime) { $best = $rec; $bestTime = $time }
        }
        if (-not $best) { return $no }
        $no.base = [string]$best.id
        $no.reason = 'unreadable-base'
        if (-not $best.PSObject.Properties['fullGreenSnapshot'] -or -not $best.fullGreenSnapshot) { return $no }
        $snap = $best.fullGreenSnapshot
        if ($snap.version -ne 1 -or $snap.lexer -ne (Initialize-CommentLexer) -or
            -not (Test-DigestMatch $snap.treeDigest $best.treeDigest) -or
            (Get-CommentEntryDigest $snap.treeEntries) -ne $snap.treeDigest) { return $no }
        $before = @{}
        foreach ($p in $snap.files.PSObject.Properties) { $before[$p.Name] = [string]$p.Value }
        $after = Get-CommentGateFiles -Repo $Repo -Tree $Tree
        $no.reason = 'file-set-changed'
        if ($before.Count -ne $after.Count) { return $no }
        $changed = [Collections.Generic.List[string]]::new()
        foreach ($p in $after.Keys) {
            if (-not $before.ContainsKey($p)) { return $no }
            if ($before[$p] -eq $after[$p]) { continue }
            $no.reason = 'non-csharp-change'
            if (-not $p.EndsWith('.cs', [StringComparison]::OrdinalIgnoreCase)) { return $no }
            $no.reason = 'tokens-changed-or-unreadable'
            $oldToken = $snap.tokens.PSObject.Properties[$p]
            if (-not $oldToken -or [string]$oldToken.Value -notmatch '^[0-9a-f]{64}$' -or
                (Get-CommentSourceHash -Repo $Repo -Path $p -ExpectedBlob $after[$p]) -ne $oldToken.Value) { return $no }
            $changed.Add($p)
        }
        $no.reason = 'no-csharp-difference'
        if (-not $changed.Count) { return $no }
        $no.reason = 'tree-changed'
        if (-not (Test-DigestMatch (Get-TreeDigest -Repo $Repo).digest $Tree.digest)) { return $no }
        [pscustomobject]@{ eligible = $true; reason = 'tokens-identical'; base = [string]$best.id;
            files = @($changed | Sort-Object -CaseSensitive) }
    } catch {
        $no.reason = 'comparison-failed:' + $_.Exception.Message
        $no
    }
}
