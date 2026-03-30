'use strict';

const { randomUUID } = require('crypto');

function printUsage() {
  console.log(`
Usage:
  node infra/scripts/run-dingtalk-card-streaming-demo.js [--key=value ...]

Required env (or CLI override):
  DINGTALK_ACCESS_TOKEN
  DINGTALK_ROBOT_CODE
  DINGTALK_CARD_TEMPLATE_ID
  (user id source: DINGTALK_USER_ID or DINGTALK_STREAM_EVENT_JSON or DINGTALK_STREAM_EVENT_FILE)

Optional:
  DINGTALK_OPENAPI_ENDPOINT (default: https://api.dingtalk.com)
  DINGTALK_STREAM_EVENT_JSON (raw DingTalk callback json string)
  DINGTALK_STREAM_EVENT_FILE (path to DingTalk callback json file)
  DINGTALK_CARD_CONTENT_KEY (default: content)
  CHUNK_SIZE (default: 20)
  INTERVAL_MS (default: 120)
  MOCK_TEXT (default: built-in printer demo markdown)
  LAST_MESSAGE (default: 打印机 AI 卡片演示)
  SEARCH_ICON (default: https://gw.alicdn.com/imgextra/i4/O1CN01h9vUuR1d7w3QW9VYQ_!!6000000003695-2-tps-120-120.png)
  SEARCH_DESC (default: 打印机故障处理、耗材更换、连接排查)
  OUT_TRACK_ID (default: auto-generated)
  SIMULATE_ERROR_AT (default: 0, 1-based index, for failure path test)
`);
}

function parseCliArgs(argv) {
  const output = {};
  for (const raw of argv) {
    if (raw === '--help' || raw === '-h') {
      output.help = true;
      continue;
    }
    if (!raw.startsWith('--')) continue;
    const [name, ...rest] = raw.slice(2).split('=');
    output[name] = rest.join('=');
  }
  return output;
}

function readConfig(cli) {
  const pick = (key, fallback = '') => {
    if (typeof cli[key] === 'string' && cli[key].trim()) return cli[key].trim();
    if (typeof process.env[key] === 'string' && process.env[key].trim()) return process.env[key].trim();
    return fallback;
  };

  const parsePositiveInt = (value, fallback) => {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
    return parsed;
  };

  const parseNonNegativeInt = (value, fallback) => {
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed) || parsed < 0) return fallback;
    return parsed;
  };

  const defaultMockText = [
    '# 打印机 AI 助手',
    '',
    '已收到你的问题，先给你一个快速排查清单：',
    '',
    '1. 确认打印机电源与网络状态。',
    '2. 检查是否存在卡纸并按路径清理。',
    '3. 查看硒鼓/定影器寿命是否到期。',
    '4. 在电脑侧重新添加打印机并重试。',
    '',
    '> 若仍失败，请提供报错截图与打印机型号，我会继续分析。',
  ].join('\n');

  const config = {
    accessToken: pick('DINGTALK_ACCESS_TOKEN'),
    robotCode: pick('DINGTALK_ROBOT_CODE'),
    cardTemplateId: pick('DINGTALK_CARD_TEMPLATE_ID'),
    userId: pick('DINGTALK_USER_ID'),
    streamEventJson: pick('DINGTALK_STREAM_EVENT_JSON'),
    streamEventFile: pick('DINGTALK_STREAM_EVENT_FILE'),
    openApiEndpoint: pick('DINGTALK_OPENAPI_ENDPOINT', 'https://api.dingtalk.com').replace(/\/$/, ''),
    contentKey: pick('DINGTALK_CARD_CONTENT_KEY', 'content'),
    chunkSize: parsePositiveInt(pick('CHUNK_SIZE', '20'), 20),
    intervalMs: parseNonNegativeInt(pick('INTERVAL_MS', '120'), 120),
    mockText: pick('MOCK_TEXT', defaultMockText),
    lastMessage: pick('LAST_MESSAGE', '打印机 AI 卡片演示'),
    searchIcon: pick(
      'SEARCH_ICON',
      'https://gw.alicdn.com/imgextra/i4/O1CN01h9vUuR1d7w3QW9VYQ_!!6000000003695-2-tps-120-120.png'
    ),
    searchDesc: pick('SEARCH_DESC', '打印机故障处理、耗材更换、连接排查'),
    outTrackId: pick('OUT_TRACK_ID', `printer-card-${Date.now()}-${randomUUID().slice(0, 8)}`),
    simulateErrorAt: parseNonNegativeInt(pick('SIMULATE_ERROR_AT', '0'), 0),
  };

  const missing = [];
  if (!config.accessToken) missing.push('DINGTALK_ACCESS_TOKEN');
  if (!config.robotCode) missing.push('DINGTALK_ROBOT_CODE');
  if (!config.cardTemplateId) missing.push('DINGTALK_CARD_TEMPLATE_ID');
  if (missing.length > 0) {
    throw new Error(`Missing required env: ${missing.join(', ')}`);
  }
  config.userId = resolveUserId(config);
  return config;
}

