# Yeongcheon Backend API Spec

Base URL:

| Environment | Base URL |
|---|---|
| Local | `http://127.0.0.1:12303` |
| Production target | `https://yc.jun0.dev` |

## Common

| Item | Value |
|---|---|
| JSON encoding | UTF-8 |
| Upload encoding | `multipart/form-data` |
| Time format for robot form fields | ISO 8601, `YYYY-MM-DDTHH:MM:SS` |
| Robot coordinate frame | ROS odom |
| Coordinate unit | meter |
| Yaw unit | radian |

## Endpoint Summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/api/robots/{robotId}/status` | Robot status upload |
| `POST` | `/api/robots/{robotId}/image` | Robot camera image upload |
| `POST` | `/api/robots/{robotId}/lidar` | Robot lidar file upload |
| `POST` | `/api/robots/{robotId}/missing-person` | Missing person detection upload |
| `POST` | `/api/graph` | Road graph registration |
| `GET` | `/api/graph` | Road graph lookup |
| `GET` | `/api/dashboard` | Full dashboard lookup |
| `GET` | `/api/dashboard/robots/{robotId}` | Single robot dashboard lookup |
| `GET` | `/api/maintenance` | Maintenance priority lookup |
| `POST` | `/api/maintenance/analyze` | Maintenance analysis job request |
| `POST` | `/api/agent/maintenance-results` | Maintenance analysis result callback |
| `POST` | `/api/agent/anomaly-results` | Anomaly result callback |
| `POST` | `/api/agent/reconstruction-results` | Reconstruction result callback |
| `GET` | `/api/events` | Event list lookup |
| `POST` | `/api/events/{eventId}/resolve` | Event resolution |
| `GET` | `/api/missing/amber` | Safe182 missing person list proxy |
| `POST` | `/agent/maintenance/analyze` | Mock agent maintenance request receiver |
| `POST` | `/agent/anomaly/analyze-image` | Mock agent anomaly request receiver |
| `POST` | `/agent/reconstruction/recommend` | Mock agent reconstruction request receiver |
| `WS` | `/ws/robot` | Robot realtime stream |
| `WS` | `/ws/dashboard` | Dashboard realtime subscription |

## `GET /health`

Response:

| Field | Type | Description |
|---|---|---|
| `ok` | boolean | Server availability |
| `service` | string | Service name |

## `POST /api/robots/{robotId}/status`

Content-Type: `application/json`

Path params:

| Field | Type | Required | Description |
|---|---:|---|---|
| `robotId` | string | yes | Robot identifier |

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `status` | string | yes | Robot status |
| `battery` | number | yes | Battery percentage |
| `x` | number | yes | Robot x coordinate |
| `y` | number | yes | Robot y coordinate |
| `address` | string | yes | Current address or label |
| `nextDestination` | string | yes | Next destination |
| `velocity` | number | yes | Robot velocity |
| `timestamp` | string | yes | Measurement time |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `robotId` | string | Robot identifier |

## `POST /api/robots/{robotId}/image`

Content-Type: `multipart/form-data`

Path params:

| Field | Type | Required | Description |
|---|---:|---|---|
| `robotId` | string | yes | Robot identifier |

Form fields:

| Field | Type | Required | Description |
|---|---:|---|---|
| `image` | file | yes | JPEG image |
| `x` | number string | yes | Capture x coordinate |
| `y` | number string | yes | Capture y coordinate |
| `timestamp` | string | yes | Capture time |
| `address` | string | no | Optional address or label |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `robotId` | string | Robot identifier |
| `imageId` | integer | Saved image ID |
| `imageUrl` | string | Stored image URL |
| `analysisJobId` | string | Anomaly analysis job ID |

## `POST /api/robots/{robotId}/lidar`

Content-Type: `multipart/form-data`

Form fields:

| Field | Type | Required | Description |
|---|---:|---|---|
| `lidarFile` | file | yes | `.npy` or `.npz` lidar data |
| `x` | number string | yes | Measurement x coordinate |
| `y` | number string | yes | Measurement y coordinate |
| `timestamp` | string | yes | Measurement time |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `robotId` | string | Robot identifier |
| `lidarId` | integer | Saved lidar ID |
| `lidarFileUrl` | string | Original lidar file URL |
| `previewImageUrl` | string | Generated preview image URL |
| `summary` | object | Lidar quantitative summary |

