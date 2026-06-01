# 정비 우선순위 결과 전달

Category: Agent → Backend
Content-Type: application/json
Endpoint: /api/agent/maintenance-results
Main Fields: jobId, analysisId, recommendations
Main Response: success, analysisId, savedCount
Method: POST
Purpose: 정비 우선순위 분석 결과 저장

## Request

```json
{
  "jobId": "MA-JOB-0001",
  "analysisId": "MA-0001",
  "recommendations": [
    {
      "rank": 1,
      "houseId": "VH-001",
      "address": "완산동 123-4",
      "priorityScore": 91,
      "riskLevel": "DANGER",
      "reason": "노후도와 접근성이 높고 최근 이상 탐지 이력이 있어 우선 정비가 필요합니다.",
      "recommendedUse": "주거 + 상업 복합"
    }
  ]
}
```

## Response

```json
{
  "success": true,
  "analysisId": "MA-0001",
  "savedCount": 1
}
```

## Backend 처리

- `maintenance_recommendation` 저장
- 우선순위 1위 빈집에 대해 재건축 추천 요청 가능