function pickString(payload, keys) {
  for (const key of keys) {
    const value = payload?.[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return '';
}

function extractEventMapping(rawPayload) {
  if (!rawPayload || typeof rawPayload !== 'object') {
    return {};
  }
  if (rawPayload.data && typeof rawPayload.data === 'object') {
    return rawPayload.data;
  }
  return rawPayload;
}

function extractSenderIdFromEvent(rawPayload) {
  const event = extractEventMapping(rawPayload);
  return (
    pickString(event, ['senderStaffId', 'sender_staff_id', 'staffId', 'userid']) ||
    pickString(event, ['senderId', 'sender_id', 'userId', 'user_id'])
  );
}

function tryParseJson(input) {
  if (typeof input !== 'string' || !input.trim()) return null;
  try {
    return JSON.parse(input);
  } catch (_) {
    return null;
  }
}

function loadEventPayloadFromFile(pathValue) {
  if (!pathValue) return null;
  let fs;
  try {
    fs = require('fs');
  } catch (_) {
    return null;
  }
  try {
    const raw = fs.readFileSync(pathValue, 'utf8');
    return tryParseJson(raw);
  } catch (_) {
    return null;
  }
}

function resolveUserId(config) {
  if (config.userId) return config.userId;

  const jsonPayload = tryParseJson(config.streamEventJson);
  const fromJson = extractSenderIdFromEvent(jsonPayload);
  if (fromJson) return fromJson;

  const filePayload = loadEventPayloadFromFile(config.streamEventFile);
  const fromFile = extractSenderIdFromEvent(filePayload);
  if (fromFile) return fromFile;

  throw new Error(
    'Missing target user id. Provide DINGTALK_USER_ID or DINGTALK_STREAM_EVENT_JSON / DINGTALK_STREAM_EVENT_FILE with senderStaffId/senderId/userid.'
  );
}

function splitByChars(text, chunkSize) {
  const chars = Array.from(text || '');
  const chunks = [];
  for (let start = 0; start < chars.length; start += chunkSize) {
    chunks.push(chars.slice(start, start + chunkSize).join(''));
  }
  return chunks.length > 0 ? chunks : [''];
}

function convertJSONValuesToString(obj) {
  const result = {};
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'string') {
      result[key] = value;
      continue;
    }
    try {
      result[key] = JSON.stringify(value);
    } catch (_) {
      result[key] = '';
    }
  }
  return result;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function ensureFetch() {
  if (typeof fetch === 'function') return fetch;
  throw new Error('Global fetch is not available. Please use Node.js 18+.');
}

function createCardOpenApiClient() {
  let dingtalkCard;
  let OpenApi;
  let Util;
  try {
    dingtalkCard = require('@alicloud/dingtalk/card_1_0');
    OpenApi = require('@alicloud/openapi-client');
    Util = require('@alicloud/tea-util');
  } catch (err) {
    throw new Error(
      'Missing dependencies. Run: npm i @alicloud/dingtalk @alicloud/openapi-client @alicloud/tea-util @alicloud/tea-typescript'
    );
  }

  const config = new OpenApi.Config({});
  config.protocol = 'https';
  config.regionId = 'central';
  return {
    client: new dingtalkCard.default(config),
    classes: dingtalkCard,
    runtimeOptions: new Util.RuntimeOptions({}),
  };
}

async function createAndDeliverCard(config) {
  const { client, classes, runtimeOptions } = createCardOpenApiClient();

  const headers = new classes.CreateAndDeliverHeaders({});
  headers.xAcsDingtalkAccessToken = config.accessToken;

  const imRobotOpenDeliverModel = new classes.CreateAndDeliverRequestImRobotOpenDeliverModel({
    spaceType: 'IM_ROBOT',
    robotCode: config.robotCode,
  });

  const imRobotOpenSpaceModel = new classes.CreateAndDeliverRequestImRobotOpenSpaceModel({
    supportForward: true,
    lastMessageI18n: { ZH_CN: config.lastMessage },
    searchSupport: new classes.CreateAndDeliverRequestImRobotOpenSpaceModelSearchSupport({
      searchIcon: config.searchIcon,
      searchDesc: config.searchDesc,
    }),
  });

  const cardData = new classes.CreateAndDeliverRequestCardData({
    cardParamMap: convertJSONValuesToString({ [config.contentKey]: '' }),
  });

  const request = new classes.CreateAndDeliverRequest({
    userId: config.userId,
    cardTemplateId: config.cardTemplateId,
    outTrackId: config.outTrackId,
    callbackType: 'STREAM',
    cardData,
    imRobotOpenSpaceModel,
    imRobotOpenDeliverModel,
    openSpaceId: `dtv1.card//im_robot.${config.userId}`,
    userIdType: 1,
  });

  const response = await client.createAndDeliverWithOptions(request, headers, runtimeOptions);
  return { outTrackId: config.outTrackId, response };
}

async function streamingUpdate(config, payload) {
  const fetchImpl = ensureFetch();
  const body = {
    outTrackId: payload.outTrackId,
    guid: randomUUID(),
    key: payload.key,
    content: payload.content,
    isFull: true,
    isFinalize: Boolean(payload.isFinalize),
    isError: Boolean(payload.isError),
  };

  const resp = await fetchImpl(`${config.openApiEndpoint}/v1.0/card/streaming`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      Accept: '*/*',
      'x-acs-dingtalk-access-token': config.accessToken,
    },
    body: JSON.stringify(body),
  });

  const text = await resp.text();
  if (!resp.ok) {
    throw new Error(`streaming update failed: status=${resp.status}, body=${text}`);
  }
  return text;
}