`summary` fields:

| Field | Type |
|---|---|
| `pointCount` | integer |
| `minDistance` | number |
| `maxDistance` | number |
| `avgDistance` | number |
| `obstacleDetected` | boolean |
| `obstacleCount` | integer |
| `frontBlocked` | boolean |
| `leftBlocked` | boolean |
| `rightBlocked` | boolean |

## `POST /api/robots/{robotId}/missing-person`

Content-Type: `multipart/form-data`

Form fields:

| Field | Type | Required | Description |
|---|---:|---|---|
| `image` | file | yes | Detection evidence JPEG |
| `missing_person_id` | string | yes | YOLO class label or missing person ID |
| `x` | number string | yes | Detection x coordinate |
| `y` | number string | yes | Detection y coordinate |
| `timestamp` | string | yes | Detection time |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `robotId` | string | Robot identifier |
| `detectionId` | string | Detection ID |
| `missingPersonId` | string | Missing person ID |
| `imageUrl` | string | Stored evidence image URL |
| `eventId` | string | Created event ID |

## `POST /api/graph`

Content-Type: `application/json`

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `type` | string | yes | Must be `graph` |
| `nodes` | object | yes | Node map. Key is node ID, value is `[x, y]` |
| `edges` | object | yes | Adjacency map. Key is node ID, value is node ID array |
| `houses` | object | yes | House map. Each value contains `pos` and `yaw` |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `nodeCount` | integer | Number of nodes |
| `edgeSourceCount` | integer | Number of edge source nodes |
| `houseCount` | integer | Number of houses |

## `GET /api/graph`

Response:

| Field | Type | Description |
|---|---|---|
| `type` | string | `graph` |
| `nodes` | object | Node map |
| `edges` | object | Adjacency map |
| `houses` | object | House map |

## `GET /api/dashboard`

Query params:

| Field | Type | Required | Description |
|---|---:|---|---|
| `selectedRobotId` | string | no | Robot ID to select by default |

Response:

| Field | Type | Description |
|---|---|---|
| `weather` | object | Simple weather object |
| `robots` | array | Robot dashboard entries |
| `selectedRobotId` | string or null | Selected robot ID |
| `vacantHouseMap` | array | Vacant house map entries |
| `maintenancePriorities` | array | Maintenance recommendations |
| `reconstruction` | array | Reconstruction results |
| `urgentEvent` | object or null | Highest priority unresolved event |
| `stats` | object | Dashboard counts |

Robot entry fields include `robotId`, `status`, `battery`, `x`, `y`, `address`, `nextDestination`, `velocity`, `timestamp`, `latestImage`, `latestLidar`, `patrolPath`, and `updatedAt`.

## `GET /api/dashboard/robots/{robotId}`

Response: one robot dashboard entry.

## `GET /api/maintenance`

Response: array of maintenance recommendation entries.

Recommendation fields may include `rank`, `houseId`, `address`, `riskLevel`, `agingRate`, `accessibility`, `score`, `recommendedUse`, `reason`, `analysisId`, `jobId`, and `createdAt`.

## `POST /api/maintenance/analyze`

Content-Type: `application/json`

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `area` | string | yes | Analysis target area |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Request result |
| `jobId` | string | Created job ID |
| `message` | string | Human readable message |

## `POST /api/agent/maintenance-results`

Content-Type: `application/json`

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `jobId` | string | yes | Job ID |
| `analysisId` | string | yes | Analysis ID |
| `recommendations` | array | yes | Recommendation list |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `analysisId` | string | Analysis ID |
| `savedCount` | integer | Saved recommendation count |

## `POST /api/agent/anomaly-results`

Content-Type: `application/json`

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `jobId` | string | yes | Job ID |
| `analysisId` | string | yes | Analysis ID |
| `robotId` | string | yes | Robot ID |
| `imageId` | integer | yes | Image ID |
| `eventType` | string | yes | Event type |
| `severity` | string | yes | Event severity |
| `confidence` | number | yes | Detection confidence |
| `summary` | string | yes | Event summary |
| `detectedObjects` | array | yes | Detected object labels |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `eventId` | string | Created event ID |

## `POST /api/agent/reconstruction-results`

Content-Type: `application/json`

Request body:

