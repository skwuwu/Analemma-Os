# Python 환경 설정 스크립트
# 관리자 권한으로 실행 필요

Write-Host "Python 환경 설정을 시작합니다..." -ForegroundColor Green

# 현재 사용자의 Python 경로 확인
$pythonPath = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312"
$scriptsPath = "$pythonPath\Scripts"

Write-Host "Python 경로: $pythonPath" -ForegroundColor Yellow

# Python 설치 확인
if (Test-Path "$pythonPath\python.exe") {
    Write-Host "✅ Python 3.12가 설치되어 있습니다." -ForegroundColor Green
    
    # 버전 확인
    $version = & "$pythonPath\python.exe" --version
    Write-Host "설치된 버전: $version" -ForegroundColor Cyan
}
else {
    Write-Host "❌ Python이 설치되지 않았습니다. winget으로 설치합니다..." -ForegroundColor Red
    winget install Python.Python.3.12
}

# 현재 PATH 확인
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")

# Python 경로가 PATH에 있는지 확인
if ($currentPath -notlike "*$pythonPath*") {
    Write-Host "Python 경로를 사용자 PATH에 추가합니다..." -ForegroundColor Yellow
    
    # 새로운 PATH 구성
    $newPath = "$pythonPath;$scriptsPath;$currentPath"
    
    # 사용자 환경 변수에 PATH 설정
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    
    Write-Host "✅ PATH에 Python 경로가 추가되었습니다." -ForegroundColor Green
}
else {
    Write-Host "✅ Python 경로가 이미 PATH에 있습니다." -ForegroundColor Green
}

# 현재 세션에서 PATH 업데이트
$env:PATH = "$pythonPath;$scriptsPath;$env:PATH"

Write-Host "`n필수 패키지 설치 중..." -ForegroundColor Yellow

# 필수 패키지 설치
$packages = @("boto3", "botocore")

foreach ($package in $packages) {
    Write-Host "설치 중: $package" -ForegroundColor Cyan
    & python -m pip install $package --quiet
}

Write-Host "`n✅ Python 환경 설정이 완료되었습니다!" -ForegroundColor Green
Write-Host "`n테스트 실행:" -ForegroundColor Yellow
Write-Host "cd backend/scripts" -ForegroundColor White
Write-Host "python test_correction_quality_fixes.py" -ForegroundColor White

Write-Host "`n⚠️  새 터미널을 열어야 PATH 변경사항이 적용됩니다." -ForegroundColor Yellow