async function sendErrorFinalize(config, context) {
  return streamingUpdate(config, {
    outTrackId: context.outTrackId,
    key: config.contentKey,
    content: context.content || '',
    isFinalize: false,
    isError: true,
  });
}

async function runStreamingDemo(config) {
  const startedAt = Date.now();
  let delivered = false;
  let currentContent = '';
  const chunks = splitByChars(config.mockText, config.chunkSize);

  console.log(`[demo] creating card, outTrackId=${config.outTrackId}`);
  await createAndDeliverCard(config);
  delivered = true;
  console.log(`[demo] card delivered, chunks=${chunks.length}, chunkSize=${config.chunkSize}`);

  try {
    for (let index = 0; index < chunks.length; index += 1) {
      if (config.simulateErrorAt > 0 && index + 1 === config.simulateErrorAt) {
        throw new Error(`simulated stream failure at chunk #${config.simulateErrorAt}`);
      }

      currentContent += chunks[index];
      await streamingUpdate(config, {
        outTrackId: config.outTrackId,
        key: config.contentKey,
        content: currentContent,
        isFinalize: false,
        isError: false,
      });
      console.log(`[demo] streamed chunk ${index + 1}/${chunks.length}, currentChars=${Array.from(currentContent).length}`);
      if (index < chunks.length - 1 && config.intervalMs > 0) {
        await sleep(config.intervalMs);
      }
    }

    await streamingUpdate(config, {
      outTrackId: config.outTrackId,
      key: config.contentKey,
      content: currentContent,
      isFinalize: true,
      isError: false,
    });
    console.log('[demo] finalize sent: success');
  } catch (err) {
    console.error(`[demo] streaming error: ${err.message}`);
    if (delivered) {
      try {
        await sendErrorFinalize(config, { outTrackId: config.outTrackId, content: currentContent });
        console.log('[demo] finalize sent: error');
      } catch (finalizeErr) {
        console.error(`[demo] error-finalize failed: ${finalizeErr.message}`);
      }
    }
    throw err;
  } finally {
    const elapsed = Date.now() - startedAt;
    console.log(`[demo] cleanup done, elapsedMs=${elapsed}`);
  }
}

async function main() {
  const cli = parseCliArgs(process.argv.slice(2));
  if (cli.help) {
    printUsage();
    process.exit(0);
  }

  const config = readConfig(cli);
  await runStreamingDemo(config);
}

main().catch((err) => {
  console.error(`[fatal] ${err.message}`);
  process.exit(1);
});
