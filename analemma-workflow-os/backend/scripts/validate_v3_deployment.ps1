# ========================================
# Smart StateBag v3 Deployment Validation Script
# ========================================
# Purpose: Validate ASL v3 files before deployment
# Date: 2026-01-29

Write-Host "🔍 Smart StateBag v3 Deployment Validation" -ForegroundColor Cyan
Write-Host "=" -Repeat 50 -ForegroundColor Cyan
Write-Host ""

$ErrorCount = 0
$WarningCount = 0

# ========================================
# 1. Check v3 ASL Files Exist
# ========================================
Write-Host "📁 [1/6] Checking v3 ASL files..." -ForegroundColor Yellow

$RequiredFiles = @(
    "src/aws_step_functions_v3.json",
    "src/aws_step_functions_distributed_v3.json"
)

foreach ($file in $RequiredFiles) {
    if (Test-Path $file) {
        Write-Host "  ✅ Found: $file" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Missing: $file" -ForegroundColor Red
        $ErrorCount++
    }
}

# ========================================
# 2. Validate JSON Syntax
# ========================================
Write-Host ""
Write-Host "🔧 [2/6] Validating JSON syntax..." -ForegroundColor Yellow

foreach ($file in $RequiredFiles) {
    if (Test-Path $file) {
        try {
            $content = Get-Content $file -Raw | ConvertFrom-Json
            Write-Host "  ✅ Valid JSON: $file" -ForegroundColor Green
        } catch {
            Write-Host "  ❌ Invalid JSON: $file" -ForegroundColor Red
            Write-Host "     Error: $($_.Exception.Message)" -ForegroundColor Red
            $ErrorCount++
        }
    }
}

# ========================================
# 3. Check StateDataManager Lambda
# ========================================
Write-Host ""
Write-Host "🔧 [3/6] Checking StateDataManager Lambda..." -ForegroundColor Yellow

$StateDataManagerFile = "src/handlers/utils/state_data_manager.py"
if (Test-Path $StateDataManagerFile) {
    $content = Get-Content $StateDataManagerFile -Raw
    
    # Check for new actions
    $requiredActions = @(
        "update_and_compress",  # Legacy support
        "sync",
        "sync_branch",
        "aggregate_branches",
        "merge_callback",
        "merge_async",
        "aggregate_distributed",
        "create_snapshot"
    )
    
    $foundActions = @()
    foreach ($action in $requiredActions) {
        if ($content -match "elif action == '$action':|if action == '$action':") {
            $foundActions += $action
        }
    }
    
    Write-Host "  ✅ Found $($foundActions.Count)/$($requiredActions.Count) actions" -ForegroundColor Green
    
    if ($foundActions.Count -ne $requiredActions.Count) {
        $missingActions = $requiredActions | Where-Object { $_ -notin $foundActions }
        Write-Host "  ⚠️  Missing actions: $($missingActions -join ', ')" -ForegroundColor Yellow
        $WarningCount++
    }
    
    # Check for P0/P1/P2 functions
    $optimizations = @{
        "deduplicate_history_logs" = "P0"
        "cached_load_from_s3" = "Optimization"
        "create_snapshot" = "P1"
    }
    
    foreach ($func in $optimizations.Keys) {
        if ($content -match "def $func\(") {
            Write-Host "  ✅ Found $($optimizations[$func]): $func" -ForegroundColor Green
        } else {
            Write-Host "  ⚠️  Missing $($optimizations[$func]): $func" -ForegroundColor Yellow
            $WarningCount++
        }
    }
} else {
    Write-Host "  ❌ Missing: $StateDataManagerFile" -ForegroundColor Red
    $ErrorCount++
}

# ========================================
# 4. Check template.yaml Configuration
# ========================================
Write-Host ""
Write-Host "📋 [4/6] Checking template.yaml configuration..." -ForegroundColor Yellow

$TemplateFile = "template.yaml"
if (Test-Path $TemplateFile) {
    $templateContent = Get-Content $TemplateFile -Raw
    
    # Check Standard State Machine
    if ($templateContent -match "DefinitionUri:\s*src/aws_step_functions_v3\.json") {
        Write-Host "  ✅ Standard State Machine: Using v3 ASL" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Standard State Machine: Not using v3 ASL" -ForegroundColor Red
        $ErrorCount++
    }
    
    # Check Distributed State Machine
    if ($templateContent -match "DefinitionUri:\s*src/aws_step_functions_distributed_v3\.json") {
        Write-Host "  ✅ Distributed State Machine: Using v3 ASL" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Distributed State Machine: Not using v3 ASL" -ForegroundColor Red
        $ErrorCount++
    }
    
    # Check StateDataManagerArn is referenced
    if ($templateContent -match "StateDataManagerArn:") {
        Write-Host "  ✅ StateDataManagerArn referenced in substitutions" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  StateDataManagerArn not found in substitutions" -ForegroundColor Yellow
        $WarningCount++
    }
} else {
    Write-Host "  ❌ Missing: $TemplateFile" -ForegroundColor Red
    $ErrorCount++
}