| Field | Type | Required |
|---|---:|---|
| `jobId` | string | yes |
| `houseId` | string | yes |
| `recommendedUse` | string | yes |
| `buildingScale` | string | yes |
| `style` | string | yes |
| `estimatedCost` | number | yes |
| `expectedReturn` | string | yes |
| `feasibility` | string | yes |
| `reason` | string | yes |
| `images` | array | yes |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `houseId` | string | House ID |

## `GET /api/events`

Query params:

| Field | Type | Required | Description |
|---|---:|---|---|
| `robotId` | string | no | Filter by robot ID |
| `eventType` | string | no | Filter by event type |
| `severity` | string | no | Filter by severity |
| `resolved` | boolean string | no | `true` or `false` |
| `limit` | integer | no | Maximum rows |

Response: array of event entries.

## `POST /api/events/{eventId}/resolve`

Content-Type: `application/json`

Request body:

| Field | Type | Required | Description |
|---|---:|---|---|
| `memo` | string | yes | Resolution memo |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Save result |
| `eventId` | string | Event ID |
| `resolved` | boolean | Resolution state |

## `GET /api/missing/amber`

Client request body: none.

Server-side Safe182 form values:

| Field | Value |
|---|---|
| `authKey` | server-defined |
| `rowSize` | `5` |
| `esntlId` | server-defined |

Response:

| Field | Type | Description |
|---|---|---|
| `success` | boolean | Safe182 result success |
| `result` | string | Safe182 result code |
| `message` | string | Safe182 message |
| `totalCount` | integer | Total source count |
| `count` | integer | Returned item count |
| `items` | array | Normalized missing person items |

Item fields:

| Field | Type |
|---|---|
| `rowNumber` | integer |
| `missingPersonId` | integer |
| `name` | string |
| `gender` | string |
| `nationality` | string |
| `missingDate` | string |
| `missingAddress` | string |
| `ageNow` | string |
| `ageAtMissing` | integer |
| `heightCm` | number |
| `weightKg` | number |
| `bodyType` | string |
| `faceShape` | string |
| `hairShape` | string |
| `hairColor` | string |
| `clothing` | string or null |
| `targetCode` | string |
| `photo` | object or null |

`photo` contains `mimeType`, `byteLength`, and `dataUrl`.

## Mock Agent Request Receivers

These endpoints accept agent-style job requests and return an accepted result for local integration testing.

| Method | Path | Required body fields | Response |
|---|---|---|---|
| `POST` | `/agent/maintenance/analyze` | `jobId`, `callbackUrl`, `area`, `vacantHouses`, `populationData`, `recentEvents` | `accepted`, `jobId` |
| `POST` | `/agent/anomaly/analyze-image` | `jobId`, `callbackUrl`, `robotId`, `imageId`, `imageUrl`, `x`, `y`, `address`, `timestamp` | `accepted`, `jobId` |
| `POST` | `/agent/reconstruction/recommend` | `jobId`, `callbackUrl`, `houseId`, `address`, `riskLevel`, `agingRate`, `accessibility`, `populationContext`, `beforeImageUrl` | `accepted`, `jobId` |

## WebSocket `/ws/robot`

Direction: robot to backend.

Message format: JSON text frame.

Accepted message types:

| Type | Required fields | Description |
|---|---|---|
| `graph` | `type`, `nodes`, `edges`, `houses` | Road graph snapshot |
| `plan` | `type`, `mission_id`, `start`, `house_order`, `waypoints`, `arrival_indices`, `arrival_yaws` | Mission plan |
| `pos` | `type`, `mission_id`, `x`, `y`, `yaw`, `t` | Realtime position |
| `status` | `type`, `mission_id`, `phase`, `t` | Mission status transition |
| `video_frame` | `type`, `data` | Optional JPEG data URL frame |
| `arrival_ack` | `type`, `house_id`, `ok`, `photo_url`, `t` | Optional photo upload acknowledgement |

`graph`, `plan`, `pos`, and `status` are cached as latest realtime state. All received messages are broadcast to dashboard clients.

## WebSocket `/ws/dashboard`

Direction: backend to dashboard.

On connection, backend sends the latest cached messages if present:

| Order | Type |
|---|---|
| 1 | `graph` |
| 2 | `plan` |
| 3 | `status` |
| 4 | `pos` |

After the initial snapshot, every message received from `/ws/robot` is broadcast to connected dashboard clients.
