# DingTalk AI Card Streaming Demo (Node.js)

This document describes how to run the local Node.js demo script for typewriter-like AI card streaming:

- script path: `infra/scripts/run-dingtalk-card-streaming-demo.js`
- flow: `createAndDeliver -> streaming update -> finalize(success/error)`

## Prerequisites

1. Node.js 18+ (global `fetch` required).
2. DingTalk app has `Card.Streaming.Write` permission.
3. The card template is published and includes an **AI streaming text** component bound to `content`.
4. Install dependencies:

```bash
npm i @alicloud/dingtalk @alicloud/openapi-client @alicloud/tea-util @alicloud/tea-typescript
```

## Required environment variables

- `DINGTALK_ACCESS_TOKEN`
- `DINGTALK_ROBOT_CODE`
- `DINGTALK_CARD_TEMPLATE_ID`

Optional:

- `DINGTALK_USER_ID` (manual target user id)
- `DINGTALK_STREAM_EVENT_JSON` (raw DingTalk callback payload JSON string)
- `DINGTALK_STREAM_EVENT_FILE` (path to callback payload JSON file)
- `DINGTALK_OPENAPI_ENDPOINT` (default `https://api.dingtalk.com`)
- `DINGTALK_CARD_CONTENT_KEY` (default `content`)
- `CHUNK_SIZE` (default `20`)
- `INTERVAL_MS` (default `120`)
- `MOCK_TEXT`
- `LAST_MESSAGE`
- `SEARCH_ICON`
- `SEARCH_DESC`
- `OUT_TRACK_ID`
- `SIMULATE_ERROR_AT` (default `0`, 1-based chunk index)

User id resolution order:
1. `DINGTALK_USER_ID`
2. parse `DINGTALK_STREAM_EVENT_JSON`
3. parse `DINGTALK_STREAM_EVENT_FILE`

Event payload fields (priority):
- `senderStaffId` / `sender_staff_id` / `staffId` / `userid`
- fallback: `senderId` / `sender_id` / `userId` / `user_id`

## Run commands

Show help:

```bash
node infra/scripts/run-dingtalk-card-streaming-demo.js --help
```

Run success path:

```bash
node infra/scripts/run-dingtalk-card-streaming-demo.js
```

Capture a real DingTalk callback payload (`event-*.json`) from Stream runtime:

```powershell
$env:DINGTALK_STREAM_EVENT_DUMP_DIR='.\tmp\dingtalk-events'
python .\infra\scripts\run-dingtalk-stream.py --env-file .env
```

Then send one message to the bot in DingTalk and pick latest event file:

```powershell
$event = Get-ChildItem .\tmp\dingtalk-events\event-*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$env:DINGTALK_STREAM_EVENT_FILE = $event.FullName
node .\infra\scripts\run-dingtalk-card-streaming-demo.js
```

Run with dynamic user from callback JSON file:

```bash
DINGTALK_STREAM_EVENT_FILE=./event.json node infra/scripts/run-dingtalk-card-streaming-demo.js
```

Run failure path (simulate error at chunk 3):

```bash
SIMULATE_ERROR_AT=3 node infra/scripts/run-dingtalk-card-streaming-demo.js
```

PowerShell equivalent:

```powershell
$env:SIMULATE_ERROR_AT='3'
node .\infra\scripts\run-dingtalk-card-streaming-demo.js
```

## Expected behavior

1. Card is delivered with the configured `outTrackId`.
2. `content` is streamed in batches (full-content updates each time).
3. Success: final update sets `isFinalize=true`.
4. Failure: script sends a dedicated error finalize update with `isError=true`.

## Troubleshooting

- `Missing required env`: set the required variables.
- `Global fetch is not available`: upgrade to Node.js 18+.
- `streaming update failed`: verify token, template id, and permission scope.
- Card not updating: confirm template variable key matches `DINGTALK_CARD_CONTENT_KEY`.