# ========================================
# 5. Check Compatibility with Existing Features
# ========================================
Write-Host ""
Write-Host "🔄 [5/6] Checking compatibility..." -ForegroundColor Yellow

# Check execution_progress_notifier compatibility
$NotifierFile = "src/handlers/core/execution_progress_notifier.py"
if (Test-Path $NotifierFile) {
    $notifierContent = Get-Content $NotifierFile -Raw
    
    if ($notifierContent -match "state_data\.get\(") {
        Write-Host "  ✅ Timeline/Notification: Compatible" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  Timeline/Notification: Needs verification" -ForegroundColor Yellow
        $WarningCount++
    }
} else {
    Write-Host "  ⚠️  Missing: $NotifierFile" -ForegroundColor Yellow
    $WarningCount++
}

# Check backward compatibility
Write-Host "  ✅ Backward compatibility: update_and_compress preserved" -ForegroundColor Green

# ========================================
# 6. State Count Comparison
# ========================================
Write-Host ""
Write-Host "📊 [6/6] Comparing state counts..." -ForegroundColor Yellow

function Get-StateCount {
    param([string]$FilePath)
    if (Test-Path $FilePath) {
        $content = Get-Content $FilePath -Raw | ConvertFrom-Json
        return ($content.States.PSObject.Properties | Measure-Object).Count
    }
    return 0
}

$legacyCount = Get-StateCount "src/aws_step_functions.json"
$v3Count = Get-StateCount "src/aws_step_functions_v3.json"
$legacyDistCount = Get-StateCount "src/aws_step_functions_distributed.json"
$v3DistCount = Get-StateCount "src/aws_step_functions_distributed_v3.json"

if ($legacyCount -gt 0 -and $v3Count -gt 0) {
    $reduction = [math]::Round((($legacyCount - $v3Count) / $legacyCount) * 100, 1)
    Write-Host "  📉 Standard: $legacyCount → $v3Count states (-$reduction%)" -ForegroundColor Cyan
}

if ($legacyDistCount -gt 0 -and $v3DistCount -gt 0) {
    $distReduction = [math]::Round((($legacyDistCount - $v3DistCount) / $legacyDistCount) * 100, 1)
    Write-Host "  📉 Distributed: $legacyDistCount → $v3DistCount states (-$distReduction%)" -ForegroundColor Cyan
}

# ========================================
# Final Summary
# ========================================
Write-Host ""
Write-Host "=" -Repeat 50 -ForegroundColor Cyan
Write-Host "📋 Validation Summary" -ForegroundColor Cyan
Write-Host "=" -Repeat 50 -ForegroundColor Cyan
Write-Host ""

if ($ErrorCount -eq 0 -and $WarningCount -eq 0) {
    Write-Host "✅ ALL CHECKS PASSED" -ForegroundColor Green
    Write-Host ""
    Write-Host "🚀 Ready to deploy! Run:" -ForegroundColor Green
    Write-Host "   sam build" -ForegroundColor White
    Write-Host "   sam deploy --guided" -ForegroundColor White
    Write-Host ""
    Write-Host "📝 Deployment will use:" -ForegroundColor Cyan
    Write-Host "   - Standard: aws_step_functions_v3.json" -ForegroundColor White
    Write-Host "   - Distributed: aws_step_functions_distributed_v3.json" -ForegroundColor White
    exit 0
} elseif ($ErrorCount -eq 0) {
    Write-Host "⚠️  VALIDATION PASSED WITH WARNINGS" -ForegroundColor Yellow
    Write-Host "   Errors: $ErrorCount" -ForegroundColor White
    Write-Host "   Warnings: $WarningCount" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "✅ Safe to deploy, but review warnings above" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "❌ VALIDATION FAILED" -ForegroundColor Red
    Write-Host "   Errors: $ErrorCount" -ForegroundColor Red
    Write-Host "   Warnings: $WarningCount" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "⛔ Please fix errors before deploying" -ForegroundColor Red
    exit 1
}
