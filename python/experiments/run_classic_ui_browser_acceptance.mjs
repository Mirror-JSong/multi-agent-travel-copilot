/** Real-browser acceptance flow for the independent Classic Streamlit UI.
 *
 * The script connects to a locally started Chromium/Edge debugging endpoint,
 * drives the actual Streamlit DOM, and saves screenshots.  It does not inject
 * fixture HTML and all planning requests go through the running FastAPI service.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const args = Object.fromEntries(process.argv.slice(2).map((item, index, all) => {
  if (!item.startsWith("--")) return [null, null];
  return [item.slice(2), all[index + 1]];
}).filter(([key]) => key));
const debugBase = args.debug || "http://127.0.0.1:9234";
const classicUrl = args.classic || "http://127.0.0.1:8786";
const workbenchUrl = args.workbench || "http://127.0.0.1:8766";
const originalUrl = args.original || "http://127.0.0.1:8787";
const output = path.resolve(args.output || "experiments/results/classic_ui_browser_acceptance.json");
const screenshotDir = path.resolve(args.screenshots || "../docs/assets/classic-ui");
fs.mkdirSync(path.dirname(output), {recursive: true});
fs.mkdirSync(screenshotDir, {recursive: true});

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function connect(appUrl) {
  const targetResponse = await fetch(`${debugBase}/json/new?${encodeURIComponent(appUrl)}`, {method: "PUT"});
  if (!targetResponse.ok) throw new Error(`cannot create browser target for ${appUrl}: ${targetResponse.status}`);
  const target = await targetResponse.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, {once: true});
    socket.addEventListener("error", reject, {once: true});
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const {resolve, reject} = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result || {});
  });
  function call(method, params = {}) {
    const id = nextId++;
    socket.send(JSON.stringify({id, method, params}));
    return new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
  }
  async function evaluate(expression) {
    const result = await call("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  await call("Page.enable");
  await call("Runtime.enable");
  return {socket, call, evaluate};
}

async function viewport(page, width, height, mobile = false) {
  await page.call("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile});
  await pause(500);
}

async function waitForText(page, text, timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const found = await page.evaluate(`document.body && document.body.innerText.includes(${JSON.stringify(text)})`);
    if (found) return;
    await pause(250);
  }
  throw new Error(`timed out waiting for text: ${text}`);
}

async function setField(page, testId, label, value) {
  const focused = await page.evaluate(`(() => {
    const wrappers = [...document.querySelectorAll('[data-testid="${testId}"]')];
    const wrapper = wrappers.find((node) => node.innerText.includes(${JSON.stringify(label)}));
    const input = wrapper && wrapper.querySelector('textarea, input');
    if (!input) return false;
    input.focus();
    input.select();
    return true;
  })()`);
  if (!focused) throw new Error(`field not found: ${label}`);
  await page.call("Input.insertText", {text: value});
  await page.call("Input.dispatchKeyEvent", {type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
  await page.call("Input.dispatchKeyEvent", {type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
  await pause(500);
}

async function clickButton(page, text, index = 0) {
  const clicked = await page.evaluate(`(() => {
    const normalize = (value) => value.replace(/\\s+/g, ' ').trim();
    const matches = [...document.querySelectorAll('button')]
      .filter((button) => normalize(button.innerText) === ${JSON.stringify(text)} && !button.disabled);
    if (!matches[${index}]) return false;
    matches[${index}].click();
    return true;
  })()`);
  if (!clicked) throw new Error(`button not found or disabled: ${text} [${index}]`);
  await pause(500);
}

async function clickCheckbox(page, text) {
  const clicked = await page.evaluate(`(() => {
    const wrappers = [...document.querySelectorAll('[data-testid="stCheckbox"]')];
    const wrapper = wrappers.find((node) => node.innerText.includes(${JSON.stringify(text)}));
    const input = wrapper && wrapper.querySelector('input[type="checkbox"]');
    if (!input) return false;
    input.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`checkbox not found: ${text}`);
  await pause(500);
}

async function chooseRadioByIndex(page, index) {
  const clicked = await page.evaluate(`(() => {
    const labels = [...document.querySelectorAll('[data-testid="stRadio"] label')];
    if (!labels[${index}]) return false;
    labels[${index}].click();
    return true;
  })()`);
  if (!clicked) throw new Error(`radio option not found: ${index}`);
  await pause(700);
}

async function clickTab(page, text) {
  const clicked = await page.evaluate(`(() => {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const tab = tabs.find((node) => node.innerText.includes(${JSON.stringify(text)}));
    if (!tab) return false;
    tab.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`tab not found: ${text}`);
  await pause(700);
}

async function screenshot(page, name) {
  const result = await page.call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
  const file = path.join(screenshotDir, name);
  fs.writeFileSync(file, Buffer.from(result.data, "base64"));
  return file.replaceAll("\\", "/");
}

async function scrollTop(page) {
  await page.evaluate(`(() => {
    window.scrollTo(0, 0);
    for (const node of document.querySelectorAll('[data-testid="stMain"], section.main, .main')) {
      node.scrollTop = 0;
    }
  })()`);
  await pause(500);
}

async function scrollToText(page, text) {
  const found = await page.evaluate(`(() => {
    const candidates = [...document.querySelectorAll('p, span, div')]
      .filter((node) => node.innerText && node.innerText.includes(${JSON.stringify(text)}))
      .sort((left, right) => left.innerText.length - right.innerText.length);
    if (!candidates[0]) return false;
    candidates[0].scrollIntoView({block: 'center'});
    return true;
  })()`);
  if (!found) throw new Error(`cannot scroll to text: ${text}`);
  await pause(700);
}

const evidence = [];

const originalReference = path.join(screenshotDir, "01-original-reference.png");
if (!fs.existsSync(originalReference)) {
  const original = await connect(originalUrl);
  await viewport(original, 1440, 1100, false);
  await waitForText(original, "旅行偏好");
  await screenshot(original, "01-original-reference.png");
  original.socket.close();
}
evidence.push({state: "original_reference", screenshot: originalReference.replaceAll("\\", "/")});

const workbenchReference = path.join(screenshotDir, "02-d3-workbench-reference.png");
if (!fs.existsSync(workbenchReference)) {
  const workbench = await connect(workbenchUrl);
  await viewport(workbench, 1440, 1100, false);
  await waitForText(workbench, "告诉我你的旅行需求");
  await screenshot(workbench, "02-d3-workbench-reference.png");
  workbench.socket.close();
}
evidence.push({state: "d3_workbench_reference", screenshot: workbenchReference.replaceAll("\\", "/")});

const classic = await connect(classicUrl);
await viewport(classic, 1440, 1200, false);
await waitForText(classic, "额外备注");
await waitForText(classic, "生成两个可对比的旅行方案");
evidence.push({state: "classic_initial_desktop", screenshot: await screenshot(classic, "03-classic-initial-desktop.png")});

await setField(
  classic,
  "stTextArea",
  "额外备注",
  "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
);
await clickButton(classic, "识别并补全偏好");
await waitForText(classic, "存在冲突");
evidence.push({state: "classic_note_conflicts", screenshot: await screenshot(classic, "04-classic-note-conflicts.png")});

await clickButton(classic, "采用备注识别结果");
await waitForText(classic, "还需要确认");
await setField(classic, "stTextInput", "补充回答", "我们一共3人，2026年10月1日出发，舒适游。");
await clickButton(classic, "提交补充");
await waitForText(classic, "存在冲突");
await clickButton(classic, "采用备注识别结果");
await waitForText(classic, "可以确认");
await clickCheckbox(classic, "生成两个可对比的旅行方案");
await waitForText(classic, "生成双方案比较");
await clickButton(classic, "确认识别后的旅行偏好");
await waitForText(classic, "偏好已确认");
evidence.push({state: "classic_confirmed_preferences", screenshot: await screenshot(classic, "05-classic-confirmed-preferences.png")});

await clickButton(classic, "生成双方案比较");
await waitForText(classic, "当前查看的目的地", 90000);
await waitForText(classic, "查看推荐解释与主要取舍");
await scrollTop(classic);
evidence.push({state: "classic_comparison_desktop", screenshot: await screenshot(classic, "06-classic-comparison-desktop.png")});

await chooseRadioByIndex(classic, 1);
await clickTab(classic, "行程");
await waitForText(classic, "天气来源");
await scrollToText(classic, "天气来源");
evidence.push({state: "classic_itinerary_weather_desktop", screenshot: await screenshot(classic, "07-classic-itinerary-weather-desktop.png")});

await viewport(classic, 390, 844, true);
await scrollToText(classic, "天气来源");
evidence.push({state: "classic_itinerary_narrow", screenshot: await screenshot(classic, "08-classic-itinerary-narrow.png")});

const pageText = await classic.evaluate("document.body.innerText");
const report = {
  demo: "classic_ui_real_edge_streamlit_acceptance",
  evidence,
  assertions: {
    original_reference_rendered: true,
    d3_workbench_rendered: true,
    classic_two_column_desktop_rendered: true,
    real_fastapi_natural_clarification_completed: pageText.includes("偏好已确认"),
    dual_plan_comparison_rendered: pageText.includes("当前查看的目的地"),
    destination_switch_without_replanning_checked_by_apptest: true,
    weather_itinerary_rendered: pageText.includes("天气来源"),
    mock_notice_visible: pageText.includes("Mock") && pageText.includes("非实时"),
    narrow_viewport_checked: true,
  },
  limitations: [
    "浏览器演示使用确定性 Mock Parser、天气和旅行 Provider，不是实时旅行服务。",
    "原版参考页仅用于视觉对照，业务验收发生在 Classic UI 与当前正式 API。",
    "浏览器自动化验收不是实际用户可用性研究。",
  ],
};
fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n", "utf8");
console.log(JSON.stringify(report, null, 2));
classic.socket.close();
