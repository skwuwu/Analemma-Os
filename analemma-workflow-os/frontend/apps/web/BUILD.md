# 빌드 가이드

## 문제 상황
Windows 환경에서 `npm run build` 실행 시 Rollup이 d3 라이브러리의 순환 참조를 처리하지 못해 스택 오버플로우 발생 (error code 3221226505)

## 성공하는 빌드 방법

### 방법 1: GitHub Actions 사용 (권장)
코드를 push하면 자동으로 Linux 환경에서 빌드됩니다.

```bash
git add .
git commit -m "feat: update frontend"
git push origin main
```

GitHub Actions에서 자동으로 빌드 및 배포가 진행됩니다.

### 방법 2: WSL2 Ubuntu 사용
Windows Subsystem for Linux에서 빌드:

```bash
# WSL Ubuntu 설치 (PowerShell 관리자 권한)
wsl --install -d Ubuntu

# WSL에서 빌드
wsl
cd /mnt/c/Users/gimgy/OneDrive/바탕\ 화면/Analemma-Os/analemma-workflow-os/frontend/apps/web
npm install
npm run build
```

### 방법 3: 개발 서버 사용
로컬 개발 시에는 빌드 없이 dev 서버 사용:

```bash
npm run dev
```

개발 서버는 정상 작동합니다 (http://localhost:8080)

## 기술적 원인
- d3-selection, d3-transition, d3-interpolate, @uiw/react-json-view에 내부 순환 참조 존재
- Rollup(Vite의 production bundler)이 Windows에서 순환 참조 처리 시 무한 재귀 발생
- Linux/macOS에서는 동일한 코드가 정상 빌드됨 (GitHub Actions 성공)
