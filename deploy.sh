#!/bin/bash
# Railway 전체 배포 스크립트

set -e

echo "🚀 Railway 전체 배포 시작..."

# 프로젝트 루트 경로
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Backend 배포
echo ""
echo "📦 [1/3] Backend 배포 중..."
cd "$ROOT_DIR/backend"
railway up --detach

# Frontend 배포
echo ""
echo "📦 [2/3] Frontend 배포 중..."
cd "$ROOT_DIR/frontend"
railway up --detach

# Cronjob 배포
echo ""
echo "📦 [3/3] Cronjob 배포 중..."
cd "$ROOT_DIR/cronjob"
railway up --detach

echo ""
echo "✅ 모든 서비스 배포 완료!"
echo "📊 Railway 대시보드에서 배포 상태를 확인하세요."
